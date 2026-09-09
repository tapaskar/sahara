"""Transcript + tool observations -> CallSummary. Gemini when online, rules offline."""
from __future__ import annotations

import json
import logging

from . import config
from .models import Family, Parent
from .persona import CallSummary, language_name
from .scam import heuristic_risk

log = logging.getLogger("sahara.summarize")

MOOD_LOW = ("अकेला", "उदास", "lonely", "sad", "tired", "थक", "नींद नहीं", "not well", "ठीक नहीं", "दर्द", "pain")
MOOD_GOOD = ("अच्छा", "बढ़िया", "ठीक हूँ", "ठीक हूं", "good", "fine", "well", "खुश", "happy")


def _text(turns: list[dict], who: str | None = None) -> str:
    return " ".join(t["text"] for t in turns if who is None or t.get("who") == who)


def offline_summary(turns: list[dict], obs: list[dict], parent: Parent, family: Family) -> CallSummary:
    ptext = _text(turns, "parent").lower()
    kinds = {o.get("kind"): o for o in obs}
    def has(kind, positive=None):
        o = kinds.get(kind)
        if o is None:
            return None
        d = o.get("detail", "").lower()
        if positive is None:
            return True
        return not any(w in d for w in ("missed", "not", "नहीं", "skip"))
    mood = "unclear"
    if any(w in ptext for w in MOOD_LOW) or kinds.get("mood", {}).get("severity") in ("warn", "urgent"):
        mood = "low"
    elif any(w in ptext for w in MOOD_GOOD) or "mood" in kinds:
        mood = "good"
    health = [o["detail"] for o in obs if o.get("kind") == "health"]
    needs = [o["detail"] for o in obs if o.get("kind") == "need"]
    scams = [o["detail"] for o in obs if o.get("kind") == "scam"]
    risk, labels = heuristic_risk(ptext)
    if risk >= 0.4 and not scams:
        scams.append("Parent mentioned: " + ", ".join(labels))
    parent_turns = [t for t in turns if t.get("who") == "parent"]
    words = sum(len(t["text"].split()) for t in parent_turns)
    engagement = round(min(1.0, words / 120), 2) if parent_turns else 0.0
    social = [o["detail"] for o in obs if o.get("kind") in ("social", "other")]
    urgent = any(o.get("severity") == "urgent" for o in obs)
    follow = urgent or bool(scams) or mood == "low" or bool(needs)
    lines = [f"{parent.name}: mood {mood}, " + ("medicines taken" if has("medication", True) else
             "medicines missed" if has("medication") is False else "medicines not discussed") + "."]
    if health: lines.append("Health: " + "; ".join(health))
    if needs: lines.append("Needs: " + "; ".join(needs))
    if scams: lines.append("Scam contact reported: " + "; ".join(scams))
    if social: lines.append("Talked about: " + "; ".join(social))
    return CallSummary(mood=mood, slept_well=has("sleep", True), ate=has("meal", True),
                       medications_taken=has("medication", True), health_concerns=health, needs=needs,
                       scam_mentions=scams, highlights=social[:3], parent_initiated_topics=[],
                       engagement=engagement, follow_up=follow,
                       follow_up_reason="urgent" if urgent else ("scam" if scams else ("low mood" if mood == "low" else "")),
                       child_message="(offline) " + "\n".join(lines))


async def summarize(turns: list[dict], obs: list[dict], parent: Parent, family: Family) -> CallSummary:
    if config.OFFLINE:
        return offline_summary(turns, obs, parent, family)
    from .gemini import text_client
    prompt = f"""You are summarising Sahara's morning call with {parent.name} ({language_name(parent.language)} speaker)
for their child {family.child_name}, who reads {language_name(family.child_language) if family.child_language != 'en' else 'English'}.

Transcript (who: text):
{json.dumps(turns, ensure_ascii=False, indent=0)}

Facts the agent logged during the call:
{json.dumps(obs, ensure_ascii=False, indent=0)}

Rules: only state what is in the transcript or the facts. If medicines were not discussed, leave
medications_taken null. parent_initiated_topics are things {parent.name} brought up unprompted.
engagement: 0.2 for one-word answers, 0.8 for stories and questions back. child_message: two to four short
lines in the child's language, specific and warm, first line the most important thing, no preamble."""
    try:
        client = text_client()
        r = await client.aio.models.generate_content(
            model=config.GEMINI_TEXT_MODEL, contents=prompt,
            config={"response_mime_type": "application/json", "response_schema": CallSummary})
        return CallSummary.model_validate_json(r.text)
    except Exception as e:  # never lose a call because the summary failed
        log.exception("summary failed, using offline rules: %s", e)
        return offline_summary(turns, obs, parent, family)
