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

from . import config, escalate, memory, notify, transcheck
from .db import session
from .engine import make_engine
from .engine import text_chat
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
    """Inbound call to the Sahara number: find the parent it belongs to and open a screen call.
    If the CALLER is the person themselves — she rang the number back — she must never meet
    her own screener asking who she is; she gets a warm check-in instead."""
    with session() as s:
        caller_is_parent = s.exec(select(Parent).where(
            Parent.phone == caller_number, Parent.active == True)).first()  # noqa: E712
        if caller_is_parent is not None:
            call = Call(parent_id=caller_is_parent.id, kind="checkin", status="in_progress",
                        provider=config.TELEPHONY, provider_call_id=provider_call_id,
                        caller_number=caller_number, engine=make_engine().name, started_at=utcnow())
            s.add(call); s.commit(); s.refresh(call)
            return call
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
        self.mem_writes = 0                     # in-call memory writes, for the pilot metric
        self.defer_minutes = 0                  # call_back_later: they were busy
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
            memory.ensure_seeded(self.parent, self.family.child_name_native or self.family.child_name)
            brief, ask = memory.briefing(self.parent.id), memory.callback(self.parent.id)
            memory.mark_used(self.parent.id, brief)
            prompt, tools = checkin_prompt(self.parent, self.family, brief, ask), CHECKIN_TOOLS
            if self.call.caller_number:      # she rang us — do not pretend this was scheduled
                opening = (f"{self.parent.name} has just called YOU. Say the recording notice, then "
                           f"greet them warmly by name and ask if everything is alright — they may "
                           f"simply want to talk, and that is a fine reason to call.")
            else:
                opening = f"Begin with the recording notice, then greet {self.parent.name} by name."
        with session() as s:
            c = s.get(Call, self.call_id); c.status = "in_progress"; c.engine = engine.name; s.add(c); s.commit()
        await engine.start(prompt, tools, self.parent.language, opening)
        stats = await bridge(ws, provider, engine, self.on_transcript, self.on_tool_call,
                             on_turn_end=self.on_turn_end)
        parent_turns = [t for t in self.turns if t["who"] == "parent"]
        stats["asr_flagged"] = sum(1 for t in parent_turns if "asr_flag" in t)
        stats["parent_turns"] = len(parent_turns)
        stats["memory_writes"] = self.mem_writes
        await self.finish(stats)
        return stats

    async def on_transcript(self, who: str, text: str, final: bool, meta: dict | None = None):
        # The Live API streams both sides in fragments: the parent's with a finished
        # flag, Sahara's in word-sized chunks closed by turn_complete. Merge either
        # into one turn, and let the chunks' own spacing stand — Devanagari joined
        # with an invented space reads as one long word.
        lang = (meta or {}).get("language_code")
        if self.turns and self.turns[-1]["who"] == who and not self.turns[-1].get("final", True):
            prev = self.turns[-1]["text"]
            sep = "" if (not prev or prev[-1].isspace() or (text and text[0].isspace())) else " "
            self.turns[-1]["text"] = (prev + sep + text).strip()
            self.turns[-1]["final"] = final
            if lang:
                self.turns[-1]["lang"] = lang
        else:
            turn = {"who": who, "text": text.strip(), "final": final}
            if lang:
                turn["lang"] = lang
            self.turns.append(turn)
        if final:
            self._flag_if_drifted(self.turns[-1])
            if who == "parent":
                try:
                    self._persist_turns()
                except Exception as e:
                    log.warning("mid-call transcript persist failed: %s", e)

    def _persist_turns(self):
        with session() as s:
            c = s.get(Call, self.call_id)
            c.transcript = json.dumps([{"who": t["who"], "text": t["text"]} for t in self.turns],
                                      ensure_ascii=False)
            c.observations = json.dumps(self.obs, ensure_ascii=False)
            s.add(c); s.commit()

    async def on_turn_end(self):
        """A spoken turn finished; stop merging into it, and write it down. Persisting
        mid-call is what lets a live view show the conversation as it happens rather than
        only after the line drops."""
        if self.turns:
            self.turns[-1]["final"] = True
            self._flag_if_drifted(self.turns[-1])
            try:
                self._persist_turns()
            except Exception as e:
                log.warning("mid-call transcript persist failed: %s", e)

    def _flag_if_drifted(self, turn: dict):
        """Mark a final parent turn whose transcription is inconsistent with the parent's
        language. Deterministic, and it annotates — never deletes: drifted turns marked,
        not laundered, is what the research artifact needs."""
        if turn["who"] != "parent" or "asr_flag" in turn:
            return
        flag = (transcheck.language_mismatch(turn.get("lang"), self.parent.language)
                or transcheck.check(turn["text"], self.parent.language))
        if flag:
            turn["asr_flag"] = flag

    async def on_tool_call(self, tc: dict) -> dict:
        name, args = tc["name"], tc.get("args", {})
        if name == "log_observation":
            kind, detail = args.get("kind", "other"), args.get("detail", "")
            severity = args.get("severity", "info")
            # The model re-states earlier facts when it circles back to an unanswered
            # question, so the same observation can arrive twice in one call. Keep the
            # first, but let a later mention raise the severity.
            key = (kind, memory.normalise(detail))
            prior = next((o for o in self.obs
                          if (o.get("kind"), memory.normalise(o.get("detail", ""))) == key), None)
            if prior is not None:
                if memory.SEVERITY_RANK.get(severity, 0) > memory.SEVERITY_RANK.get(prior.get("severity"), 0):
                    prior["severity"] = severity
                return {"ok": True}
            self.obs.append({"kind": kind, "detail": detail, "severity": severity})
            if detail and self.call.kind == "checkin":
                # deterministic fan-out from an in-call tool write (the only canonical
                # write path): a symptom becomes a thread so it can be compared with
                # last week's; a need becomes an open loop so tomorrow's call follows
                # it up. Severity rides along so a fall outranks a bill in the callback.
                try:
                    if kind == "health":
                        memory.remember(self.parent.id, "health_thread", detail[:60], detail,
                                        severity=severity, call_id=self.call_id)
                        self.mem_writes += 1
                    elif kind == "need":
                        memory.open_loop(self.parent.id, detail[:60], detail,
                                         severity=severity, call_id=self.call_id)
                        self.mem_writes += 1
                except Exception as e:
                    log.warning("memory fan-out for %s failed: %s", kind, e)
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
        if name == "call_back_later":
            self.defer_minutes = max(15, min(int(args.get("minutes") or 120), 360))
            return {"ok": True, "calling_back_in_minutes": self.defer_minutes}
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
        self.mem_writes += 1
        return {"ok": True}

    async def finish(self, stats: dict):
        ended = utcnow()
        with session() as s:
            c = s.get(Call, self.call_id)
            c.ended_at = ended
            c.duration_s = int((ended - (c.started_at or ended)).total_seconds())
            c.transcript = json.dumps([{k: v for k, v in t.items() if k not in ("final", "lang")}
                                       for t in self.turns], ensure_ascii=False)
            c.observations = json.dumps(self.obs, ensure_ascii=False)
            answered = stats.get("frames_in", 0) > 50 or bool(self.turns)
            if c.kind == "screen":
                d = self.decision or ScreenDecision(action="message", reason="caller gave no purpose")
                c.summary = d.model_dump_json(); c.status = "completed"
            elif self.defer_minutes:
                c.status = "deferred"; c.defer_minutes = self.defer_minutes
            else:
                c.status = "completed" if answered else "no_answer"
            s.add(c); s.commit()
        if self.call.kind == "screen":
            await self._notify_screen()
        elif answered and not self.defer_minutes:
            await self._notify_checkin()

    async def _notify_checkin(self):
        summary = await summarize(self.turns, self.obs, self.parent, self.family)
        # reasoning pass, off the critical path: decide how hard to reach the child, with the
        # whole call and earlier calls in view — the voice model's mid-call tags under-fire.
        history = memory.prior_health(self.parent.id, exclude_call_id=self.call_id)
        esc = await escalate.assess(self.turns, self.obs, self.parent, self.family, history)
        with session() as s:
            c = s.get(Call, self.call_id)
            c.summary = summary.model_dump_json()
            c.escalation = esc.model_dump_json()
            s.add(c); s.commit()
        log.info("call %s escalation: %s (%s)", self.call_id, esc.level, "; ".join(esc.signals))

        invite = f"\n\nReply with anything you want me to ask {self.parent.name} tomorrow."
        channel, ok = await notify.send_whatsapp(self.family.child_phone,
                                                 summary.child_message + invite)
        with session() as s:
            s.add(notify.alert_row(self.parent.id, self.call_id, "summary", "info",
                                   summary.child_message, channel, ok))
            # one escalation alert, driven by the reasoning pass rather than a lone urgent tag
            if esc.level in ("urgent", "emergency"):
                lead = "EMERGENCY" if esc.level == "emergency" else "URGENT"
                body = " ".join(x for x in (esc.headline, esc.recommended_action) if x) \
                    or f"{lead}: please call {self.parent.name} now."
                ch, ok2 = await notify.send_whatsapp(self.family.child_phone, f"{lead}: {body}")
                s.add(notify.alert_row(self.parent.id, self.call_id, "health", esc.level, body, ch, ok2))
            elif esc.level == "notify" and esc.headline:
                # a level that reaches nobody debases the whole vocabulary: notify goes
                # out as its own short message, not just a database row
                body = " ".join(x for x in (esc.headline, esc.recommended_action) if x)
                ch3, ok3 = await notify.send_whatsapp(self.family.child_phone, body)
                s.add(notify.alert_row(self.parent.id, self.call_id, "health", "warn",
                                       body, ch3, ok3))
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
    with session() as s:
        calls = s.exec(select(Call).where(Call.kind == "checkin",
                                          Call.status.in_(("no_answer", "deferred")),
                                          Call.attempt < config.MAX_ATTEMPTS + 1)).all()
        out = []
        for c in calls:
            wait = c.defer_minutes or config.RETRY_AFTER_MINUTES   # her "after lunch" wins
            if (c.ended_at or c.created_at) <= now - timedelta(minutes=wait) and not s.exec(
                    select(Call).where(Call.parent_id == c.parent_id, Call.id > c.id)).first():
                out.append(c)
        return out


