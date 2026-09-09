"""Plivo: REST calls + bidirectional Audio Stream (<Stream bidirectional="true">)."""
from __future__ import annotations

import base64
from xml.sax.saxutils import escape

import httpx

from .. import config
from .base import Telephony


class Plivo(Telephony):
    name = "plivo"

    async def place_call(self, to: str, call_id: int) -> str:
        url = f"https://api.plivo.com/v1/Account/{config.PLIVO_AUTH_ID}/Call/"
        body = {"from": config.CALLER_ID.lstrip("+"), "to": to.lstrip("+"),
                "answer_url": self.answer_url(call_id), "answer_method": "POST",
                "hangup_url": self.status_url(call_id), "hangup_method": "POST", "ring_timeout": 25}
        async with httpx.AsyncClient(timeout=20, auth=(config.PLIVO_AUTH_ID, config.PLIVO_AUTH_TOKEN)) as c:
            r = await c.post(url, json=body)
            r.raise_for_status()
            j = r.json()
            return (j.get("request_uuid") or "")

    def answer_xml(self, call_id: int, after_url: str | None = None) -> str:
        after = f'<Redirect method="POST">{escape(after_url)}</Redirect>' if after_url else ""
        return (f'<?xml version="1.0" encoding="UTF-8"?><Response>'
                f'<Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">'
                f'{escape(self.stream_url(call_id))}</Stream>{after}</Response>')

    def dial_xml(self, to: str, caller_id: str) -> str:
        return (f'<?xml version="1.0" encoding="UTF-8"?><Response>'
                f'<Dial callerId="{escape(caller_id.lstrip("+"))}" timeout="25"><Number>{escape(to.lstrip("+"))}</Number>'
                f'</Dial></Response>')

    def hangup_xml(self) -> str:
        return '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>'

    def parse_frame(self, msg: dict) -> tuple[str, bytes | None, dict]:
        ev = msg.get("event")
        if ev == "start":
            return "start", None, {"stream_id": (msg.get("start") or {}).get("streamId")}
        if ev == "media":
            return "media", base64.b64decode(msg["media"]["payload"]), {}
        if ev in ("stop", "hangup"):
            return "stop", None, {}
        return "other", None, {}

    def audio_frame(self, mulaw: bytes, info: dict) -> dict:
        return {"event": "playAudio", "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000,
                                                "payload": base64.b64encode(mulaw).decode()}}

    def clear_frame(self, info: dict) -> dict:
        return {"event": "clearAudio"}

    def status_from_callback(self, form: dict) -> str | None:
        cause = (form.get("HangupCause") or form.get("HangupCauseName") or "").lower()
        if any(k in cause for k in ("no_answer", "no answer", "busy", "cancel", "reject")):
            return "no_answer"
        return None
