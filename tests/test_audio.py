import numpy as np

from sahara.audio import (EnergyVAD, model_to_phone, mulaw_decode, mulaw_encode, phone_to_model, resample,
                          wav_bytes, wav_to_pcm)


def test_mulaw_roundtrip_is_close():
    t = np.arange(8000) / 8000
    pcm = (np.sin(2 * np.pi * 300 * t) * 12000).astype(np.int16)
    back = mulaw_decode(mulaw_encode(pcm))
    assert back.shape == pcm.shape
    err = np.abs(back.astype(np.int32) - pcm.astype(np.int32))
    assert np.percentile(err, 99) < 400        # mu-law is ~13-bit; fine for speech
    assert mulaw_decode(bytes([0xFF]))[0] == 0  # 0xFF is silence


def test_resample_lengths_and_roundtrip():
    pcm = np.zeros(8000, dtype=np.int16)
    assert len(resample(pcm, 8000, 16000)) == 16000
    assert len(resample(pcm, 24000, 8000)) == 2667
    frame = bytes([0xFF] * 160)                  # 20 ms of silence
    up = phone_to_model(frame, 16000); assert len(up) == 320 * 2
    down = model_to_phone(b"\x00\x00" * 480, 24000); assert len(down) == 160


def test_wav_helpers():
    pcm = (np.random.default_rng(0).integers(-3000, 3000, 16000)).astype(np.int16).tobytes()
    out, hz = wav_to_pcm(wav_bytes(pcm, 16000))
    assert hz == 16000 and out == pcm


def test_vad_finds_an_utterance():
    vad = EnergyVAD(silence_ms=300, min_speech_ms=200)
    rng = np.random.default_rng(1)
    loud = (rng.normal(0, 3000, 16000)).astype(np.int16).tobytes()   # 1 s of noise = speech
    quiet = np.zeros(16000, dtype=np.int16).tobytes()                 # 1 s silence
    assert vad.feed(quiet) is None
    assert vad.feed(loud) is None                                     # still speaking
    utt = vad.feed(quiet)
    assert utt is not None and len(utt) >= 16000 * 2 * 0.9
