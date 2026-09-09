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
    assert got_audio > 20                                    # the 0.6 s greeting tone = 30 frames
    d = client.get(f"/api/calls/{c['id']}").json()
    assert d["status"] == "completed" and d["obs"] and d["obs"][0]["detail"] == "Offline engine heard audio"
    assert any(a["kind"] == "summary" for a in d["alerts"])
