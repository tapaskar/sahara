"""What a telephony provider must do for Sahara: place a call, hand the call's
audio to us over a WebSocket, dial the parent after screening, hang up."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .. import config


def ws_base() -> str:
    """The wss:// origin the provider dials back into.

    Twilio accepts Stream urls over wss:// ONLY (its TwiML reference is explicit), and a
    plain-http SAHARA_PUBLIC_URL yields ws:// — which is not rejected at configuration
    time but fails once the call is already connected, so it reads as a broken product.
    Warn loudly rather than let that reach a real phone."""
    url = config.PUBLIC_URL.rstrip("/")
    if url.startswith("http://") and not config.OFFLINE and "localhost" not in url \
            and "127.0.0.1" not in url:
        logging.getLogger("sahara.telephony").error(
            "SAHARA_PUBLIC_URL is http:// — Twilio and Plivo only dial wss:// media streams, "
            "so calls will connect and then drop. Use the https URL of your tunnel: %s", url)
    return url.replace("https://", "wss://").replace("http://", "ws://")


class Telephony(ABC):
    name = "base"

    @abstractmethod
    async def place_call(self, to: str, call_id: int) -> str:
        """Start an outbound call; the provider will fetch answer_url when it connects."""

    def answer_url(self, call_id: int) -> str:
        return f"{config.PUBLIC_URL.rstrip('/')}/telephony/{self.name}/answer?call_id={call_id}"

    def status_url(self, call_id: int) -> str:
        return f"{config.PUBLIC_URL.rstrip('/')}/telephony/{self.name}/status?call_id={call_id}"

    def stream_url(self, call_id: int) -> str:
        return f"{ws_base()}/ws/{self.name}/{call_id}"

    @abstractmethod
    def answer_xml(self, call_id: int, after_url: str | None = None) -> str:
        """Instruct the provider to open the bidirectional audio stream, then continue at after_url."""

    @abstractmethod
    def dial_xml(self, to: str, caller_id: str) -> str: ...

    @abstractmethod
    def hangup_xml(self) -> str: ...

    # --- stream protocol -----------------------------------------------------
    @abstractmethod
    def parse_frame(self, msg: dict) -> tuple[str, bytes | None, dict]:
        """-> (event, mulaw_bytes, info) with event in start | media | stop | other"""

    @abstractmethod
    def audio_frame(self, mulaw: bytes, info: dict) -> dict: ...

    @abstractmethod
    def clear_frame(self, info: dict) -> dict: ...

    def mark_frame(self, name: str, info: dict) -> dict | None:
        """A frame the provider echoes back once the audio queued before it has finished
        playing. Returning None means this provider has no such mechanism and the caller
        must fall back to waiting a fixed time."""
        return None

    def status_from_callback(self, form: dict) -> str | None:
        """Map a status webhook to Call.status, or None to ignore."""
        return None
