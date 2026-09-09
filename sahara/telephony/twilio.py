"""Twilio: REST calls + Media Streams (bidirectional via <Connect><Stream>)."""
from __future__ import annotations

import base64
from xml.sax.saxutils import escape

import httpx

from .. import config
from .base import Telephony


class Twilio(Telephony):
    name = "twilio"

    def _auth(self) -> dict:
        tok = base64.b64encode(f"{config.TWILIO_ACCOUNT_SID}:{config.TWILIO_AUTH_TOKEN}".encode()).decode()
        return {"Authorization": f"Basic {tok}"}

    async def place_call(self, to: str, call_id: int) -> str:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{config.TWILIO_ACCOUNT_SID}/Calls.json"
        data = {"To": to, "From": config.CALLER_ID, "Url": self.answer_url(call_id),
                "StatusCallback": self.status_url(call_id), "StatusCallbackEvent": "completed",
                "Timeout": "25", "MachineDetection": "Enable"}
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(url, data=data, headers=self._auth())
            r.raise_for_status()
            return r.json()["sid"]

    def answer_xml(self, call_id: int, after_url: str | None = None) -> str:
        after = f'<Redirect method="POST">{escape(after_url)}</Redirect>' if after_url else ""
        return (f'<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
                f'<Stream url="{escape(self.stream_url(call_id))}">'
                f'<Parameter name="call_id" value="{call_id}"/></Stream></Connect>{after}</Response>')

    def dial_xml(self, to: str, caller_id: str) -> str:
        return (f'<?xml version="1.0" encoding="UTF-8"?><Response>'
                f'<Dial callerId="{escape(caller_id)}" timeout="25">{escape(to)}</Dial></Response>')

    def hangup_xml(self) -> str:
        return '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>'

    def parse_frame(self, msg: dict) -> tuple[str, bytes | None, dict]:
        ev = msg.get("event")
        if ev == "start":
            s = msg.get("start", {})
            return "start", None, {"stream_sid": msg.get("streamSid") or s.get("streamSid"),
                                   "params": s.get("customParameters", {})}
        if ev == "media":
            return "media", base64.b64decode(msg["media"]["payload"]), {}
        if ev == "stop":
            return "stop", None, {}
        return "other", None, {}

    def audio_frame(self, mulaw: bytes, info: dict) -> dict:
        return {"event": "media", "streamSid": info.get("stream_sid"),
                "media": {"payload": base64.b64encode(mulaw).decode()}}

    def clear_frame(self, info: dict) -> dict:
        return {"event": "clear", "streamSid": info.get("stream_sid")}

    def status_from_callback(self, form: dict) -> str | None:
        st = form.get("CallStatus")
        if st in ("no-answer", "busy", "canceled"):
            return "no_answer"
        if st == "failed":
            return "failed"
        if st == "completed" and form.get("AnsweredBy", "").startswith("machine"):
            return "no_answer"
        return None
