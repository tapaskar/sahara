from __future__ import annotations

from .. import config
from .base import Telephony


def make_telephony(name: str | None = None) -> Telephony:
    name = name or config.TELEPHONY
    if name == "twilio":
        from .twilio import Twilio
        return Twilio()
    if name == "plivo":
        from .plivo import Plivo
        return Plivo()
    raise ValueError(f"unknown telephony provider {name}")


__all__ = ["Telephony", "make_telephony"]
