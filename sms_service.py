"""
sms_service.py — SMS Notification Service for Timos ERP Manager
==================================================================

WHY AFRICA'S TALKING (over Twilio):
- Built for the African market, with direct local routes to Safaricom, Airtel
  and Telkom Kenya — better delivery rates and lower latency than Twilio's
  international routes into Kenya.
- Billed in KSh, no international currency conversion.
- Cheaper per-SMS in Kenya than Twilio (roughly KSh 0.80 - 1.00 vs Twilio's
  ~KSh 3-5 equivalent per SMS to Kenyan numbers).
- Free sandbox environment for development/testing that behaves exactly like
  production, so you can build and test before paying for anything.
- Simple Python SDK (`pip install africastalking`).

SETUP:
1. Create an account at https://africastalking.com
2. In the dashboard, use the "Sandbox" app to start (free, no KYC needed).
   Once ready to go live, create a real app and complete KYC/Sender ID
   registration (required by Kenyan telecoms regulation to send from a
   custom Sender ID like "TIMOS" instead of a shared shortcode).
3. Grab your Username and API Key from the dashboard.
4. Set environment variables before starting the app:

       AT_USERNAME=sandbox            # or your live app's username
       AT_API_KEY=your_api_key_here
       AT_SENDER_ID=TIMOS             # optional — omit until your Sender ID is approved

5. Install the SDK:
       pip install africastalking

DEV-SAFE BY DEFAULT:
If AT_USERNAME / AT_API_KEY are not set, this module runs in "console mode":
messages are printed to the terminal instead of actually sent. This means the
app never crashes or accidentally fires real texts just because you're
running it locally without credentials configured.
"""

import os
import re
import logging

logger = logging.getLogger("timos_sms")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

AT_USERNAME = os.environ.get("AT_USERNAME")
AT_API_KEY = os.environ.get("AT_API_KEY")
AT_SENDER_ID = os.environ.get("AT_SENDER_ID")  # optional; None uses AT's shared shortcode

_sms_client = None
LIVE_MODE = bool(AT_USERNAME and AT_API_KEY)

if LIVE_MODE:
    try:
        import africastalking

        africastalking.initialize(AT_USERNAME, AT_API_KEY)
        _sms_client = africastalking.SMS
        logger.info("SMS service initialized in LIVE mode (Africa's Talking, user=%s)", AT_USERNAME)
    except ImportError:
        logger.warning(
            "The 'africastalking' package isn't installed. Run `pip install africastalking`. "
            "Falling back to console mode until it's installed."
        )
        LIVE_MODE = False
    except Exception as e:
        logger.error("Failed to initialize Africa's Talking client: %s", e)
        LIVE_MODE = False
else:
    logger.info(
        "SMS service running in CONSOLE mode (AT_USERNAME/AT_API_KEY not set). "
        "Messages will be printed to the console instead of sent."
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


def send_sms(phone_raw, message):
    """
    Sends an SMS to a single recipient.

    Returns a tuple: (success: bool, status_label: str)
      - status_label is one of "Sent", "Console", or "Failed" — meant to be
        stored directly in SMSLog.status by the caller.

    Never raises — safe to call from the background scheduler without
    risking the whole reminder job on a single bad number or API hiccup.
    """
    phone = normalize_kenyan_number(phone_raw)
    if not phone:
        logger.warning("Skipped SMS — '%s' is not a recognizable phone number.", phone_raw)
        return False, "Failed"

    if not LIVE_MODE:
        print(f"[SMS-CONSOLE] To: {phone} | Message: {message}")
        return True, "Console"

    try:
        kwargs = {"message": message, "recipients": [phone]}
        if AT_SENDER_ID:
            kwargs["sender_id"] = AT_SENDER_ID

        response = _sms_client.send(**kwargs)
        recipients = response.get("SMSMessageData", {}).get("Recipients", [])

        if recipients and recipients[0].get("status") == "Success":
            logger.info("SMS sent to %s", phone)
            return True, "Sent"
        else:
            logger.error("SMS to %s failed: %s", phone, recipients)
            return False, "Failed"
    except Exception as e:
        logger.error("SMS send error to %s: %s", phone, e)
        return False, "Failed"
