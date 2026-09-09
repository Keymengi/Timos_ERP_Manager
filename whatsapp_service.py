"""
whatsapp_service.py — WhatsApp Notification Service for Timos ERP Manager
============================================================================

WHY AFRICA'S TALKING WHATSAPP (over building against Meta directly):
- You already have an Africa's Talking account and dashboard for SMS — this
  reuses the exact same login, the same AT_USERNAME/AT_API_KEY credentials,
  and the same billing relationship. No new provider to sign up with.
- AT handles the WhatsApp Business Account (WABA) setup and Meta paperwork
  on your behalf, and gives you one dashboard for both channels.

IMPORTANT — READ BEFORE YOU FLIP THIS ON:
WhatsApp is NOT free, and it does not work exactly like SMS. Two things are
different in a way that affects this app specifically:

1. COST: Meta (WhatsApp's owner) charges a small per-message fee for any
   message a business sends first (which is every reminder this app sends —
   the customer didn't message first). This is usually a lot cheaper than
   SMS in Kenya, but it is not zero, and Africa's Talking may add a small
   markup on top of Meta's fee. Check AT's WhatsApp pricing page in your
   dashboard before going live so there are no surprises.

2. TEMPLATES: WhatsApp does not let a business send free-form text out of
   nowhere. Any message YOU start (like these reminders) must use a
   message "template" — a fixed layout with blanks — that you submit to
   Africa's Talking/Meta for approval BEFORE you can send it. Approval
   usually takes anywhere from a few hours to 2 days. Until a template is
   approved, sends will fail (or, in sandbox, just print to your console).
   This is a WhatsApp platform rule, not something this app can route
   around.

SETUP:
1. Log in to your existing Africa's Talking dashboard (africastalking.com).
2. Go to the "Chat" / WhatsApp product and follow their prompts to enable
   WhatsApp for your app and register a WhatsApp-enabled sender number
   (this becomes your AT_WHATSAPP_NUMBER below, in +254... format).
3. In the WhatsApp > Templates section, create two templates — one for
   customer booking reminders, one for debt reminders — and submit them
   for approval. Keep the wording close to what's in this app's messages
   (see app.py) so approval is fast; WhatsApp is stricter about templates
   that look like spam/marketing than about plain transactional wording.
4. Once approved, note each template's exact name and the order of its
   variable placeholders — you'll need that when you swap the plain-text
   send below for a template send (see "GOING LIVE WITH TEMPLATES" below).
5. Set environment variables (same place you set AT_USERNAME/AT_API_KEY):

       AT_USERNAME=your_at_username        # same one used for SMS
       AT_API_KEY=your_api_key_here         # same one used for SMS
       AT_WHATSAPP_NUMBER=+2547XXXXXXXX     # your approved WhatsApp sender

DEV-SAFE BY DEFAULT:
If AT_WHATSAPP_NUMBER (or the username/key) isn't set, this module runs in
"console mode": messages are printed to the terminal instead of actually
sent, exactly like the old sms_service.py did. Nothing crashes just because
you haven't finished WhatsApp setup yet.

GOING LIVE WITH TEMPLATES:
The send_whatsapp() function below sends a plain-text message (AT's
"body.message" field). This works today in the sandbox/console, and it will
keep working live for any customer who has messaged your WhatsApp number in
the last 24 hours — but for a cold reminder Meta requires the approved
template instead. Once your templates are approved, open your AT dashboard
> Templates > (your template) > "Code sample" tab — it will show you the
exact JSON to send for THAT template. Swap the `body` dict inside the `if
LIVE_MODE:` block below for that JSON, keeping the phone-normalizing and
logging logic around it unchanged. Flagging this now rather than guessing
at the exact template JSON shape, since Africa's Talking can adjust template
payload fields and getting it wrong there just means a failed send you'd
have to debug — normalizing/logging is the part safe to lock in today.
"""

import os
import re
import logging
import requests

logger = logging.getLogger("timos_whatsapp")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

AT_USERNAME = os.environ.get("AT_USERNAME")
AT_API_KEY = os.environ.get("AT_API_KEY")
AT_WHATSAPP_NUMBER = os.environ.get("AT_WHATSAPP_NUMBER")  # your approved WA sender, e.g. +254711000111

SEND_URL = "https://chat.africastalking.com/whatsapp/message/send"

LIVE_MODE = bool(AT_USERNAME and AT_API_KEY and AT_WHATSAPP_NUMBER)

if LIVE_MODE:
    logger.info("WhatsApp service initialized in LIVE mode (Africa's Talking, sender=%s)", AT_WHATSAPP_NUMBER)
else:
    logger.info(
        "WhatsApp service running in CONSOLE mode (AT_USERNAME/AT_API_KEY/AT_WHATSAPP_NUMBER "
        "not fully set). Messages will be printed to the console instead of sent."
    )


def normalize_kenyan_number(raw):
    """
    Normalizes common Kenyan phone number formats to E.164 (+254XXXXXXXXX).
    Accepts: 0712345678, 712345678, 254712345678, +254712345678 (with or
    without spaces/dashes). Returns None if it doesn't look like a valid
    Kenyan mobile number.
    """
    if not raw:
        return None

    digits = re.sub(r"[^\d+]", "", raw.strip())

    if digits.startswith("+254") and len(digits) == 13:
        return digits
    if digits.startswith("254") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+254" + digits[1:]
    if len(digits) == 9 and digits[0] in "17":
        return "+254" + digits

    return None


def send_whatsapp(phone_raw, message):
    """
    Sends a WhatsApp message to a single recipient.

    Returns a 3-tuple: (success: bool, status_label: str, error_detail: str|None)
      - status_label is one of "Sent", "Console", or "Failed" — meant to be
        stored directly in SMSLog.status by the caller.
      - error_detail carries the reason for a "Failed" status (bad number,
        API error, template not approved yet, etc.) so it can be logged and
        shown in the Recent Notifications table instead of just "Failed".

    Never raises — safe to call from the background scheduler without
    risking the whole reminder job on a single bad number or API hiccup.
    """
    phone = normalize_kenyan_number(phone_raw)
    if not phone:
        detail = f"'{phone_raw}' is not a recognizable phone number"
        logger.warning("Skipped WhatsApp message — %s.", detail)
        return False, "Failed", detail

    if not LIVE_MODE:
        print(f"[WHATSAPP-CONSOLE] To: {phone} | Message: {message}")
        return True, "Console", None

    try:
        payload = {
            "username": AT_USERNAME,
            "waNumber": AT_WHATSAPP_NUMBER,
            "phoneNumber": phone,
            "body": {"message": message},
        }
        headers = {"apikey": AT_API_KEY, "content-type": "application/json"}
        response = requests.post(SEND_URL, json=payload, headers=headers, timeout=15)
        data = response.json() if response.content else {}

        if response.status_code in (200, 201) and str(data.get("status", "")).lower() in ("success", "queued", "sent", ""):
            logger.info("WhatsApp message sent to %s", phone)
            return True, "Sent", None
        else:
            detail = str(data) or f"HTTP {response.status_code}"
            logger.error("WhatsApp send to %s failed: %s", phone, detail)
            return False, "Failed", detail[:255]
    except Exception as e:
        logger.error("WhatsApp send error to %s: %s", phone, e)
        return False, "Failed", str(e)[:255]
