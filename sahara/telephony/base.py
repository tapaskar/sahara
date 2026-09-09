"""What a telephony provider must do for Sahara: place a call, hand the call's
audio to us over a WebSocket, dial the parent after screening, hang up."""
from __future__ import annotations

from abc import ABC, abstractmethod

from .. import config


def ws_base() -> str:
    return config.PUBLIC_URL.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")


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

    def status_from_callback(self, form: dict) -> str | None:
        """Map a status webhook to Call.status, or None to ignore."""
        return None
