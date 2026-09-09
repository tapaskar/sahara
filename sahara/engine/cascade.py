"""Cascade engine: Sarvam speech-to-text -> Gemini text (with tools) -> Sarvam text-to-speech.

Turn-based, so it needs an endpointer (EnergyVAD) and cannot be interrupted
mid-sentence the way Gemini Live can. It is the fallback when the Live model
is not available in a project, and the path to swap in on-device models later.

Endpoint shapes follow Sarvam's public API; confirm field names against
docs.sarvam.ai before the first live call, they version their models often.
"""
from __future__ import annotations

import asyncio
import base64
import logging

import httpx
from google.genai import types

from .. import config
from ..audio import EnergyVAD, wav_bytes, wav_to_pcm
from ..gemini import text_client
from .base import VoiceEngine

log = logging.getLogger("sahara.engine.cascade")
SARVAM = "https://api.sarvam.ai"


class CascadeEngine(VoiceEngine):
    name = "cascade"
    out_hz = 22050

    def __init__(self):
        super().__init__()
        self.vad = EnergyVAD()
        self.history: list[types.Content] = []
        self.system = ""
        self.tools: list[dict] = []
        self.language = "hi-IN"
        self._busy = asyncio.Lock()
        self._pending_tools: dict[str, str] = {}

    async def start(self, system_prompt: str, tools: list[dict], language: str, opening: str) -> None:
        self.system, self.tools, self.language = system_prompt, tools, language
        await self._respond(f"(The call has connected. {opening})")

    async def send_audio(self, pcm16: bytes) -> None:
        utt = self.vad.feed(pcm16)
        if utt and not self._busy.locked():
            asyncio.create_task(self._on_utterance(utt))

    async def send_tool_result(self, call_id: str, name: str, result: dict) -> None:
        self._pending_tools[call_id] = name

    async def _on_utterance(self, pcm16: bytes):
        async with self._busy:
            text = await self._stt(pcm16)
            if not text.strip():
                return
            self.emit("transcript_in", text, final=True)
            await self._respond(text)

    async def _respond(self, user_text: str):
        self.history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        cfg = types.GenerateContentConfig(
            system_instruction=self.system,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in self.tools])] if self.tools else None)
        r = await text_client().aio.models.generate_content(model=config.GEMINI_TEXT_MODEL,
                                                            contents=self.history, config=cfg)
        cand = r.candidates[0].content if r.candidates else None
        if cand is None:
            return
        self.history.append(cand)
        said = []
        for part in cand.parts or []:
            if part.function_call:
                fc = part.function_call
                self.emit("tool_call", {"id": fc.id or fc.name, "name": fc.name, "args": dict(fc.args or {})})
            if part.text:
                said.append(part.text)
        if said:
            text = " ".join(said).strip()
            self.emit("transcript_out", text)
            pcm = await self._tts(text)
            if pcm:
                self.emit("audio", pcm)
        self.emit("turn_complete")

    async def _stt(self, pcm16: bytes) -> str:
        if not config.SARVAM_API_KEY:
            return ""
        wav = wav_bytes(pcm16, 16000)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{SARVAM}/speech-to-text", headers={"api-subscription-key": config.SARVAM_API_KEY},
                             files={"file": ("u.wav", wav, "audio/wav")},
                             data={"model": config.SARVAM_STT_MODEL, "language_code": self.language})
            r.raise_for_status()
            return r.json().get("transcript", "")

    async def _tts(self, text: str) -> bytes:
        if not config.SARVAM_API_KEY:
            return b""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{SARVAM}/text-to-speech", headers={"api-subscription-key": config.SARVAM_API_KEY},
                             json={"inputs": [text[:500]], "target_language_code": self.language,
                                   "speaker": config.SARVAM_TTS_SPEAKER, "model": config.SARVAM_TTS_MODEL,
                                   "speech_sample_rate": 22050})
            r.raise_for_status()
            audios = r.json().get("audios") or []
        if not audios:
            return b""
        pcm, hz = wav_to_pcm(base64.b64decode(audios[0]))
        self.out_hz = hz
        return pcm
