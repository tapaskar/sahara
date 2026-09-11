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
    ("", "family"), ("neighbour", "family"),
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
