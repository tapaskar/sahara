"""Audio plumbing between the phone network and the models.

Phones speak 8 kHz G.711 mu-law. Gemini Live wants 16 kHz PCM16 in and returns
24 kHz PCM16. Sarvam returns 22.05 kHz WAV. Everything here is numpy so it runs
anywhere, including Python 3.13 where the stdlib audioop is gone.
"""
from __future__ import annotations

import io
import struct
import wave

import numpy as np

MULAW_BIAS = 0x84
MULAW_CLIP = 32635


def mulaw_decode(data: bytes) -> np.ndarray:
    """mu-law bytes -> int16 samples."""
    u = np.frombuffer(data, dtype=np.uint8).astype(np.int16)
    u = ~u & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = ((mantissa << 3) + MULAW_BIAS) << exponent
    sample = sample - MULAW_BIAS
    return np.where(sign != 0, -sample, sample).astype(np.int16)


def mulaw_encode(pcm: np.ndarray) -> bytes:
    """int16 samples -> mu-law bytes."""
    s = pcm.astype(np.int32)
    sign = np.where(s < 0, 0x80, 0)
    s = np.abs(s)
    s = np.minimum(s, MULAW_CLIP) + MULAW_BIAS
    exponent = np.floor(np.log2(s)).astype(np.int32) - 7
    exponent = np.clip(exponent, 0, 7)
    mantissa = (s >> (exponent + 3)) & 0x0F
    u = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return u.astype(np.uint8).tobytes()


def resample(pcm: np.ndarray, src_hz: int, dst_hz: int) -> np.ndarray:
    """Linear-interpolation resampler; adequate for speech, dependency-free."""
    if src_hz == dst_hz or len(pcm) == 0:
        return pcm.astype(np.int16)
    n_out = int(round(len(pcm) * dst_hz / src_hz))
    x_old = np.linspace(0.0, 1.0, num=len(pcm), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    y = np.interp(x_new, x_old, pcm.astype(np.float32))
    return np.clip(np.round(y), -32768, 32767).astype(np.int16)


def phone_to_model(mulaw: bytes, model_hz: int = 16000) -> bytes:
    """8 kHz mu-law frame -> PCM16 bytes at the model's rate."""
    return resample(mulaw_decode(mulaw), 8000, model_hz).tobytes()


def model_to_phone(pcm16: bytes, model_hz: int = 24000) -> bytes:
    """PCM16 bytes at the model's rate -> 8 kHz mu-law bytes."""
    pcm = np.frombuffer(pcm16, dtype=np.int16)
    return mulaw_encode(resample(pcm, model_hz, 8000))


def rms(pcm16: bytes) -> float:
    pcm = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(pcm * pcm))) if len(pcm) else 0.0


def wav_bytes(pcm16: bytes, hz: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(hz); w.writeframes(pcm16)
    return buf.getvalue()


def wav_to_pcm(data: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        hz, n, width, ch = w.getframerate(), w.getnframes(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(n)
    if width != 2:
        raise ValueError(f"expected 16-bit wav, got {width * 8}-bit")
    pcm = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return pcm.tobytes(), hz


class EnergyVAD:
    """Tiny endpointer for the cascade engine: speech started / speech ended."""

    def __init__(self, hz: int = 16000, frame_ms: int = 20, threshold: float = 400.0,
                 silence_ms: int = 700, min_speech_ms: int = 300):
        self.frame = int(hz * frame_ms / 1000) * 2
        self.threshold, self.silence_frames = threshold, silence_ms // frame_ms
        self.min_speech_frames = min_speech_ms // frame_ms
        self.buf = bytearray(); self.speech: list[bytes] = []
        self.speaking = False; self.silent = 0

    def feed(self, pcm16: bytes) -> bytes | None:
        """Returns a completed utterance (PCM16) when the speaker goes quiet."""
        self.buf += pcm16
        out = None
        while len(self.buf) >= self.frame:
            f, self.buf = bytes(self.buf[:self.frame]), self.buf[self.frame:]
            loud = rms(f) > self.threshold
            if loud:
                self.speaking = True; self.silent = 0; self.speech.append(f)
            elif self.speaking:
                self.speech.append(f); self.silent += 1
                if self.silent >= self.silence_frames:
                    if len(self.speech) >= self.min_speech_frames + self.silence_frames:
                        out = b"".join(self.speech)
                    self.speech, self.speaking, self.silent = [], False, 0
        return out
