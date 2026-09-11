"""The reply loop: a family's WhatsApp reply becomes an action, deterministically. A
mis-parsed 'pause' that keeps calling a grieving house is worse than asking them to
rephrase, so only explicit shapes mutate anything."""
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from sahara import inbox, memory
from sahara.db import session
from sahara.models import Parent
from sahara.web.app import app

client = TestClient(app)

_seq = [0]


def _family():
    _seq[0] += 1
    phone = f"+9198111{_seq[0]:05d}"
    f = client.post("/api/families", json={"child_name": "Ravi", "child_phone": phone}).json()
    p = client.post("/api/parents", json={"family_id": f["id"], "name": "Sushila Devi",
                                          "phone": f"+9197001{_seq[0]:05d}", "language": "hi-IN",
                                          "relation": "mother", "consent": True}).json()
    f["phone"] = phone
    return f, p


async def test_ask_becomes_tomorrows_question():
    f, p = _family()
    reply = await inbox.handle(f["phone"], "ask her about Ayaan's exam")
    assert "tomorrow" in reply
    assert "exam" in memory.callback(p["id"]).lower()


async def test_pause_and_resume_control_the_scheduler():
    from sahara.scheduler import due_parents

    f, p = _family()
    with session() as s:
        row = s.get(Parent, p["id"]); row.call_time = "08:30"; s.add(row); s.commit()

    reply = await inbox.handle(f["phone"], "pause 5 days")
    assert "no calls" in reply.lower()
    with session() as s:
        assert s.get(Parent, p["id"]).pause_until == date.today() + timedelta(days=5)

    now = datetime.now().replace(hour=8, minute=32, second=0)   # inside the catch-up window
    assert p["id"] not in due_parents(now)

    await inbox.handle(f["phone"], "resume")
    with session() as s:
        assert s.get(Parent, p["id"]).pause_until is None
    assert p["id"] in due_parents(now)


async def test_medicine_reply_updates_the_record_and_memory():
    f, p = _family()
    reply = await inbox.handle(f["phone"], "medicine: Telma 40, morning")
    assert "Telma 40" in reply
    with session() as s:
        meds = s.get(Parent, p["id"]).meds()
    assert {"name": "Telma 40", "when": "morning"} in meds
    assert any(n["label"] == "Telma 40" for n in memory.graph(p["id"])["nodes"])


async def test_forget_erases_and_memory_lists():
    f, p = _family()
    memory.remember(p["id"], "topic", "cricket", "follows every match")
    listing = await inbox.handle(f["phone"], "memory")
    assert "cricket" in listing
    gone = await inbox.handle(f["phone"], "forget cricket")
    assert "Forgotten" in gone
    assert not any(n["label"] == "cricket" for n in memory.graph(p["id"])["nodes"])


async def test_free_text_files_a_loop_never_mutates():
    f, p = _family()
    before = len(memory.graph(p["id"])["nodes"])
    reply = await inbox.handle(f["phone"], "She sounded tired on the phone yesterday na?")
    assert "next call" in reply
    nodes = memory.graph(p["id"])["nodes"]
    assert len(nodes) == before + 1 and nodes[-1]["kind"] == "open_loop"


async def test_a_stranger_gets_silence():
    _family()
    assert await inbox.handle("+919999999999", "pause 30 days") is None


def test_webhook_verification_and_inbound(monkeypatch):
    from sahara import config
    monkeypatch.setattr(config, "META_WA_VERIFY_TOKEN", "vt-123")
    ok = client.get("/whatsapp/webhook", params={"hub.mode": "subscribe",
                                                 "hub.verify_token": "vt-123",
                                                 "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/whatsapp/webhook", params={"hub.mode": "subscribe",
                                                  "hub.verify_token": "wrong"})
    assert bad.status_code == 403

    f, p = _family()
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"type": "text", "from": f["phone"], "text": {"body": "ask about the wedding"}}]}}]}]}
    r = client.post("/whatsapp/webhook", json=payload)
    assert r.status_code == 200
    assert "wedding" in memory.callback(p["id"]).lower()
