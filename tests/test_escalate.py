"""The escalation reasoning pass and its deterministic floor. Offline here (no model),
so these exercise the safety net: the floor the model can raise but never lower."""
from sahara import escalate
from sahara.escalate import Escalation, deterministic_floor
from sahara.models import Family, Parent


def _pf():
    return Parent(id=1, family_id=1, name="Sushila Devi", language="hi-IN"), \
           Family(id=1, child_name="Ravi", child_phone="+91")


def obs(kind, detail, severity="info"):
    return {"kind": kind, "detail": detail, "severity": severity}


def parent_turn(text):
    return {"who": "parent", "text": text}


def test_chest_pain_forces_emergency():
    lvl, sig = deterministic_floor([parent_turn("मुझे सीने में दर्द हो रहा है")], [])
    assert lvl == "emergency"


def test_a_fall_floors_at_urgent_even_if_logged_only_warn():
    # exactly the call-9 miss: the voice model logged "warn", never "urgent"
    lvl, sig = deterministic_floor(
        [parent_turn("कल मैं गिर गई थी और टखने में सूजन है")],
        [obs("health", "Fell yesterday, ankle swelling", "warn")])
    assert lvl == "urgent"


def test_an_ordinary_good_call_stays_none():
    lvl, sig = deterministic_floor(
        [parent_turn("सब ठीक है, अच्छा दिन है")],
        [obs("meal", "Ate poha"), obs("medication", "Took tablet")])
    assert lvl == "none"


def test_a_warn_fact_floors_at_notify():
    lvl, sig = deterministic_floor([parent_turn("थोड़ा घुटने में दर्द है")],
                                   [obs("health", "Mild knee ache", "warn")])
    assert lvl == "notify"


def test_the_floor_can_raise_the_model_never_lowers_it():
    esc = Escalation(level="notify", reason="seems minor")           # model under-calls
    floor, sigs = deterministic_floor([parent_turn("सीने में दर्द")], [])
    raised = escalate._apply_floor(esc, floor, sigs)
    assert raised.level == "emergency"                               # hard signal wins

    esc2 = Escalation(level="emergency", reason="model already alarmed")
    kept = escalate._apply_floor(esc2, "notify", ["minor"])
    assert kept.level == "emergency"                                 # never lowered


async def test_offline_assess_returns_the_floor_with_a_message():
    p, f = _pf()
    esc = await escalate.assess([parent_turn("कल गिर गई, टखने में सूजन")],
                                [obs("health", "Fell, swelling", "warn")], p, f)
    assert esc.level == "urgent"
    assert esc.headline and esc.recommended_action                   # the child gets something to act on
