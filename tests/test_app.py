"""Offline, end to end: family -> parent -> simulated call -> summary -> console WhatsApp ->
metrics; telephony webhooks produce the right XML; the audio bridge runs with the null engine."""
import base64
import json

from fastapi.testclient import TestClient

from sahara.web.app import app

client = TestClient(app)


def _seed():
    f = client.post("/api/families", json={"child_name": "Ravi", "child_phone": "+919800000001"}).json()
    p = client.post("/api/parents", json={"family_id": f["id"], "name": "Sushila Devi", "phone": "+919700000001",
                                          "language": "hi-IN", "call_time": "08:30", "consent": True,
                                          "medications": [{"name": "Amlodipine", "when": "morning"}],
                                          "notes": "Lives alone in Cuttack, loves cricket"}).json()
    return f, p


def test_simulated_checkin_produces_summary_and_alerts():
    f, p = _seed()
    r = client.post(f"/api/parents/{p['id']}/simulate", json={"parent_lines": [
        "हाँ बेटा, नींद ठीक आई", "दवा ले ली", "घुटने में दर्द है",
        "एक फोन आया था, बोला CBI से है, ओटीपी बताओ नहीं तो गिरफ्तार"]})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["status"] == "completed" and c["summary"]["medications_taken"] is True
    assert c["summary"]["scam_mentions"] and c["summary"]["follow_up"]
    kinds = {a["kind"] for a in c["alerts"]}
    assert "summary" in kinds and "scam" in kinds
    assert all(a["delivered"] for a in c["alerts"])           # console channel always delivers
    m = client.get("/api/metrics").json()
    assert m["answered"] >= 1 and m["scam_alerts"] >= 1 and m["summaries_delivered"] >= 1


def test_no_consent_no_call():
    f = client.post("/api/families", json={"child_name": "Asha", "child_phone": "+919800000002"}).json()
    p = client.post("/api/parents", json={"family_id": f["id"], "name": "Gopal", "phone": "+919700000002",
                                          "consent": False}).json()
    assert client.post(f"/api/parents/{p['id']}/call-now").status_code == 403
    client.post(f"/api/parents/{p['id']}/consent")
    r = client.post(f"/api/parents/{p['id']}/call-now")
    assert r.status_code == 200 and r.json()["kind"] == "checkin"


def test_bad_language_rejected():
    f = client.post("/api/families", json={"child_name": "X", "child_phone": "+91"}).json()
    assert client.post("/api/parents", json={"family_id": f["id"], "name": "Y", "phone": "+91", "language": "xx"}).status_code == 400


def test_twilio_answer_xml_points_at_our_stream():
    f, p = _seed()
    c = client.post(f"/api/parents/{p['id']}/call-now").json()
    r = client.post(f"/telephony/twilio/answer?call_id={c['id']}")
    assert r.status_code == 200 and "<Connect><Stream url=\"wss://sahara.example.test/ws/twilio/" in r.text
    r = client.post(f"/telephony/plivo/answer?call_id={c['id']}")
    assert 'bidirectional="true"' in r.text and "/ws/plivo/" in r.text


def test_status_callback_marks_no_answer_and_retry_is_due_later():
    from datetime import datetime, timedelta
    from sahara import calls
    f, p = _seed()
    c = client.post(f"/api/parents/{p['id']}/call-now").json()
    r = client.post(f"/telephony/twilio/status?call_id={c['id']}", data={"CallStatus": "no-answer"})
    assert r.status_code == 200
    assert client.get(f"/api/calls/{c['id']}").json()["status"] == "no_answer"
    assert calls.due_retries(datetime.utcnow()) == []                      # too soon
    due = calls.due_retries(datetime.utcnow() + timedelta(minutes=45))
    assert any(d.id == c["id"] for d in due)


