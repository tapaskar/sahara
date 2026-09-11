"""Who Sahara thinks she is talking to. The header used to assert "their child" on its own
and the family's actual words sat 25 lines below, so a grandmother was told her grandson
was her son. What the family typed must come first and outrank everything."""
import pytest

from sahara.models import Family, Parent
from sahara.persona import checkin_prompt, child_is_to_parent, identity_block

FAM = Family(child_name="Ravi", child_phone="+91")


def _p(**kw):
    kw.setdefault("language", "hi-IN")
    return Parent(name=kw.pop("name", "Arvind"), **kw)


@pytest.mark.parametrize("relation,inverse", [
    ("mother", "child"), ("father", "child"), ("amma", "child"), ("papa", "child"),
    ("grandmother", "grandchild"), ("dadi", "grandchild"), ("nana", "grandchild"),
    ("uncle", "niece or nephew"), ("bua", "niece or nephew"),
    ("friend", "friend"), ("neighbour", "friend"), ("wife", "spouse"),
    ("", "family"), ("colleague", "family"),
])
def test_the_child_is_described_from_the_parents_side(relation, inverse):
    assert child_is_to_parent(relation) == inverse


def test_a_grandmother_is_not_told_her_grandson_is_her_son():
    p = _p(name="Kamla", relation="grandmother", gender="female")
    prompt = checkin_prompt(p, FAM)
    assert "Ravi is their grandchild" in prompt
    assert "their child Ravi" not in prompt          # the old hardcoded claim is gone


def test_the_family_notes_lead_the_prompt_and_are_marked_authoritative():
    p = _p(relation="father", gender="male", notes="Retired teacher in Pune. Widowed last year.")
    prompt = checkin_prompt(p, FAM)
    block = identity_block(p, FAM)
    assert "OVERRIDES" in block and "Never contradict it" in block
    assert "Retired teacher in Pune" in block
    # it must sit near the top, ahead of the agenda
    lines = prompt.splitlines()
    notes_at = next(i for i, l in enumerate(lines) if "Retired teacher" in l)
    agenda_at = next(i for i, l in enumerate(lines) if l.startswith("1. How did they sleep"))
    assert notes_at < agenda_at
    assert notes_at < 12, "the family's own words belong at the top, not buried"


def test_an_unstated_relationship_is_never_invented():
    p = _p(relation="", gender="")
    block = identity_block(p, FAM)
    assert "Do not guess how they are related" in block
    assert "is Ravi's" not in block


def test_gender_is_stated_as_fact_when_known_and_omitted_when_not():
    assert "They are male." in identity_block(_p(relation="father", gender="male"), FAM)
    assert "They are" not in identity_block(_p(relation="father", gender=""), FAM)


def test_a_session_code_carries_everything_the_family_typed_back(monkeypatch):
    """Clicking a session code must repopulate the form exactly as it was left, and the
    brief must say who set it up for whom, and how they are related."""
    from fastapi.testclient import TestClient

    from sahara import config
    from sahara.web.app import app

    monkeypatch.setattr(config, "DEMO", True)
    c = TestClient(app)
    v = "visitor-" + "s" * 20

    made = c.post("/api/try/start", json={
        "visitor": v, "child_name": "Ravi", "child_name_native": "रवि",
        "parent_name": "Arvind", "parent_name_native": "अरविंद", "relation": "father",
        "language": "hi-IN", "conditions": "diabetes",
        "notes": "Retired teacher in Pune.",
        "medications": [{"name": "Metformin", "when": "morning"}]}).json()

    code = made["session"]
    assert len(code) == 9 and code[4] == "-"            # readable, copyable

    got = c.get(f"/api/try/session/{code}").json()
    assert got["child_name"] == "Ravi" and got["parent_name"] == "Arvind"
    assert got["relation"] == "father" and got["gender"] == "male"
    assert got["conditions"] == "diabetes"
    assert got["notes"] == "Retired teacher in Pune."
    assert got["medications"][0]["name"] == "Metformin"
    assert got["child_name_native"] == "रवि"
    # the brief says who did this for whom, and how they are related
    assert got["summary"] == "Ravi set this up for Arvind, their father"
    assert got["headline"] == "Ravi → Arvind"

    # the code alone opens it, so a person can return on another device
    assert c.post(f"/api/try/{got['parent_id']}/voice-call?token={code}").status_code == 200
    assert c.get(f"/api/try/session/WRNG-9999").status_code == 404

    # and it shows up in that visitor's list with its code
    listed = c.get(f"/api/try/mine?visitor={v}").json()["personas"]
    assert any(row["session"] == code and row["relation"] == "father" for row in listed)


def test_each_session_keeps_its_own_memory(monkeypatch):
    from fastapi.testclient import TestClient

    from sahara import config, memory
    from sahara.web.app import app

    monkeypatch.setattr(config, "DEMO", True)
    c = TestClient(app)
    v = "visitor-" + "m" * 20
    a = c.post("/api/try/start", json={"visitor": v, "child_name": "Ravi",
                                       "parent_name": "Sushila", "relation": "mother",
                                       "language": "hi-IN"}).json()
    b = c.post("/api/try/start", json={"visitor": v, "child_name": "Ravi",
                                       "parent_name": "Arvind", "relation": "father",
                                       "language": "hi-IN"}).json()
    memory.open_loop(a["parent_id"], "making achaar", "was going to make it")
    assert "achaar" in memory.callback(a["parent_id"])
    assert "achaar" not in memory.callback(b["parent_id"])     # sessions do not bleed
