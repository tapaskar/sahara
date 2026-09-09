#!/usr/bin/env python3
"""Pretend to be Twilio: open the media stream for a call and play a WAV file
as the parent's voice, saving what Sahara says to a WAV. Lets you test the
whole voice loop from a laptop before buying a phone number.

    # 1) create a call row (offline or not) and note its id:
    #    curl -X POST localhost:8080/api/parents/1/call-now   (prints the call id)
    # 2) python scripts/fake_phone.py --call 1 --wav samples/parent_hindi.wav --out reply.wav
"""
import argparse
import asyncio
import base64
import json
import sys

import websockets

sys.path.insert(0, ".")
from sahara.audio import mulaw_decode, mulaw_encode, resample, wav_bytes, wav_to_pcm  # noqa: E402
import numpy as np  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="ws://localhost:8080")
ap.add_argument("--call", type=int, required=True)
ap.add_argument("--wav", help="16-bit mono WAV of the 'parent' speaking; silence if omitted")
ap.add_argument("--seconds", type=float, default=20)
ap.add_argument("--out", default="sahara_said.wav")
a = ap.parse_args()


async def main():
    if a.wav:
        pcm, hz = wav_to_pcm(open(a.wav, "rb").read())
        pcm8 = resample(np.frombuffer(pcm, dtype=np.int16), hz, 8000)
    else:
        pcm8 = np.zeros(int(8000 * a.seconds), dtype=np.int16)
    mulaw = mulaw_encode(pcm8)
    heard = bytearray()
    async with websockets.connect(f"{a.url}/ws/twilio/{a.call}") as ws:
        await ws.send(json.dumps({"event": "start", "streamSid": "MZfake", "start": {"customParameters": {"call_id": a.call}}}))

        async def rx():
            try:
                async for m in ws:
                    j = json.loads(m)
                    if j.get("event") == "media":
                        heard.extend(base64.b64decode(j["media"]["payload"]))
            except Exception:
                pass
        t = asyncio.create_task(rx())
        hung_up = False
        try:
            for i in range(0, len(mulaw), 160):
                await ws.send(json.dumps({"event": "media", "media": {"payload": base64.b64encode(mulaw[i:i + 160]).decode()}}))
                await asyncio.sleep(0.02)
            await asyncio.sleep(3)
            await ws.send(json.dumps({"event": "stop"}))
            await asyncio.sleep(0.5)
        except websockets.exceptions.ConnectionClosed:
            hung_up = True                      # Sahara ended the call first: that is the normal case
        t.cancel()
    print("Sahara hung up first" if hung_up else "we hung up")
    pcm = mulaw_decode(bytes(heard)).tobytes()
    open(a.out, "wb").write(wav_bytes(pcm, 8000))
    print(f"sent {len(mulaw)//160} frames, heard {len(heard)//160} frames -> {a.out}")

asyncio.run(main())