# ---------------------------------------------------------- simulation ---
async def open_sim(parent_id: int) -> Call:
    """Start a text-simulated check-in: a real call row, seeded memory, Sahara's opening line
    already spoken — the tester types the parent's replies from there."""
    from .persona import spoken_name
    with session() as s:
        parent = s.get(Parent, parent_id)
        if parent is None or not parent.active:
            raise ValueError("no such active parent")
        family = s.get(Family, parent.family_id)
        call = Call(parent_id=parent.id, kind="checkin", status="in_progress",
                    provider="sim", engine="text", started_at=utcnow())
        s.add(call); s.commit(); s.refresh(call)
    memory.ensure_seeded(parent, family.child_name_native or family.child_name)
    lc = LiveCall(call.id)
    brief, ask = memory.briefing(parent_id), memory.callback(parent_id)
    memory.mark_used(parent_id, brief)
    lc._sim_prompt = checkin_prompt(parent, family, brief, ask)
    opening = await text_chat.respond(
        lc._sim_prompt, CHECKIN_TOOLS,
        [{"who": "parent", "text": "(The call has connected. Begin with the recording notice, "
          "then greet them by name and start warmly.)"}], lc.on_tool_call)
    opening = opening or recording_notice(parent, family)
    lc.turns.append({"who": "sahara", "text": opening.strip(), "final": True})
    lc._persist_turns()
    return s.get(Call, call.id) if False else call


