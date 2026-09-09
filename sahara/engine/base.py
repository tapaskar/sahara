"""A voice engine takes 16 kHz PCM16 from the phone and yields events:
audio out (at `out_hz`), transcripts both ways, tool calls, and end."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class EngineEvent:
    type: str                      # audio | transcript_in | transcript_out | tool_call | interrupted | turn_complete | end
    data: Any = None               # bytes for audio, str for transcripts, dict for tool_call
    meta: dict = field(default_factory=dict)


class VoiceEngine(ABC):
    name = "base"
    out_hz = 24000
    in_hz = 16000

    def __init__(self):
        self.queue: asyncio.Queue[EngineEvent] = asyncio.Queue()

    @abstractmethod
    async def start(self, system_prompt: str, tools: list[dict], language: str, opening: str) -> None: ...

    @abstractmethod
    async def send_audio(self, pcm16: bytes) -> None: ...

    async def send_tool_result(self, call_id: str, name: str, result: dict) -> None:
        return None

    async def close(self) -> None:
        return None

    async def events(self) -> AsyncIterator[EngineEvent]:
        while True:
            ev = await self.queue.get()
            yield ev
            if ev.type == "end":
                return

    def emit(self, type: str, data: Any = None, **meta) -> None:
        self.queue.put_nowait(EngineEvent(type, data, meta))
