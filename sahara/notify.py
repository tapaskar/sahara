"""Tell the child. WhatsApp via Meta's Cloud API or Twilio, or the console offline.

Meta only delivers proactive messages as approved templates, so the daily summary
goes out as a template with one body parameter; see README for the template text.
"""
from __future__ import annotations

import base64
import logging

import httpx

from . import config
from .models import Alert

log = logging.getLogger("sahara.notify")


async def send_whatsapp(to: str, text: str) -> tuple[str, bool]:
    """Returns (channel, delivered)."""
    if config.OFFLINE or config.WHATSAPP == "console":
        print(f"\n=== WhatsApp to {to} ===\n{text}\n===")
        return "console", True
    try:
        if config.WHATSAPP == "meta":
            await _meta(to, text)
            return "meta", True
        if config.WHATSAPP == "twilio":
            await _twilio(to, text)
            return "twilio", True
    except Exception as e:
        log.exception("whatsapp failed: %s", e)
        return config.WHATSAPP, False
    raise ValueError(f"unknown WHATSAPP channel {config.WHATSAPP}")


async def _meta(to: str, text: str):
    url = f"https://graph.facebook.com/v21.0/{config.META_WA_PHONE_ID}/messages"
    body = {"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "template",
            "template": {"name": config.META_WA_TEMPLATE, "language": {"code": "en"},
                         "components": [{"type": "body", "parameters": [{"type": "text", "text": text[:1024]}]}]}}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, json=body, headers={"Authorization": f"Bearer {config.META_WA_TOKEN}"})
        r.raise_for_status()


async def _twilio(to: str, text: str):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{config.TWILIO_ACCOUNT_SID}/Messages.json"
    auth = base64.b64encode(f"{config.TWILIO_ACCOUNT_SID}:{config.TWILIO_AUTH_TOKEN}".encode()).decode()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, data={"From": config.TWILIO_WA_FROM, "To": f"whatsapp:{to}", "Body": text},
                         headers={"Authorization": f"Basic {auth}"})
        r.raise_for_status()


def alert_row(parent_id: int, call_id: int | None, kind: str, severity: str, message: str,
              channel: str, delivered: bool) -> Alert:
    return Alert(parent_id=parent_id, call_id=call_id, kind=kind, severity=severity,
                 message=message, channel=channel, delivered=delivered)