async def say_sim(call_id: int, parent_line: str) -> dict:
    """One tester turn: append the parent's line, get Sahara's real reply, persist."""
    lc = LiveCall(call_id)
    if lc.call.status != "in_progress":
        raise ValueError("this call has ended")
    family = None
    with session() as s:
        parent = s.get(Parent, lc.parent.id); family = s.get(Family, parent.family_id)
    brief, ask = memory.briefing(lc.parent.id), memory.callback(lc.parent.id)
    prompt = checkin_prompt(parent, family, brief, ask)
    lc.turns = [{"who": t["who"], "text": t["text"], "final": True} for t in lc.call.turns()]
    lc.obs = list(lc.call.obs())
    lc.turns.append({"who": "parent", "text": parent_line.strip(), "final": True})
    lc._persist_turns()                        # save her line first — a model failure must not lose it
    reply = await text_chat.respond(prompt, CHECKIN_TOOLS, lc.turns, lc.on_tool_call)
    if reply.strip():
        lc.turns.append({"who": "sahara", "text": reply.strip(), "final": True})
    lc._persist_turns()
    return {"reply": reply.strip(), "facts": lc.obs}


async def close_sim(call_id: int) -> Call:
    """End the simulated call: run summary + escalation + notification, exactly as a real
    call's finish does."""
    lc = LiveCall(call_id)
    lc.turns = [{"who": t["who"], "text": t["text"], "final": True} for t in lc.call.turns()]
    lc.obs = list(lc.call.obs())
    with session() as s:
        c = s.get(Call, call_id); c.status = "completed"; c.ended_at = utcnow()
        c.duration_s = max(1, int((c.ended_at - (c.started_at or c.ended_at)).total_seconds()))
        s.add(c); s.commit()
    await lc._notify_checkin()
    return lc.call


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
