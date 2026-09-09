from __future__ import annotations

from .. import config
from .base import EngineEvent, VoiceEngine


def make_engine(name: str | None = None) -> VoiceEngine:
    name = name or ("null" if config.OFFLINE else config.VOICE_ENGINE)
    if name == "gemini_live":
        from .gemini_live import GeminiLiveEngine
        return GeminiLiveEngine()
    if name == "cascade":
        from .cascade import CascadeEngine
        return CascadeEngine()
    if name == "null":
        from .null import NullEngine
        return NullEngine()
    raise ValueError(f"unknown voice engine {name}")


__all__ = ["EngineEvent", "VoiceEngine", "make_engine"]
