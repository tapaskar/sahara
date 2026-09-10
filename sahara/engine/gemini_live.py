"""Gemini Live API: audio in, audio out, transcripts both ways, tool calls, barge-in.

Native-audio Live models choose the spoken language from the conversation and
the system instruction; a language code is not set. The parent's language is
therefore pinned in the prompt, not in config.
"""
from __future__ import annotations

import asyncio
import logging

from google.genai import types

from .. import config
from ..gemini import live_client
from .base import VoiceEngine

log = logging.getLogger("sahara.engine.gemini_live")


def transcription_languages(language: str) -> list[str]:
    """The input-transcription hint. Two corrections that matter:
    - od-IN is Sarvam's code for Odia; BCP-47 (and Gemini) want or-IN. An unrecognized
      code risks silently falling back to full auto-detect — the exact drift being fixed.
    - Always co-hint en-IN: elders code-switch into English constantly, and a lone
      Hindi hint would push English words into Devanagari mis-transcription."""
    code = {"od-IN": "or-IN"}.get(language, language)
    return [code] if code == "en-IN" else [code, "en-IN"]


class GeminiLiveEngine(VoiceEngine):
    name = "gemini_live"
    out_hz = 24000

    def __init__(self):
        super().__init__()
        self._session = None
        self._cm = None
        self._rx: asyncio.Task | None = None

    async def start(self, system_prompt: str, tools: list[dict], language: str, opening: str) -> None:
        cfg = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in tools])] if tools else None,
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.GEMINI_VOICE))),
            # The language hint is a prior, not a constraint (the SDK's own word is
            # "hints"): left to auto-detect, one live Hindi call transcribed as Portuguese.
            # VERBATIM is pinned because SMART's disfluency removal would erase exactly
            # the elderly-speech phenomena the pilot exists to measure.
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=transcription_languages(language),
                mode=types.AudioTranscriptionConfigMode.VERBATIM),
            output_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=transcription_languages(language)[:1]),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,   # elders pause
                    silence_duration_ms=900, prefix_padding_ms=200)),
        )
        log.info("live transcription hint: in=%s out=%s mode=VERBATIM",
                 transcription_languages(language), transcription_languages(language)[:1])
        self._cm = live_client().aio.live.connect(model=config.GEMINI_LIVE_MODEL, config=cfg)
        self._session = await self._cm.__aenter__()
        self._rx = asyncio.create_task(self._receive())
        # make the model speak first: a user turn that is only a cue
        await self._session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=f"(The call has connected. {opening})")]),
            turn_complete=True)

    async def send_audio(self, pcm16: bytes) -> None:
        if self._session is not None:
            await self._session.send_realtime_input(audio=types.Blob(data=pcm16, mime_type="audio/pcm;rate=16000"))

    async def send_tool_result(self, call_id: str, name: str, result: dict) -> None:
        if self._session is not None:
            await self._session.send_tool_response(function_responses=[
                types.FunctionResponse(id=call_id, name=name, response=result)])

    async def _receive(self):
        """`session.receive()` yields ONE model turn and then stops — the SDK breaks out of
        its own loop on turn_complete. Re-enter it for every turn, or the call ends the
        instant Sahara stops speaking and the parent never gets to answer."""
        try:
            while True:
                turn_had_messages = False
                async for msg in self._session.receive():
                    turn_had_messages = True
                    sc = msg.server_content
                    if msg.data:
                        self.emit("audio", msg.data)
                    if sc is not None:
                        if sc.input_transcription and sc.input_transcription.text:
                            self.emit("transcript_in", sc.input_transcription.text,
                                      final=bool(getattr(sc.input_transcription, "finished", True)),
                                      language_code=getattr(sc.input_transcription, "language_code", None))
                        if sc.output_transcription and sc.output_transcription.text:
                            # streams in word-sized chunks; the turn closes on turn_complete
                            self.emit("transcript_out", sc.output_transcription.text, final=False)
                        if sc.interrupted:
                            self.emit("interrupted")
                        if sc.turn_complete:
                            self.emit("turn_complete")
                    if msg.tool_call:
                        for fc in msg.tool_call.function_calls or []:
                            self.emit("tool_call", {"id": fc.id, "name": fc.name, "args": dict(fc.args or {})})
                    if msg.go_away:
                        log.warning("live session go_away: %s", msg.go_away)
                if not turn_had_messages:
                    break                      # the session itself closed
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("live receive ended: %s", e)
        finally:
            self.emit("end")

    async def close(self) -> None:
        if self._rx:
            self._rx.cancel()
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        self._session = None
