"""The medical boundary. These assert the shape of what Sahara is *told*, which is the
only lever we have over what she says — so the wording is the product, and a regression
here is a safety regression, not a cosmetic one."""
import pytest

from sahara import guardrails
from sahara.guardrails import (MEDICAL_GUARDRAILS, dietary_note, gender_from_relation,
                               guardrails_block, parse_conditions)
from sahara.models import Family, Parent
from sahara.persona import checkin_prompt


def _p(**kw):
    return Parent(name="Sushila", language="hi-IN", **kw)


FAM = Family(child_name="Ravi", child_phone="+91")


@pytest.mark.parametrize("forbidden", [
    "diagnosis", "medicine, dose, timing or remedy", "Reassure them about a symptom",
    "Contradict, second-guess", "Quantify or predict", "Interpret a reading",
    "forbid a food",
])
def test_every_hard_prohibition_is_stated(forbidden):
    assert forbidden in MEDICAL_GUARDRAILS


def test_the_emergency_route_is_stated_and_is_not_an_assessment():
    assert "call 108 or 112 now" in MEDICAL_GUARDRAILS
    assert "Do not assess it further" in MEDICAL_GUARDRAILS


def test_the_boundary_reaches_every_call():
    assert "MEDICAL BOUNDARY" in checkin_prompt(_p(), FAM)


def test_no_dietary_advice_without_a_doctor_recorded_condition():
    """Sahara never polices food on her own initiative — the five-track debate refused diet
    nudging outright, and a daily caller who nags about food becomes a reason not to answer."""
    assert dietary_note(_p()) == ""
    assert dietary_note(_p(conditions="")) == ""
    assert "WHAT THEIR DOCTOR" not in checkin_prompt(_p(), FAM)


def test_a_recorded_condition_permits_one_gentle_reminder():
    note = dietary_note(_p(conditions="diabetes"))
    assert "go easy on sweet things" in note
    assert "only once in the whole call" in note
    assert "never as a rule or a scolding" in note
    assert "not your own medical opinion" in note
    # and it must route to the family rather than press the parent
    assert "log_observation" in note


def test_sugar_and_salt_are_both_carried_when_both_are_recorded():
    note = dietary_note(_p(conditions="diabetes, high blood pressure"))
    assert "sweet" in note and "salt" in note


def test_the_parent_always_outranks_us_on_what_their_doctor_said():
    assert "If they say the doctor changed the advice, accept it without argument" \
        in dietary_note(_p(conditions="diabetes"))


def test_unknown_conditions_are_ignored_rather_than_improvised():
    """We only speak to conditions we have a vetted, conservative note for."""
    assert parse_conditions("lupus, something rare") == []
    assert dietary_note(_p(conditions="lupus")) == ""
    assert parse_conditions("Diabetes; HIGH BLOOD PRESSURE") == ["diabetes", "hypertension"]


def test_the_narrow_exception_never_loosens_the_hard_boundary():
    block = guardrails_block(_p(conditions="diabetes"))
    assert block.startswith(MEDICAL_GUARDRAILS)          # prohibitions come first
    assert "never refuse them the food or predict what it will do" in block


@pytest.mark.parametrize("relation,expected", [
    ("father", "male"), ("Papa", "male"), ("dada", "male"),
    ("mother", "female"), ("amma", "female"), ("Dadi", "female"),
    ("parent", ""), ("", ""), ("guardian", ""),
])
def test_gender_follows_the_relation_rather_than_a_silent_default(relation, expected):
    """A dropdown defaulting to female tagged a man named Arvind as female, and the whole
    call addressed him as a woman. The relation word already carries the answer."""
    assert gender_from_relation(relation) == expected


def test_an_unclear_relation_is_left_unset_not_guessed():
    assert guardrails.gender_from_relation("cousin") == ""
    assert "not recorded" in __import__("sahara.persona", fromlist=["x"]).grammar_note(_p())
