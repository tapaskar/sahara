"""Post-call escalation reasoning.

The voice model tags each fact's severity mid-conversation, while it is also talking
warmly in the parent's language and following a checklist — so it under-calls. A real
call proved it: a fall with ankle swelling was logged "warn", and no urgent alert reached
the child. This pass runs after the call, off the critical path, with the whole transcript,
the logged facts, and what earlier calls recorded — a reasoning job a text model does far
better than the voice model can mid-sentence.

The model proposes; deterministic rules dispose. A hard signal (chest pain, a fall that
can't be got up from, breathlessness, an urgent-tagged fact) sets a floor the model may
raise but can never lower — the same "only ever raise the risk" rule the scam screener uses.
The pass produces a routing decision for the child; it never diagnoses.
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from . import config
from .models import Family, Parent
from .persona import language_name, relationship_line

log = logging.getLogger("sahara.escalate")

LEVELS = ("none", "notify", "urgent", "emergency")
RANK = {lvl: i for i, lvl in enumerate(LEVELS)}


class Escalation(BaseModel):
    """How hard to reach the child, and why. A routing decision, not a diagnosis."""
    level: str = Field(description="none | notify | urgent | emergency")
    reason: str = Field(default="", description="One observational sentence: what was said, not what it means clinically")
    headline: str = Field(default="", description="One line for the child, only when level is above none")
    recommended_action: str = Field(default="", description="What the child should do, e.g. 'Call her this morning'")
    signals: list[str] = Field(default_factory=list, description="The specific things that drove the level")


# Bilingual hard-signal patterns. These set a FLOOR the reasoning model cannot go below.
# Kept deliberately narrow: a false floor costs a phone call, a missed one costs a life.
_EMERGENCY = [
    ("chest pain", "सीने में दर्द", "सीने में", "chhati", "छाती"),
    ("can't breathe", "breathless", "साँस", "सांस लेने", "dam ghut"),
    ("unconscious", "बेहोश", "behosh", "fainted", "पास आउट"),
    ("slurred", "confusion", "लकवा", "paralysis", "stroke", "मुँह टेढ़ा"),
    ("bleeding heavily", "खून बह", "bahut khoon"),
]
_URGENT = [
    ("fell", "गिर ", "gir gaya", "gir gayi", "fall"),
    ("swelling", "सूजन", "sujan"),
    ("severe pain", "तेज़ दर्द", "बहुत दर्द", "unbearable"),
    ("not eaten", "कुछ नहीं खाया", "khaya nahi", "no food"),
]


def _hit(text: str, groups) -> str | None:
    low = text.lower()
    for g in groups:
        for term in g:
            if term.lower() in low:
                return g[0]
    return None


def deterministic_floor(turns: list[dict], obs: list[dict]) -> tuple[str, list[str]]:
    """The level the situation is at least at, from hard signals alone — no model.
    Reproducible, offline, and the safety net under the reasoning pass.

    The URGENT keywords are scoped: raw transcript hits only count when the in-call model
    also logged a warn+ health/need fact — otherwise "my neighbour fell" fires a false
    URGENT to the family, and two of those in a week teach them to ignore the real one.
    EMERGENCY keywords stay unscoped on the transcript deliberately: a false emergency
    costs a phone call, a missed one can cost a life, and that asymmetry decides it.
    Levels are still only ever raised, never lowered."""
    parent_text = " ".join(t.get("text", "") for t in turns if t.get("who") == "parent")
    all_obs_text = " ".join(o.get("detail", "") for o in obs)
    # only observations the model judged to be trouble about THIS person; a social note
    # that happens to contain "fell" is a story, not an incident
    trouble_text = " ".join(o.get("detail", "") for o in obs
                            if o.get("severity") in ("warn", "urgent")
                            and o.get("kind") in ("health", "need"))
    signals: list[str] = []
    level = "none"

    em = _hit(parent_text + " " + all_obs_text, _EMERGENCY)
    if em:
        return "emergency", [f"hard signal: {em}"]

    if any(o.get("severity") == "urgent" for o in obs):
        level = "urgent"
        signals.append("a fact was logged urgent during the call")
    ur = _hit(trouble_text, _URGENT) or (trouble_text and _hit(parent_text, _URGENT))
    if ur:
        level = _max(level, "urgent")
        signals.append(f"hard signal: {ur}")
    if any(o.get("severity") == "warn" for o in obs):
        level = _max(level, "notify")
        signals.append("a fact was logged warn during the call")
    return level, signals


def _max(a: str, b: str) -> str:
    return a if RANK[a] >= RANK[b] else b


def _apply_floor(esc: Escalation, floor: str, floor_signals: list[str]) -> Escalation:
    if RANK.get(esc.level, 0) < RANK[floor]:
        esc.level = floor
        for sig in floor_signals:
            if sig not in esc.signals:
                esc.signals.append(sig)
        if not esc.reason:
            esc.reason = "Raised by a hard safety signal in the call."
    return esc


def _fallback(floor: str, signals: list[str], parent: Parent) -> Escalation:
    headline = {
        "emergency": f"Possible emergency on the call with {parent.name} — call them right now.",
        "urgent": f"Something on the call with {parent.name} needs your attention today.",
        "notify": f"Worth knowing from the call with {parent.name}.",
        "none": "",
    }[floor]
    action = {
        "emergency": f"Call now; if you cannot reach {parent.name}, call a neighbour or 108.",
        "urgent": f"Call {parent.name} this morning.",
        "notify": f"A call to {parent.name} when you can would be kind.",
        "none": "",
    }[floor]
    return Escalation(level=floor, reason="", headline=headline,
                      recommended_action=action, signals=signals)


async def assess(turns: list[dict], obs: list[dict], parent: Parent, family: Family,
                 history: str = "") -> Escalation:
    """Decide how hard to reach the child. Reasoning model when online; the deterministic
    floor is always applied on top, so vendor choice can never lower an escalation."""
    floor, floor_signals = deterministic_floor(turns, obs)

    if config.OFFLINE:
        return _fallback(floor, floor_signals, parent)

    from .gemini import text_client
    prompt = f"""You decide how urgently to alert {family.child_name} about {parent.name} after this