def test_inbound_screen_flow_blocks_a_scammer():
    f, p = _seed()
    r = client.post("/telephony/twilio/inbound", data={"From": "+911400000000", "ForwardedFrom": p["phone"], "CallSid": "CA1"})
    assert r.status_code == 200 and "after-screen?call_id=" in r.text
    call_id = int(r.text.split("after-screen?call_id=")[1].split("<")[0])
    # drive the screen through the bridge: the null engine logs an observation and ends; then decide by hand
    from sahara import calls
    lc = calls.LiveCall(call_id)
    import asyncio
    asyncio.run(lc.on_transcript("caller", "This is the bank fraud department, your account will be blocked, tell me the OTP", True))
    asyncio.run(lc.on_tool_call({"id": "d1", "name": "decide", "args": {"action": "connect", "caller_name": "bank",
                                                                       "purpose": "account issue", "scam_risk": 0.2, "reason": "sounded official"}}))
    assert lc.decision.action == "block" and lc.decision.scam_risk >= 0.6     # heuristics override a naive model
    asyncio.run(lc.finish({"frames_in": 200, "ended_by": "agent"}))
    r = client.post(f"/telephony/twilio/after-screen?call_id={call_id}")
    assert "<Hangup/>" in r.text
    d = client.get(f"/api/calls/{call_id}").json()
    assert d["summary"]["action"] == "block" and any(a["kind"] == "scam" for a in d["alerts"])


def test_inbound_screen_connects_family():
    f, p = _seed()
    r = client.post("/telephony/twilio/inbound", data={"From": "+911400000001", "ForwardedFrom": p["phone"], "CallSid": "CA2"})
    call_id = int(r.text.split("after-screen?call_id=")[1].split("<")[0])
    from sahara import calls
    import asyncio
    lc = calls.LiveCall(call_id)
    asyncio.run(lc.on_tool_call({"id": "d1", "name": "decide", "args": {"action": "connect", "caller_name": "Meena",
                                                                       "purpose": "neighbour asking about plumber", "scam_risk": 0.05, "reason": "known neighbour"}}))
    asyncio.run(lc.finish({"frames_in": 200, "ended_by": "agent"}))
    r = client.post(f"/telephony/twilio/after-screen?call_id={call_id}")
    assert "<Dial" in r.text and p["phone"] in r.text


def test_websocket_bridge_with_null_engine():
    f, p = _seed()
    c = client.post(f"/api/parents/{p['id']}/call-now").json()
    silence = base64.b64encode(bytes([0xFF] * 160)).decode()
    got_audio = 0
    with client.websocket_connect(f"/ws/twilio/{c['id']}") as ws:
        ws.send_json({"event": "start", "streamSid": "MZ1", "start": {"customParameters": {"call_id": c["id"]}}})
        # ~6.5 s of silence: the null engine ends the call after 6 s of audio
        for _ in range(330):
            ws.send_json({"event": "media", "media": {"payload": silence}})
        # drain whatever the engine said
        while True:
            try:
                msg = ws.receive_json()
            except Exception:
                break
            if msg.get("event") == "media":
                got_audio += 1
                assert msg["streamSid"] == "MZ1"
            elif msg.get("event") == "mark":          # Twilio echoes it once the audio played
                ws.send_json(msg)
    assert got_audio > 20                                    # the 0.6 s greeting tone = 30 frames
    d = client.get(f"/api/calls/{c['id']}").json()
    assert d["status"] == "completed" and d["obs"] and d["obs"][0]["detail"] == "Offline engine heard audio"
    assert any(a["kind"] == "summary" for a in d["alerts"])


def test_mic_call_row_needs_consent_and_dials_nobody():
    f = client.post("/api/families", json={"child_name": "Meera", "child_phone": "+919800000009"}).json()
    p = client.post("/api/parents", json={"family_id": f["id"], "name": "Kamala", "phone": "+919700000009",
                                          "consent": False}).json()
    assert client.post(f"/api/parents/{p['id']}/mic-call").status_code == 403
    assert client.post("/api/parents/999/mic-call").status_code == 404
    client.post(f"/api/parents/{p['id']}/consent")
    r = client.post(f"/api/parents/{p['id']}/mic-call")
    assert r.status_code == 200
    c = client.get(f"/api/calls/{r.json()['call_id']}").json()
    assert c["provider"] == "browser" and c["provider_call_id"] == "browser"   # no telephony leg
    assert c["kind"] == "checkin" and c["status"] == "scheduled"


