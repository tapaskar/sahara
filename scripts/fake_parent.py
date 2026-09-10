#!/usr/bin/env python3
"""A scripted parent who speaks recorded audio into a live call, taking acoustic turns
with Sahara (waits for her to go quiet, then speaks the next line).

CAVEAT, learned the hard way: Gemini Live\'s automatic voice-activity detection does not
reliably fire on macOS `say` TTS at 8 kHz — the model hears the greeting out but often
does not treat the synthesized replies as turns, so it never responds. Feed real recorded
human WAVs (8 kHz mono PCM16) for a faithful end-to-end test; TTS is fine only for driving
the transport and turn-taking, not for exercising the model\'s understanding.

Turn-taking is acoustic: it waits for Sahara's audio to go quiet before speaking the
next line. Unlike fake_phone.py (which streams one WAV and exits), this holds a real
conversation shape: greeting, several answers, goodbye.

    # 1) synthesize some lines (macOS; Lekha is the Hindi voice):
    #    say -v Lekha -o 1.aiff "नमस्ते बेटा, ठीक हूँ।"
    #    afconvert -f WAVE -d LEI16@8000 -c 1 1.aiff 1.wav      (8 kHz mono PCM16)
    # 2) python scripts/fake_parent.py --parent 1 --lines 1.wav 2.wav 3.wav
"""
import argparse
import asyncio
import base64
import json
import os
import ssl
import sys
import time
import wave

import httpx
import numpy as np
import websockets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sahara.audio import mulaw_encode, resample  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="https://localhost:8090")
ap.add_argument("--parent", type=int, default=1)
ap.add_argument("--lines", nargs="+", required=True, help="8 kHz mono PCM16 WAVs, in speaking order")
ap.add_argument("--token", default=os.environ.get("SAHARA_OPERATOR_TOKEN", ""))
ap.add_argument("--gap", type=float, default=1.6, help="silence (s) after Sahara stops before we speak")
ap.add_argument("--max-seconds", type=float, default=150)
a = ap.parse_args()

FRAME = 160  # 20 ms of 8 kHz


def load_8k(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() == 2:
            pcm = pcm[::2]
        if w.getframerate() != 8000:
            pcm = resample(pcm, w.getframerate(), 8000)
    return mulaw_encode(pcm)


async def main():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    verify = a.url.startswith("https")

    with httpx.Client(verify=False if verify else True, timeout=20) as c:
        r = c.post(f"{a.url}/api/parents/{a.parent}/mic-call",
                   headers={"x-sahara-token": a.token})
        r.raise_for_status()
        call = r.json()
    print(f"call {call['call_id']} · {call['parent']} · engine {call['engine']}")

    lines = [load_8k(p) for p in a.lines]
    silence = mulaw_encode(np.zeros(FRAME, dtype=np.int16))
    quiet = base64.b64encode(silence).decode()

    ws_url = a.url.replace("https", "wss").replace("http", "ws")
    kw = {"ssl": ctx} if verify else {}
    heard = 0
    last_heard = [0.0]
    t0 = time.monotonic()

    async with websockets.connect(f"{ws_url}/ws/twilio/{call['call_id']}", **kw) as ws:
        await ws.send(json.dumps({"event": "start", "streamSid": "MZfakeparent",
                                  "start": {"streamSid": "MZfakeparent",
                                            "customParameters": {"call_id": call["call_id"]}}}))

        async def rx():
            nonlocal heard
            try:
                async for m in ws:
                    if json.loads(m).get("event") == "media":
                        heard += 1
                        last_heard[0] = time.monotonic()
            except Exception:
                pass

        rx_task = asyncio.create_task(rx())
        line_i, speaking, pos = 0, None, 0
        spoke_at = 0.0
        try:
            while time.monotonic() - t0 < a.max_seconds:
                now = time.monotonic()
                if speaking is None and line_i < len(lines):
                    sahara_done = heard > 20 and last_heard[0] and (now - last_heard[0]) > a.gap
                    if sahara_done and (now - spoke_at) > a.gap:
                        speaking, pos = lines[line_i], 0
                        line_i += 1
                        print(f"  speaking line {line_i}/{len(lines)}")
                if speaking is not None:
                    chunk = speaking[pos:pos + FRAME]
                    pos += FRAME
                    if pos >= len(speaking):
                        speaking, spoke_at = None, now
                    payload = chunk if len(chunk) == FRAME else chunk + silence[:FRAME - len(chunk)]
                else:
                    payload = silence
                await ws.send(json.dumps({"event": "media",
                                          "media": {"payload": base64.b64encode(payload).decode()}}))
                await asyncio.sleep(0.02)
                if line_i >= len(lines) and speaking is None and last_heard[0] \
                        and (now - last_heard[0]) > 8:
                    break                              # said everything; Sahara has gone quiet
        except websockets.ConnectionClosed:
            print("  Sahara hung up first (agent ended the call)")
        finally:
            rx_task.cancel()
            try:
                await ws.send(json.dumps({"event": "stop"}))
            except Exception:
                pass

    print(f"done: heard {heard} frames of Sahara over {time.monotonic() - t0:.0f}s")


asyncio.run(main())
