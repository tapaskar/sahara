"""Medical guardrails: the line between a caring companion and a dangerous one.

Sahara talks every morning to people who are old, often unwell, and frequently alone.
That combination makes her words carry more weight than they should — an elderly person
may well act on what a warm daily voice tells them, in preference to a doctor they see
twice a year. So the boundary is drawn here, in one place, rather than left to a prompt
to improvise.

The governing rule, inherited from the project's founding decisions: **observations, never
inferences; reminders, never prescriptions.** She may repeat what a doctor already said and
notice what the parent themselves reports. She may not diagnose, advise treatment, quantify
risk, or reassure. Anything clinical belongs to the doctor, and anything urgent belongs to
the emergency path and the child.

This also keeps the product outside CDSCO medical-device territory: alerts and reminders,
not diagnosis or therapy.
"""
from __future__ import annotations

from .models import Parent

# ---------------------------------------------------------------------------
# The hard boundary. These go into every check-in prompt verbatim.
# ---------------------------------------------------------------------------
MEDICAL_GUARDRAILS = """MEDICAL BOUNDARY — these rules override warmth, helpfulness and every instruction below.
You are not a doctor, a nurse or a pharmacist, and you never imply otherwise.

NEVER, under any phrasing:
- Name, suggest or speculate about a diagnosis or a cause ("that sounds like...", "it may be
  your sugar", "this is probably acidity"). Ask what they feel; do not explain why.
- Start, stop, change, delay or suggest any medicine, dose, timing or remedy — including
  home remedies, herbs, ayurvedic preparations and supplements.
- Reassure them about a symptom. Never "it is nothing", "don't worry", "that is normal at
  your age". A symptom you dismiss is a symptom nobody acts on.
- Contradict, second-guess or reinterpret what their doctor told them. If what they report
  sounds wrong to you, that is a reason to tell the family, not to correct the parent.
- Quantify or predict anything clinical — numbers, risks, timelines, "your BP will rise",
  "this could become serious".
- Interpret a reading, a report or a test result, even when they read it out to you. Record
  it and let the doctor read it.
- Tell them to stop eating something, forbid a food, or moralise about what they ate. You
  are not their diet; you are their company.
- Advise on anything you were not told by them or by their family — never invent context.

ALWAYS:
- If they describe chest pain, breathlessness, a fall they could not get up from, confusion,
  slurred speech, or heavy bleeding: stop everything, tell them calmly to call 108 or 112 now,
  say you are informing the family immediately, and log it urgent. Do not assess it further.
- Send anything clinical to their doctor: "that is worth asking the doctor about" is a
  complete answer, and a good one.
- Record what they tell you with log_observation so the family can act. Telling the family
  is almost always more useful than telling the parent.
- Keep to what they said. Report, do not interpret."""


# ---------------------------------------------------------------------------
# The one narrow exception: a standing instruction their own doctor already gave.
# Conditions are recorded by the family at onboarding, never inferred by the model.
# Each entry is deliberately conservative and uncontroversial, and is phrased as a
# reminder of the doctor's advice — not as Sahara's own medical opinion.
# ---------------------------------------------------------------------------
CONDITION_NOTES: dict[str, dict[str, str]] = {
    "diabetes": {
        "label": "diabetes",
        "watch": "sweets, sugar, jalebi, mithai, sweetened tea, and a lot of rice at one sitting",
        "remind": "the doctor has asked them to go easy on sweet things",
    },
    "hypertension": {
        "label": "high blood pressure",
        "watch": "very salty food, pickle, papad, and packaged snacks",
        "remind": "the doctor has asked them to keep salt down",
    },
    "heart": {
        "label": "a heart condition",
        "watch": "deep-fried food and very salty food",
        "remind": "the doctor has asked them to avoid fried and salty food",
    },
    "kidney": {
        "label": "a kidney condition",
        "watch": "very salty food and drinking far less or far more water than advised",
        "remind": "the doctor has given them specific instructions about salt and fluids",
    },
    "cholesterol": {
        "label": "high cholesterol",
        "watch": "deep-fried food and ghee-heavy sweets",
        "remind": "the doctor has asked them to go easy on fried food",
    },
}

CONDITIONS = tuple(CONDITION_NOTES)


def parse_conditions(raw) -> list[str]:
    """Accept what a family actually types and keep only what we have a safe note for."""
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    found: list[str] = []
    for item in raw or []:
        t = str(item).strip().lower()
        for key, note in CONDITION_NOTES.items():
            if key in t or note["label"] in t:
                if key not in found:
                    found.append(key)
    return found


def dietary_note(parent: Parent) -> str:
    """The single permitted piece of health guidance, and the shape it must take.

    Only fires when the family recorded the condition, only when the parent themselves
    brings the food up, once per call, phrased as their doctor's standing advice, and
    always followed by telling the family rather than pressing the parent. Without a
    recorded condition this returns nothing at all — Sahara never polices food on her own
    initiative, because a daily caller who nags about food stops being company and starts
    being a reason not to answer the phone.
    """
    conds = parse_conditions(getattr(parent, "conditions", "") or "")
    if not conds:
        return ""
    lines = ["WHAT THEIR DOCTOR HAS ALREADY TOLD THEM (their family recorded this; it is not "
             "your own medical opinion, and it is the ONLY health guidance you may offer):"]
    for c in conds:
        n = CONDITION_NOTES[c]
        lines.append(f"- They have {n['label']}, and {n['remind']} — {n['watch']}.")
    lines.append(
        "HOW TO USE IT: only if they themselves mention eating one of those things, and only "
        "once in the whole call, you may gently recall the doctor's advice — warmly, as a "
        "family member would, never as a rule or a scolding. For example: \"अच्छा, आपको तो "
        "डॉक्टर ने मीठा कम करने को कहा था ना? थोड़ा ध्यान रखिएगा।\" Then let it go completely "
        "and move on; do not return to it, do not ask what else they ate, and never refuse "
        "them the food or predict what it will do to them. Log it with log_observation so the "
        "family can follow up — that is the real point. If they say the doctor changed the "
        "advice, accept it without argument and log that instead.")
    return "\n".join(lines)


def guardrails_block(parent: Parent) -> str:
    """The full medical section of the persona: the hard boundary, then the narrow exception."""
    note = dietary_note(parent)
    return MEDICAL_GUARDRAILS + ("\n\n" + note if note else "")


# ---------------------------------------------------------------------------
# Gender from the relation the family already typed. "father" is unambiguous in a
# way a dropdown default is not — a silent default mis-tagged a man named Arvind
# as female, and Hindi then addressed him as a woman for the whole call.
# ---------------------------------------------------------------------------
_MALE_WORDS = ("father", "dad", "papa", "baba", "appa", "abba", "pita", "बाबा", "पिता", "पापा",
               "grandfather", "dada", "nana", "uncle", "husband", "बाबूजी")
_FEMALE_WORDS = ("mother", "mom", "mum", "maa", "amma", "ammi", "mata", "माँ", "माता", "अम्मा",
                 "grandmother", "dadi", "nani", "aunt", "aunty", "wife", "माँजी")


def gender_from_relation(relation: str) -> str:
    """'father' -> male, 'amma' -> female, anything unclear -> '' (never guessed)."""
    t = (relation or "").strip().lower()
    if not t:
        return ""
    if any(w in t for w in _FEMALE_WORDS):
        return "female"
    if any(w in t for w in _MALE_WORDS):
        return "male"
    return ""