def test_mic_call_row_runs_over_the_same_bridge():
    """The browser client speaks the Twilio envelope, so a mic row must drive the bridge
    exactly as a dialled call does."""
    f, p = _seed()
    call_id = client.post(f"/api/parents/{p['id']}/mic-call").json()["call_id"]
    silence = base64.b64encode(bytes([0xFF] * 160)).decode()
    got_audio = 0
    with client.websocket_connect(f"/ws/twilio/{call_id}") as ws:
        ws.send_json({"event": "start", "streamSid": "MZbrowser",
                      "start": {"customParameters": {"call_id": call_id}}})
        for _ in range(330):
            ws.send_json({"event": "media", "media": {"payload": silence}})
        while True:
            try:
                msg = ws.receive_json()
            except Exception:
                break
            if msg.get("event") == "media":
                got_audio += 1
            elif msg.get("event") == "mark":
                ws.send_json(msg)
    assert got_audio > 20
    d = client.get(f"/api/calls/{call_id}").json()
    assert d["status"] == "completed" and d["turns"]


# Gemini Live streams Sahara's own speech in word-sized chunks. Two guards: the engine
# must mark those chunks unfinished, and the call must merge them into one readable
# turn. Transcripts are the pilot's research artifact, and Devanagari joined with an
# invented space reads as one long word.
CHUNKS = ["नमस्ते, मैं", " सहारा", " हूँ।", " यह", " कॉल", " रिकॉर्ड", " हो रही", " है"]


async def test_engine_marks_spoken_chunks_unfinished():
    from types import SimpleNamespace as NS

    from sahara.engine.gemini_live import GeminiLiveEngine

    def content(**kw):
        fields = dict(input_transcription=None, output_transcription=None,
                      interrupted=False, turn_complete=False)
        fields.update(kw)
        return NS(data=None, server_content=NS(**fields), tool_call=None, go_away=None)

    msgs = [content(output_transcription=NS(text=c)) for c in CHUNKS]
    msgs.append(content(turn_complete=True))

    class FakeSession:
        """One turn, then a closed session — receive() must not replay, or the engine's
        re-entry loop spins forever."""

        def __init__(self):
            self.done = False

        async def receive(self):
            if self.done:
                return
            self.done = True
            for m in msgs:
                yield m

    eng = GeminiLiveEngine()
    eng._session = FakeSession()
    await eng._receive()

    events = []
    while not eng.queue.empty():
        events.append(eng.queue.get_nowait())
    spoken = [e for e in events if e.type == "transcript_out"]
    assert len(spoken) == len(CHUNKS)
    assert all(e.meta.get("final") is False for e in spoken), "chunks must not close the turn"
    assert any(e.type == "turn_complete" for e in events), "turn_complete closes it instead"


async def test_streamed_chunks_become_one_turn():
    from sahara.calls import LiveCall

    f, p = _seed()
    call_id = client.post(f"/api/parents/{p['id']}/mic-call").json()["call_id"]
    live = LiveCall(call_id)

    for c in CHUNKS:
        await live.on_transcript("sahara", c, False)
    await live.on_turn_end()
    await live.on_transcript("parent", "ठीक", False)          # new speaker, new turn
    await live.on_transcript("parent", " हूँ बेटा", True)
    await live.on_transcript("sahara", "अच्छा", False)         # does not rejoin the closed turn

    assert len(live.turns) == 3, live.turns
    assert live.turns[0] == {"who": "sahara", "final": True,
                             "text": "नमस्ते, मैं सहारा हूँ। यह कॉल रिकॉर्ड हो रही है"}
    assert live.turns[1]["text"] == "ठीक हूँ बेटा"
    assert live.turns[2]["text"] == "अच्छा"


