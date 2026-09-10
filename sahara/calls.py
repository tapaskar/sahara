"""Call orchestration: start a check-in, run the stream, finish with a summary.

One object per live call keeps the transcript and observations in memory while
the audio flows, and writes them to the database once, at the end.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from fastapi import WebSocket
from sqlmodel import select

from . import config, memory, notify
from .db import session
from .engine import make_engine
from .models import Alert, Call, Family, Parent, utcnow
from .persona import (CHECKIN_TOOLS, SCREEN_TOOLS, CallSummary, ScreenDecision, checkin_prompt,
                      recording_notice, screener_prompt)
from .scam import heuristic_risk, verdict
from .summarize import summarize
from .telephony import make_telephony
from .telephony.stream import bridge

log = logging.getLogger("sahara.calls")


# ------------------------------------------------------------- starting ---
async def start_checkin(parent_id: int, attempt: int = 1) -> Call:
    with session() as s:
        parent = s.get(Parent, parent_id)
        if parent is None or not parent.active:
            raise ValueError("no such active parent")
        if not parent.consent:
            raise PermissionError(f"{parent.name} has not consented; not calling")
        call = Call(parent_id=parent.id, kind="checkin", status="ringing", attempt=attempt,
                    provider=config.TELEPHONY if not config.OFFLINE else "offline",
                    engine=make_engine().name, started_at=utcnow())
        s.add(call); s.commit(); s.refresh(call)
        phone = parent.phone
    if config.OFFLINE:
        with session() as s:
            c = s.get(Call, call.id); c.status = "scheduled"; c.provider_call_id = "offline"; s.add(c); s.commit()
        return call
    try:
        sid = await make_telephony().place_call(phone, call.id)
        with session() as s:
            c = s.get(Call, call.id); c.provider_call_id = sid; s.add(c); s.commit()
    except Exception as e:
        log.exception("place_call failed: %s", e)
        with session() as s:
            c = s.get(Call, call.id); c.status = "failed"; c.ended_at = utcnow(); s.add(c); s.commit()
        raise
    return call


def start_screen(caller_number: str, parent_phone: str, provider_call_id: str) -> Call | None:
    """Inbound call to the Sahara number: find the parent it belongs to and open a screen call."""
    with session() as s:
        parent = s.exec(select(Parent).where(Parent.phone == parent_phone)).first()
        if parent is None:
            parent = s.exec(select(Parent).where(Parent.active == True)).first()  # noqa: E712 - single-parent pilots
        if parent is None:
            return None
        call = Call(parent_id=parent.id, kind="screen", status="in_progress", provider=config.TELEPHONY,
                    provider_call_id=provider_call_id, caller_number=caller_number,
                    engine=make_engine().name, started_at=utcnow())
        s.add(call); s.commit(); s.refresh(call)
        return call


# ------------------------------------------------------------- the call ---
class LiveCall:
    def __init__(self, call_id: int):
        self.call_id = call_id
        self.turns: list[dict] = []
        self.obs: list[dict] = []
        self.decision: ScreenDecision | None = None
        self._partial: dict[str, str] = {}
        with session() as s:
            self.call = s.get(Call, call_id)
            self.parent = s.get(Parent, self.call.parent_id)
            self.family = s.get(Family, self.parent.family_id)

    async def run(self, ws: WebSocket, provider_name: str) -> dict:
        provider = make_telephony(provider_name) if not config.OFFLINE else make_telephony("twilio")
        engine = make_engine()
        if self.call.kind == "screen":
            prompt, tools = screener_prompt(self.parent, self.family), SCREEN_TOOLS
            opening = "Answer the call and ask who is calling."
        else:
            memory.ensure_seeded(self.parent, self.family.child_name)
            brief = memory.briefing(self.parent.id)
            memory.mark_used(self.parent.id, brief)
            prompt, tools = checkin_prompt(self.parent, self.family, brief), CHECKIN_TOOLS
            opening = f"Begin with the recording notice, then greet {self.parent.name} by name."
        with session() as s:
            c = s.get(Call, self.call_id); c.status = "in_progress"; c.engine = engine.name; s.add(c); s.commit()
        await engine.start(prompt, tools, self.parent.language, opening)
        stats = await bridge(ws, provider, engine, self.on_transcript, self.on_tool_call,
                             on_turn_end=self.on_turn_end)
        await self.finish(stats)
        return stats

    async def on_transcript(self, who: str, text: str, final: bool):
        # The Live API streams both sides in fragments: the parent's with a finished
        # flag, Sahara's in word-sized chunks closed by turn_complete. Merge either
        # into one turn, and let the chunks' own spacing stand — Devanagari joined
        # with an invented space reads as one long word.
        if self.turns and self.turns[-1]["who"] == who and not self.turns[-1].get("final", True):
            prev = self.turns[-1]["text"]
            sep = "" if (not prev or prev[-1].isspace() or (text and text[0].isspace())) else " "
            self.turns[-1]["text"] = (prev + sep + text).strip()
            self.turns[-1]["final"] = final
        else:
            self.turns.append({"who": who, "text": text.strip(), "final": final})

    async def on_turn_end(self):
        """A spoken turn finished; stop merging into it."""
        if self.turns:
            self.turns[-1]["final"] = True

    async def on_tool_call(self, tc: dict) -> dict:
        name, args = tc["name"], tc.get("args", {})
        if name == "log_observation":
            kind, detail = args.get("kind", "other"), args.get("detail", "")
            self.obs.append({"kind": kind, "detail": detail,
                             "severity": args.get("severity", "info")})
            if kind == "health" and detail and self.call.kind == "checkin":
                # a symptom is only useful if it can be compared with last week's
                try:
                    memory.remember(self.parent.id, "health_thread", detail[:60], detail,
                                    call_id=self.call_id)
                except Exception as e:
                    log.warning("health thread write failed: %s", e)
            return {"ok": True}
        if name in ("remember_person", "remember_fact", "open_loop", "close_loop"):
            return self._remember(name, args)
        if name == "decide":
            self.decision = ScreenDecision(**{k: args.get(k, d) for k, d in
                                              ScreenDecision().model_dump().items()})
            # heuristics never lower the model's risk, only raise it
            risk, labels = heuristic_risk(" ".join(t["text"] for t in self.turns if t["who"] != "sahara"))
            if risk > self.decision.scam_risk:
                self.decision.scam_risk = risk
                self.decision.reason += f" Heuristics: {', '.join(labels)}."
                self.decision.action = verdict(self.decision.scam_risk)
            return {"ok": True, "action": self.decision.action}
        if name == "end_call":
            return {"ok": True}
        return {"ok": False, "error": f"unknown tool {name}"}

    def _remember(self, name: str, args: dict) -> dict:
        """Memory writes. A wrong fact is worse than a missing one, so a bad write fails
        quietly rather than poisoning the graph or derailing the call."""
        pid = self.parent.id
        try:
            if name == "remember_person":
                memory.remember(pid, "person", args.get("name", ""), args.get("detail", ""),
                                relation=args.get("relation", ""), call_id=self.call_id)
            elif name == "remember_fact":
                kind = args.get("kind", "topic")
                memory.remember(pid, kind if kind in memory.FACT_KINDS else "topic",
                                args.get("label", ""), args.get("detail", ""),
                                sensitivity=args.get("sensitivity", "normal"), call_id=self.call_id)
            elif name == "open_loop":
                memory.open_loop(pid, args.get("topic", ""), args.get("detail", ""), call_id=self.call_id)
            else:
                memory.close_loop(pid, args.get("topic", ""), args.get("outcome", ""))
        except Exception as e:
            log.warning("memory write %s failed: %s", name, e)
            return {"ok": False}
        return {"ok": True}

    async def finish(self, stats: dict):
        ended = utcnow()
        with session() as s:
            c = s.get(Call, self.call_id)
            c.ended_at = ended
            c.duration_s = int((ended - (c.started_at or ended)).total_seconds())
            c.transcript = json.dumps([{k: v for k, v in t.items() if k != "final"} for t in self.turns], ensure_ascii=False)
            c.observations = json.dumps(self.obs, ensure_ascii=False)
            answered = stats.get("frames_in", 0) > 50 or bool(self.turns)
            if c.kind == "screen":
                d = self.decision or ScreenDecision(action="message", reason="caller gave no purpose")
                c.summary = d.model_dump_json(); c.status = "completed"
            else:
                c.status = "completed" if answered else "no_answer"
            s.add(c); s.commit()
        if self.call.kind == "screen":
            await self._notify_screen()
        elif answered:
            await self._notify_checkin()

    async def _notify_checkin(self):
        summary = await summarize(self.turns, self.obs, self.parent, self.family)
        with session() as s:
            c = s.get(Call, self.call_id); c.summary = summary.model_dump_json(); s.add(c); s.commit()
        channel, ok = await notify.send_whatsapp(self.family.child_phone, summary.child_message)
        with session() as s:
            s.add(notify.alert_row(self.parent.id, self.call_id, "summary", "info", summary.child_message, channel, ok))
            urgent = [o for o in self.obs if o.get("severity") == "urgent"]
            for o in urgent:
                msg = f"URGENT from {self.parent.name}'s call: {o['detail']}. Please call them now."
                ch, ok2 = await notify.send_whatsapp(self.family.child_phone, msg)
                s.add(notify.alert_row(self.parent.id, self.call_id, o.get("kind", "health"), "urgent", msg, ch, ok2))
            for m in summary.scam_mentions:
                s.add(notify.alert_row(self.parent.id, self.call_id, "scam", "warn", m, channel, ok))
            s.commit()

    async def _notify_screen(self):
        d = self.decision or ScreenDecision()
        if d.action == "block" or d.scam_risk >= 0.6:
            msg = (f"Sahara blocked a suspicious call to {self.parent.name} from {self.call.caller_number or 'unknown'}: "
                   f"{d.purpose or 'no stated purpose'} (risk {d.scam_risk:.0%}). {d.reason}")
            ch, ok = await notify.send_whatsapp(self.family.child_phone, msg)
            with session() as s:
                s.add(notify.alert_row(self.parent.id, self.call_id, "scam", "warn", msg, ch, ok)); s.commit()
        elif d.action == "message":
            msg = f"Message for {self.parent.name} from {d.caller_name or self.call.caller_number or 'a caller'}: {d.purpose}"
            ch, ok = await notify.send_whatsapp(self.family.child_phone, msg)
            with session() as s:
                s.add(notify.alert_row(self.parent.id, self.call_id, "need", "info", msg, ch, ok)); s.commit()


# ------------------------------------------------------------ callbacks ---
async def mark_status(call_id: int, status: str):
    with session() as s:
        c = s.get(Call, call_id)
        if c is None or c.status in ("completed",):
            return
        c.status = status; c.ended_at = c.ended_at or utcnow(); s.add(c); s.commit()
        parent = s.get(Parent, c.parent_id); family = s.get(Family, parent.family_id)
        attempt, pid = c.attempt, parent.id
    if status == "no_answer" and attempt >= config.MAX_ATTEMPTS:
        msg = f"{parent.name} did not answer Sahara's call today ({attempt} attempts). You may want to call."
        ch, ok = await notify.send_whatsapp(family.child_phone, msg)
        with session() as s:
            s.add(notify.alert_row(pid, call_id, "no_answer", "warn", msg, ch, ok)); s.commit()


def due_retries(now: datetime) -> list[Call]:
    cutoff = now - timedelta(minutes=config.RETRY_AFTER_MINUTES)
    with session() as s:
        calls = s.exec(select(Call).where(Call.kind == "checkin", Call.status == "no_answer",
                                          Call.attempt < config.MAX_ATTEMPTS)).all()
        out = []
        for c in calls:
            if (c.ended_at or c.created_at) <= cutoff and not s.exec(
                    select(Call).where(Call.parent_id == c.parent_id, Call.id > c.id)).first():
                out.append(c)
        return out


# ---------------------------------------------------------- simulation ---
async def simulate_checkin(parent_id: int, parent_lines: list[str]) -> Call:
    """Offline: a scripted parent, the rule-based summary, a console WhatsApp. Exercises the
    whole data path without audio, telephony or Gemini."""
    with session() as s:
        parent = s.get(Parent, parent_id); family = s.get(Family, parent.family_id)
        call = Call(parent_id=parent.id, kind="checkin", status="in_progress", provider="offline",
                    engine="simulated", started_at=utcnow())
        s.add(call); s.commit(); s.refresh(call)
    lc = LiveCall(call.id)
    await lc.on_transcript("sahara", recording_notice(parent, family), True)
    for line in parent_lines:
        await lc.on_transcript("parent", line, True)
        low = line.lower()
        if any(w in low for w in ("दवा", "dawa", "medicine", "tablet", "goli")):
            taken = not any(w in low for w in ("नहीं", "nahi", "not", "bhool", "भूल"))
            lc.obs.append({"kind": "medication", "detail": "Took medicines" if taken else "Missed medicines",
                           "severity": "info" if taken else "warn"})
        if any(w in low for w in ("दर्द", "dard", "pain", "chakkar", "चक्कर", "गिर", "gir gaya")):
            lc.obs.append({"kind": "health", "detail": f"Reported: {line}", "severity": "warn"})
        if any(w in low for w in ("अकेला", "akela", "lonely", "udaas", "उदास")):
            lc.obs.append({"kind": "mood", "detail": "Feels lonely", "severity": "warn"})
        risk, labels = heuristic_risk(low)
        if risk >= 0.4:
            lc.obs.append({"kind": "scam", "detail": f"Parent described a call: {', '.join(labels)}", "severity": "urgent" if risk > 0.7 else "warn"})
        await lc.on_transcript("sahara", "(offline reply)", True)
    await lc.finish({"frames_in": 999, "ended_by": "agent", "seconds": 120})
    with session() as s:
        return s.get(Call, call.id)
