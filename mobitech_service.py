"""
mobitech_service.py — SMS Notification Service for Timos ERP Manager
============================================================================
Sends plain SMS (not WhatsApp) via Mobitech Technologies' Kenya Bulk SMS
REST API. Replaces the old whatsapp_service.py — same job (debt reminders,
booking reminders), different provider and channel.

API REFERENCE (Mobitech Bulk SMS):
    Endpoint:   POST https://api.mobitechtechnologies.com/sms/sendsms
    Auth:       API key sent as the 'h_api_key' HTTP header (not in the body)
    Body (JSON):
        mobile          - recipient number, any common format (0722xxxyyy,
                           722xxxyyy, +254722xxxyyy all accepted by Mobitech)
        response_type   - "json" (we always request this)
        sender_name     - your approved alphanumeric/numeric sender ID
        service_id      - "0" for standard bulk messaging
        message         - the SMS text

    Success response looks like:
        [{"status_code": "1000", "status_desc": "Success",
          "message_id": 70055777, "mobile_number": "254712345678", ...}]

    A non-"1000" status_code means the message did NOT go out — Mobitech
    still returns HTTP 200 in that case, so success is judged from
    status_code, not the HTTP status.

SETUP:
1. Log in to your Mobitech Technologies dashboard and locate your API key
   and your approved sender name (Sender ID).
2. Set these environment variables (same place you'd set DATABASE_URL etc.):

       MOBITECH_API_KEY=your_api_key_here
       MOBITECH_SENDER_NAME=your_approved_sender_id
       MOBITECH_SERVICE_ID=0            # optional, defaults to "0"

DEV-SAFE BY DEFAULT:
If MOBITECH_API_KEY isn't set, this module runs in "console mode": messages 
are printed to the terminal instead of actually sent. If MOBITECH_SENDER_NAME 
is left blank, it defaults to Mobitech's shared gateway.
"""

import os
import re
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("timos_sms")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

MOBITECH_API_KEY = os.environ.get("MOBITECH_API_KEY")
MOBITECH_SENDER_NAME = os.environ.get("MOBITECH_SENDER_NAME", "")
MOBITECH_SERVICE_ID = os.environ.get("MOBITECH_SERVICE_ID", "0")

SEND_URL = "https://api.mobitechtechnologies.com/sms/sendsms"

# Mobitech's own "it worked" status code (see response codes table in their docs).
SUCCESS_STATUS_CODE = "1000"

LIVE_MODE = bool(MOBITECH_API_KEY)

if LIVE_MODE:
    logger.info("SMS service initialized in LIVE mode (Mobitech, sender=%s)", MOBITECH_SENDER_NAME or "Default Gateway")
else:
    logger.info(
        "SMS service running in CONSOLE mode (MOBITECH_API_KEY not set). "
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
    Sends an SMS to a single recipient via Mobitech.

    Returns a 3-tuple: (success: bool, status_label: str, error_detail: str|None)
      - status_label is one of "Sent", "Console", or "Failed" — meant to be
        stored directly in SMSLog.status by the caller.
      - error_detail carries the reason for a "Failed" status (bad number,
        API error, etc.) so it can be logged and shown in the Recent
        Notifications table instead of just "Failed".

    Never raises — safe to call from the background scheduler without
    risking the whole reminder job on a single bad number or API hiccup.
    """
    phone = normalize_kenyan_number(phone_raw)
    if not phone:
        detail = f"'{phone_raw}' is not a recognizable phone number"
        logger.warning("Skipped SMS — %s.", detail)
        return False, "Failed", detail

    if not LIVE_MODE:
        print(f"[SMS-CONSOLE] To: {phone} | Message: {message}")
        return True, "Console", None

    try:
        payload = {
            "mobile": phone,
            "response_type": "json",
            "sender_name": MOBITECH_SENDER_NAME,
            "service_id": MOBITECH_SERVICE_ID,
            "message": message,
        }
        headers = {
            "h_api_key": MOBITECH_API_KEY,
            "content-type": "application/json",
        }
        response = requests.post(SEND_URL, json=payload, headers=headers, timeout=15)
        data = response.json() if response.content else None

        # Mobitech returns a JSON array (even for a single recipient).
        result = data[0] if isinstance(data, list) and data else (data or {})
        status_code = str(result.get("status_code", ""))

        if response.status_code == 200 and status_code == SUCCESS_STATUS_CODE:
            logger.info("SMS sent to %s (message_id=%s)", phone, result.get("message_id"))
            return True, "Sent", None
        else:
            detail = result.get("status_desc") or str(data) or f"HTTP {response.status_code}"
            logger.error("SMS send to %s failed: %s", phone, detail)
            return False, "Failed", str(detail)[:255]
    except Exception as e:
        logger.error("SMS send error to %s: %s", phone, e)
        return False, "Failed", str(e)[:255]