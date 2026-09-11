"""The bridge: provider WebSocket frames <-> voice engine.

Two loops. Inbound: phone mu-law -> PCM16 16 kHz -> engine. Outbound: engine
audio -> mu-law 8 kHz -> 20 ms frames -> provider; transcripts and tool calls go
to callbacks. Barge-in: on `interrupted` we tell the provider to drop queued
audio so the agent stops talking when the parent does.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from .. import config
from ..audio import model_to_phone, phone_to_model
from ..engine.base import VoiceEngine
from .base import Telephony

log = logging.getLogger("sahara.stream")
FRAME = 160              # 20 ms of 8 kHz mu-law
GOODBYE_MARK = "sahara-goodbye"
GOODBYE_WAIT = 20.0      # cap: a long goodbye still ends, even if the echo never comes
GOODBYE_BLIND = 2.5      # providers without marks: the old fixed wait


async def bridge(ws: WebSocket, provider: Telephony, engine: VoiceEngine,
                 on_transcript: Callable[[str, str, bool], Awaitable[None]],
                 on_tool_call: Callable[[dict], Awaitable[dict | None]],
                 max_seconds: int | None = None,
                 on_turn_end: Callable[[], Awaitable[None]] | None = None) -> dict:
    """Runs until the provider stops the stream, the engine ends, or the time cap hits."""
    info: dict = {}
    started = time.time()
    max_seconds = max_seconds or config.MAX_CALL_SECONDS
    stop = asyncio.Event()
    stream_ready = asyncio.Event()     # provider sent its start frame (carries the stream id)
    goodbye_played = asyncio.Event()   # the provider says the farewell audio has been heard
    stats = {"frames_in": 0, "frames_out": 0, "ended_by": ""}

    async def inbound():
        try:
            while not stop.is_set():
                msg = await ws.receive_json()
                ev, mulaw, meta = provider.parse_frame(msg)
                if ev == "start":
                    info.update(meta); stream_ready.set()
                elif ev == "media" and mulaw:
                    stream_ready.set()
                    stats["frames_in"] += 1
                    await engine.send_audio(phone_to_model(mulaw, engine.in_hz))
                elif ev == "stop":
                    stats["ended_by"] = "provider"; stop.set()
                elif ev == "mark" and meta.get("name") == GOODBYE_MARK:
                    goodbye_played.set()
        except WebSocketDisconnect:
            stats["ended_by"] = stats["ended_by"] or "disconnect"; stop.set()
        except Exception as e:
            log.warning("inbound ended: %s", e); stats["ended_by"] = stats["ended_by"] or "error"; stop.set()

    async def outbound():
        ending = False
        try:
            async for ev in engine.events():
                if ev.type == "audio":
                    await stream_ready.wait()     # never send audio before we know the stream id
                    mulaw = model_to_phone(ev.data, engine.out_hz)
                    for i in range(0, len(mulaw), FRAME):
                        await ws.send_json(provider.audio_frame(mulaw[i:i + FRAME], info))
                        stats["frames_out"] += 1
                elif ev.type == "interrupted":
                    await ws.send_json(provider.clear_frame(info))
                elif ev.type in ("transcript_in", "transcript_out"):
                    await on_transcript("parent" if ev.type == "transcript_in" else "sahara", ev.data,
                                        ev.meta.get("final", True), ev.meta)
                elif ev.type == "tool_call":
                    result = await on_tool_call(ev.data)
                    await engine.send_tool_result(ev.data["id"], ev.data["name"], result or {"ok": True})
                    if ev.data["name"] == "end_call":
                        ending = True
                elif ev.type == "turn_complete":
                    if on_turn_end:
                        await on_turn_end()
                    if ending:
                        # Do not hang up until the goodbye has actually been heard. A mark
                        # sits behind the queued audio and is echoed back when that audio
                        # finishes, so the farewell is never clipped and we never wait
                        # longer than we must.
                        mark = provider.mark_frame(GOODBYE_MARK, info)
                        if mark is not None:
                            await ws.send_json(mark)
                            try:
                                await asyncio.wait_for(goodbye_played.wait(), GOODBYE_WAIT)
                                stats["goodbye"] = "heard"
                            except asyncio.TimeoutError:
                                log.warning("no goodbye mark after %.0fs; hanging up", GOODBYE_WAIT)
                                stats["goodbye"] = "timeout"
                        else:
                            await asyncio.sleep(GOODBYE_BLIND)   # provider has no marks
                            stats["goodbye"] = "blind"
                        stats["ended_by"] = "agent"; stop.set(); return
                elif ev.type == "end":
                    stats["ended_by"] = stats["ended_by"] or "engine"; stop.set(); return
        except Exception as e:
            log.warning("outbound ended: %s", e); stats["ended_by"] = stats["ended_by"] or "error"; stop.set()

    async def timer():
        await asyncio.sleep(max_seconds)
        stats["ended_by"] = stats["ended_by"] or "timeout"; stop.set()

    tasks = [asyncio.create_task(inbound()), asyncio.create_task(outbound()), asyncio.create_task(timer())]
    await stop.wait()
    for t in tasks:
        t.cancel()
    await engine.close()
    stats["seconds"] = round(time.time() - started)
    return stats
