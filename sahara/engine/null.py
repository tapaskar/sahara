"""Offline engine: speaks a tone as its greeting, transcribes nothing, ends after
a few seconds of audio. Exists so the telephony bridge and the web app can be
exercised end to end without Gemini."""
from __future__ import annotations

import asyncio

import numpy as np

from .base import VoiceEngine


class NullEngine(VoiceEngine):
    name = "null"
    out_hz = 24000

    def __init__(self, end_after_bytes: int = 16000 * 2 * 6):
        super().__init__()
        self.received = 0
        self.end_after = end_after_bytes

    async def start(self, system_prompt: str, tools: list[dict], language: str, opening: str) -> None:
        t = np.arange(int(0.6 * self.out_hz)) / self.out_hz
        tone = (np.sin(2 * np.pi * 440 * t) * 6000).astype(np.int16).tobytes()
        self.emit("transcript_out", f"(offline greeting in {language})")
        self.emit("audio", tone)
        self.emit("turn_complete")

    async def send_audio(self, pcm16: bytes) -> None:
        self.received += len(pcm16)
        if self.received >= self.end_after:
            self.emit("transcript_in", "(offline: parent audio received)", final=True)
            self.emit("tool_call", {"id": "t1", "name": "log_observation",
                                    "args": {"kind": "other", "detail": "Offline engine heard audio", "severity": "info"}})
            self.emit("tool_call", {"id": "t2", "name": "end_call", "args": {"reason": "offline demo complete"}})
            self.emit("transcript_out", "(offline goodbye)")
            self.emit("turn_complete")
            self.end_after = 1 << 60
            await asyncio.sleep(0)