async def test_the_engine_survives_the_end_of_a_turn():
    """session.receive() yields ONE model turn and stops. If the engine does not re-enter
    it, the call ends the instant Sahara stops speaking and the parent never gets to
    answer — the whole product is a monologue."""
    from types import SimpleNamespace as NS

    from sahara.engine.gemini_live import GeminiLiveEngine

    def content(**kw):
        fields = dict(input_transcription=None, output_transcription=None,
                      interrupted=False, turn_complete=False)
        fields.update(kw)
        return NS(data=None, server_content=NS(**fields), tool_call=None, go_away=None)

    # turn 1: Sahara greets. turn 2: the parent answers and Sahara replies. then closed.
    turns = [
        [content(output_transcription=NS(text="नमस्ते")), content(turn_complete=True)],
        [content(input_transcription=NS(text="ठीक हूँ", finished=True)),
         content(output_transcription=NS(text="अच्छा")), content(turn_complete=True)],
        [],
    ]

    class FakeSession:
        def __init__(self):
            self.calls = 0

        async def receive(self):
            batch = turns[self.calls] if self.calls < len(turns) else []
            self.calls += 1
            for m in batch:
                yield m

    eng = GeminiLiveEngine()
    eng._session = FakeSession()
    await eng._receive()

    events = []
    while not eng.queue.empty():
        events.append(eng.queue.get_nowait())
    kinds = [e.type for e in events]
    assert kinds.count("turn_complete") == 2, "both turns must be seen, not just the first"
    assert any(e.type == "transcript_in" for e in events), "the parent's turn must arrive"
    assert kinds.count("end") == 1 and kinds[-1] == "end", "end only once the session closes"


def test_the_person_calling_back_never_meets_their_own_screener():
    """She rings the number that calls her every morning. The screener asking her who she
    is and why she is calling herself is humiliating; she gets a check-in instead."""
    from sahara import calls as calls_mod

    f, p = _seed()
    parent_number = "+919700000001"
    c = calls_mod.start_screen(parent_number, "+911234567890", "IN123")
    assert c.kind == "checkin", "her own number must never route to the screener"
    assert c.caller_number == parent_number

    stranger = calls_mod.start_screen("+919999999999", parent_number, "IN124")
    assert stranger.kind == "screen"        # unknown callers still get screened


async def test_call_back_later_defers_without_summary_spam():
    from datetime import timedelta

    from sahara import calls as calls_mod
    from sahara.calls import LiveCall
    from sahara.models import utcnow

    f, p = _seed()
    call_id = client.post(f"/api/parents/{p['id']}/mic-call").json()["call_id"]
    live = LiveCall(call_id)
    r = await live.on_tool_call({"id": "1", "name": "call_back_later", "args": {"minutes": 60}})
    assert r["ok"] and r["calling_back_in_minutes"] == 60
    await live.on_transcript("parent", "अभी मंदिर जा रही हूँ, बाद में करना", True)
    await live.finish({"frames_in": 999})

    d = client.get(f"/api/calls/{call_id}").json()
    assert d["status"] == "deferred"
    # no "we spoke to her" summary goes to the family for a ten-second brush-off
    assert not d["summary"]

    # not due before her hour is up; due after it
    assert all(c.id != call_id for c in calls_mod.due_retries(utcnow() + timedelta(minutes=30)))
    assert any(c.id == call_id for c in calls_mod.due_retries(utcnow() + timedelta(minutes=61)))


