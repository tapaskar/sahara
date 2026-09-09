from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from sahara import scheduler
from sahara.web.app import app

client = TestClient(app)


def test_due_parents_by_local_time_and_once_per_day():
    f = client.post("/api/families", json={"child_name": "Z", "child_phone": "+91"}).json()
    p = client.post("/api/parents", json={"family_id": f["id"], "name": "Timed", "phone": "+919700000099",
                                          "call_time": "07:15", "consent": True}).json()
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist).replace(hour=7, minute=15)
    assert p["id"] in scheduler.due_parents(now)
    assert p["id"] not in scheduler.due_parents(now.replace(minute=16))
    client.post(f"/api/parents/{p['id']}/call-now")
    assert p["id"] not in scheduler.due_parents(now)      # already called today
