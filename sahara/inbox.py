"""The reply loop: what happens when the family writes back.

The daily summary used to be a receipt — one-way, unanswerable. Month-two churn is the
payer concluding that receipt is inert. A reply turns it into a conversation with proof:
"ask Amma about the wedding" tonight becomes her answer in tomorrow's summary, which is
personally verified evidence the assistant really talks to her. The same channel absorbs
the jobs that were operator tickets: corrections, a new medicine, pausing for travel.

Parsing is deliberately deterministic-first: a family's commands are short and follow a
handful of shapes, and a mis-parsed "pause" that keeps calling a grieving house is worse
than asking them to rephrase. The model is only consulted for messages the rules cannot
place, and its verdict can only file a note — never mutate anything.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from sqlmodel import select

from . import memory, notify
from .db import session
from .models import Family, Parent

log = logging.getLogger("sahara.inbox")


def _digits(number: str) -> str:
    return re.sub(r"\D", "", number or "")[-10:]          # compare on the last 10 digits


def find_family(from_number: str) -> tuple[Family, Parent] | None:
    """The sender must be a known family's number; strangers get nothing at all."""
    frm = _digits(from_number)
    if not frm:
        return None
    with session() as s:
        fams = sorted(s.exec(select(Family)).all(), key=lambda f: f.id or 0, reverse=True)
        for fam in fams:                       # newest first: a re-onboarded family wins
            if _digits(fam.child_phone) == frm:
                parent = s.exec(select(Parent).where(Parent.family_id == fam.id,
                                                     Parent.active == True)).first()  # noqa: E712
                if parent:
                    return fam, parent
    return None


# --------------------------------------------------------------- parsing ---
_PAUSE_WORD = re.compile(r"\b(pause|hold|stop|रोक|band)\b", re.I)
_NUMBER = re.compile(r"(\d+)")
_WEEKS = re.compile(r"week|hafta|हफ़्ता|हफ्ता", re.I)
_RESUME = re.compile(r"\b(resume|restart|start again|shuru|शुरू|चालू)\b", re.I)
_ASK = re.compile(r"^(ask|pucho|पूछ(?:ो|ना)?|ask her|ask him|ask them)\b[:,]?\s*(.+)", re.I | re.S)
_MED = re.compile(r"^(medicine|med|dawa|दवा)\b[:,]?\s*(.+)", re.I | re.S)
_FORGET = re.compile(r"^(forget|remove|delete|भूल)\b[:,]?\s*(.+)", re.I | re.S)


def act_on(text: str, fam: Family, parent: Parent) -> str:
    """Apply one reply and answer it. Every branch confirms exactly what it did — a silent
    success is indistinguishable from a lost message."""
    t = (text or "").strip()
    low = t.lower()

    m = _ASK.match(t)
    if m:
        topic = m.group(2).strip().rstrip("?.!")[:80]
        memory.open_loop(parent.id, topic, f"{fam.child_name} asked to bring this up")
        return (f"I will ask {parent.name} about that on tomorrow's call and tell you "
                f"what they say.")

    if _RESUME.search(low):
        with session() as s:
            p = s.get(Parent, parent.id); p.pause_until = None; s.add(p); s.commit()
        return f"Calls to {parent.name} will resume from tomorrow morning."

    if _PAUSE_WORD.search(low):
        num = _NUMBER.search(low)
        n = int(num.group(1)) if num else 3
        days = n * 7 if _WEEKS.search(low) else n
        days = max(1, min(days, 60))
        until = date.today() + timedelta(days=days)
        with session() as s:
            p = s.get(Parent, parent.id); p.pause_until = until; s.add(p); s.commit()
        return (f"Understood — no calls to {parent.name} until {until.strftime('%d %b')}. "
                f"Reply 'resume' to start earlier.")

    m = _MED.match(t)
    if m:
        body = m.group(2).strip()
        name = body.split(",")[0].strip()[:60]
        when = (body.split(",")[1].strip() if "," in body else "daily")[:30]
        with session() as s:
            p = s.get(Parent, parent.id)
            meds = p.meds(); meds.append({"name": name, "when": when})
            import json as _json
            p.medications = _json.dumps(meds); s.add(p); s.commit()
        memory.remember(parent.id, "medication", name, f"taken {when}",
                        confidence=1.0, corrected_by="child")
        return f"Noted: {name} ({when}). I will ask about it by name from tomorrow."

    m = _FORGET.match(t)
    if m:
        label = m.group(2).strip().rstrip(".")[:80]
        removed = memory.forget(parent.id, label)
        if removed:
            return f"Forgotten — I will not mention '{label}' again."
        return (f"I could not find '{label}' in what I remember. Reply 'memory' to see "
                f"the list, or give the exact words from the summary.")

    if low in ("memory", "yaad", "याद"):
        nodes = [n for n in memory.graph(parent.id)["nodes"]
                 if n["status"] == "active" and n["label_key"] != "background"]
        if not nodes:
            return f"I have not recorded anything from calls with {parent.name} yet."
        lines = [f"- {n['label']}" + (f": {n['detail']}" if n['detail'] and n['detail'] != n['label'] else "")
                 for n in nodes[:10]]
        return "What I remember:\n" + "\n".join(lines) + "\nReply 'forget <thing>' to remove one."

    # nothing matched: file it for tomorrow rather than guessing at a mutation
    memory.open_loop(parent.id, t[:60], f"{fam.child_name} wrote: {t[:200]}")
    return (f"I've noted it and will keep it in mind for {parent.name}'s next call. "
            f"You can also say: 'ask <question>', 'medicine: <name>, <when>', "
            f"'pause <n> days', 'resume', 'memory', or 'forget <thing>'.")


async def handle(from_number: str, text: str) -> str | None:
    """Route one inbound WhatsApp message. Returns the reply sent, or None for strangers."""
    found = find_family(from_number)
    if not found:
        log.info("inbound from unknown number ignored")
        return None
    fam, parent = found
    try:
        reply = act_on(text, fam, parent)
    except Exception as e:
        log.exception("inbound handling failed: %s", e)
        reply = "Something went wrong on my side — please try that again."
    await notify.send_whatsapp(fam.child_phone, reply)
    return reply