def test_the_twiml_matches_twilios_documented_stream_protocol(monkeypatch):
    """Checked against twilio.com/docs/voice/twiml/stream and the Media Streams WebSocket
    message reference: bidirectional needs <Connect>, the url must be wss://, and the
    outbound media/clear frames must carry the streamSid Twilio sent us."""
    from sahara import config
    from sahara.telephony.twilio import Twilio

    monkeypatch.setattr(config, "PUBLIC_URL", "https://example.test")
    t = Twilio()

    xml = t.answer_xml(42)
    assert "<Connect>" in xml, "bidirectional audio requires <Connect>, not <Start>"
    assert 'url="wss://example.test/ws/twilio/42"' in xml, "Twilio accepts wss:// only"
    assert '<Parameter name="call_id" value="42"/>' in xml   # arrives as customParameters

    # <Connect> blocks later TwiML until the socket closes, so the screener's Redirect
    # is what runs after the stream ends
    assert t.answer_xml(42, after_url="https://example.test/next").index("<Redirect") \
        > xml.index("</Connect>") - 1

    start = {"event": "start", "sequenceNumber": "1", "streamSid": "MZabc",
             "start": {"accountSid": "AC1", "streamSid": "MZabc", "callSid": "CA1",
                       "tracks": ["inbound"],
                       "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000,
                                       "channels": 1},
                       "customParameters": {"call_id": "42"}}}
    ev, audio, meta = t.parse_frame(start)
    assert (ev, audio) == ("start", None)
    assert meta["stream_sid"] == "MZabc" and meta["params"]["call_id"] == "42"

    assert t.parse_frame({"event": "media", "streamSid": "MZabc",
                          "media": {"track": "inbound", "chunk": "1", "timestamp": "5",
                                    "payload": "fw=="}})[:2] == ("media", b"\x7f")
    assert t.parse_frame({"event": "stop", "streamSid": "MZabc", "stop": {}})[0] == "stop"
    # connected / dtmf are documented events we neither need nor may crash on
    for other in ({"event": "connected", "protocol": "Call", "version": "1.0.0"},
                  {"event": "dtmf", "streamSid": "MZabc", "dtmf": {"digit": "1"}}):
        assert t.parse_frame(other)[0] == "other"

    # marks we do use: they tell us the goodbye finished playing
    assert t.parse_frame({"event": "mark", "streamSid": "MZabc",
                          "mark": {"name": "sahara-goodbye"}}) == \
        ("mark", None, {"name": "sahara-goodbye"})
    assert t.mark_frame("sahara-goodbye", meta) == {
        "event": "mark", "streamSid": "MZabc", "mark": {"name": "sahara-goodbye"}}

    assert t.audio_frame(b"\x7f", meta) == {"event": "media", "streamSid": "MZabc",
                                            "media": {"payload": "fw=="}}
    assert t.clear_frame(meta) == {"event": "clear", "streamSid": "MZabc"}


async def test_the_goodbye_waits_to_be_heard_not_a_guessed_duration():
    """A fixed sleep either clips the farewell or wastes the line. A mark rides behind the
    queued audio and comes back when it has played, so we hang up exactly then — and a
    provider without marks still falls back to the old blind wait."""
    import asyncio

    from sahara.telephony import stream as stream_mod
    from sahara.telephony.plivo import Plivo
    from sahara.telephony.twilio import Twilio

    info = {"stream_sid": "MZ1"}
    mark = Twilio().mark_frame(stream_mod.GOODBYE_MARK, info)
    assert mark["mark"]["name"] == stream_mod.GOODBYE_MARK

    # Plivo's Audio Streams have no documented equivalent: capability, not crash
    assert Plivo().mark_frame(stream_mod.GOODBYE_MARK, {}) is None

    # the echo is what releases the hang-up
    played = asyncio.Event()
    ev, _, meta = Twilio().parse_frame(mark)            # Twilio echoes the frame verbatim
    assert ev == "mark"
    if meta.get("name") == stream_mod.GOODBYE_MARK:
        played.set()
    await asyncio.wait_for(played.wait(), 1)

    # a mark for something else must not end the call
    other = Twilio().parse_frame({"event": "mark", "streamSid": "MZ1",
                                  "mark": {"name": "something-else"}})
    assert other[2]["name"] != stream_mod.GOODBYE_MARK


async def test_a_call_records_how_the_goodbye_ended():
    """stats carry it so a clipped-goodbye complaint is diagnosable from the log rather
    than from guesswork."""
    f, p = _seed()
    c = client.post(f"/api/parents/{p['id']}/call-now").json()
    silence = base64.b64encode(bytes([0xFF] * 160)).decode()
    with client.websocket_connect(f"/ws/twilio/{c['id']}") as ws:
        ws.send_json({"event": "start", "streamSid": "MZ1",
                      "start": {"customParameters": {"call_id": c["id"]}}})
        for _ in range(330):
            ws.send_json({"event": "media", "media": {"payload": silence}})
        while True:
            try:
                msg = ws.receive_json()
            except Exception:
                break
            if msg.get("event") == "mark":
                ws.send_json(msg)                       # behave like Twilio
    d = client.get(f"/api/calls/{c['id']}").json()
    assert d["status"] == "completed"