morning's call. You are NOT a doctor: never diagnose, never name a condition. You route a message to
someone far away who cares about them. Judge how much attention today's call needs.

WHO THESE PEOPLE ARE — use only this, never an assumption:
{relationship_line(parent, family)}

Choose exactly one level:
- none: an ordinary good call, nothing to act on.
- notify: worth knowing (low mood, a minor complaint, a small need) — a call would help.
- urgent: {family.child_name} should call {parent.name} today (a fall, new or worsening pain, swelling,
  not eating, a scam attempt, real distress).
- emergency: a possible medical emergency — {family.child_name} should call immediately (chest pain,
  breathlessness, a fall they could not get up from, signs of a stroke, heavy bleeding).

Weigh what changed since earlier calls: a brand-new fall or a WORSENING symptom escalates; the same mild
complaint mentioned for weeks does not.

Today's transcript (who: text):
{json.dumps(turns, ensure_ascii=False)}

Facts the agent logged today:
{json.dumps(obs, ensure_ascii=False)}

What earlier calls recorded (for trend — is this new or ongoing?):
{history or "nothing on record yet"}

Write headline and recommended_action in {language_name(family.child_language) if family.child_language != 'en' else 'English'},
one short line each, observational ("she said…", "he mentioned…"), never clinical. Name {parent.name} or
use the exact relationship word above — never "your mother" or "your father" unless that is the word. Leave them empty only
for level none. signals: the specific things that drove your decision."""
    try:
        client = text_client()
        r = await client.aio.models.generate_content(
            model=config.GEMINI_TEXT_MODEL, contents=prompt,
            config={"response_mime_type": "application/json", "response_schema": Escalation})
        esc = Escalation.model_validate_json(r.text)
        if esc.level not in RANK:
            esc.level = floor
    except Exception as e:  # a failed reasoning pass must never drop below the safe floor
        log.exception("escalation reasoning failed, using deterministic floor: %s", e)
        return _fallback(floor, floor_signals, parent)

    return _apply_floor(esc, floor, floor_signals)
