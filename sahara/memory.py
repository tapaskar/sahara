"""Long-term memory: a small typed knowledge graph per parent.

Written only by tool calls during a call, never by parsing the transcript afterwards.
Read as a short briefing compiled into the persona before the next call — never as a
dump of everything known. See docs/MEMORY.md for the framework and the reasoning.
"""
from __future__ import annotations

import unicodedata
from datetime import timedelta

from sqlmodel import select

from .db import session
from .models import MemoryEdge, MemoryNode, utcnow

# node kinds the tools may write; open_loop is created by its own tool
FACT_KINDS = ("preference", "routine", "place", "event", "organisation", "topic")
SENSITIVITIES = ("normal", "sensitive", "never_volunteer")

MAX_FACTS = 5          # a briefing is a handful of things, not a dossier
SEVERITY_RANK = {"info": 0, "warn": 1, "urgent": 2}
HALF_LIFE_DAYS = 45.0  # salience halves after this long without confirmation


def normalise(label: str) -> str:
    """Match key for entity resolution: case- and punctuation-insensitive, Latin accents
    folded. Indic vowel signs and the virama are letters here, not decoration: strip them
    and मंदिर becomes म द र, which would collide with unrelated words. A wrong merge is
    worse than a missed one, so we only fold what folds safely."""
    s = unicodedata.normalize("NFKD", (label or "").strip().casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))     # Latin diacritics only
    s = "".join(c if unicodedata.category(c)[0] in "LNM" or c.isspace() else " " for c in s)
    return " ".join(s.split())


# ----------------------------------------------------------------- writing ---
def remember(parent_id: int, kind: str, label: str, detail: str = "", *, relation: str = "",
             sensitivity: str = "normal", severity: str = "info", confidence: float = 0.6,
             corrected_by: str = "model", call_id: int | None = None) -> MemoryNode:
    """Upsert one fact. A confident match refreshes the node and raises its confidence
    rather than creating a duplicate; matching never crosses parent_id."""
    key = normalise(label)
    if not key:
        raise ValueError("a fact needs a label")
    if sensitivity not in SENSITIVITIES:
        sensitivity = "normal"
    with session() as s:
        node = s.exec(select(MemoryNode).where(
            MemoryNode.parent_id == parent_id, MemoryNode.kind == kind,
            MemoryNode.label_key == key, MemoryNode.status != "deleted")).first()
        if node is None:
            node = MemoryNode(parent_id=parent_id, kind=kind, label=label.strip(), label_key=key,
                              relation=relation, detail=detail, sensitivity=sensitivity,
                              severity=severity, confidence=confidence, corrected_by=corrected_by,
                              source_call_id=call_id)
        else:
            node.last_confirmed = utcnow()
            node.confidence = min(1.0, max(node.confidence, confidence) + 0.1)  # re-mention is evidence
            if detail:
                node.detail = detail
            if relation:
                node.relation = relation
            if sensitivity != "normal":
                node.sensitivity = sensitivity          # sensitivity only ever tightens
            if SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(node.severity, 0):
                node.severity = severity                # severity only ever rises
            if node.status == "closed":
                node.status = "active"
        s.add(node); s.commit(); s.refresh(node)
        return node


def link(parent_id: int, src: MemoryNode, dst: MemoryNode, kind: str, label: str = "",
         call_id: int | None = None) -> MemoryEdge:
    """Relate two of this parent's nodes. Idempotent on (src, dst, kind)."""
    with session() as s:
        edge = s.exec(select(MemoryEdge).where(
            MemoryEdge.parent_id == parent_id, MemoryEdge.src_id == src.id,
            MemoryEdge.dst_id == dst.id, MemoryEdge.kind == kind)).first()
        if edge is None:
            edge = MemoryEdge(parent_id=parent_id, src_id=src.id, dst_id=dst.id,
                              kind=kind, label=label, source_call_id=call_id)
            s.add(edge); s.commit(); s.refresh(edge)
        return edge


def open_loop(parent_id: int, topic: str, detail: str = "", call_id: int | None = None,
              severity: str = "info") -> MemoryNode:
    """Something unfinished worth asking about next time."""
    return remember(parent_id, "open_loop", topic, detail, severity=severity, call_id=call_id)


def close_loop(parent_id: int, topic: str, outcome: str = "") -> MemoryNode | None:
    """The loop was asked about, or a health thread has resolved; stop leading the call
    with it. The node is kept (status=closed), so the longitudinal history survives."""
    key = normalise(topic)
    with session() as s:
        node = s.exec(select(MemoryNode).where(
            MemoryNode.parent_id == parent_id,
            MemoryNode.kind.in_(("open_loop", "health_thread")),
            MemoryNode.label_key == key, MemoryNode.status == "active")).first()
        if node is None:
            return None
        node.status = "closed"
        node.last_confirmed = utcnow()
        if outcome:
            node.detail = outcome
        s.add(node); s.commit(); s.refresh(node)
        return node


def forget(parent_id: int, label: str, reason: str = "") -> int:
    """Real deletion, parent-initiated: the node and its edges go. Returns rows removed."""
    key = normalise(label)
    removed = 0
    with session() as s:
        for node in s.exec(select(MemoryNode).where(
                MemoryNode.parent_id == parent_id, MemoryNode.label_key == key)).all():
            for e in s.exec(select(MemoryEdge).where(
                    MemoryEdge.parent_id == parent_id,
                    (MemoryEdge.src_id == node.id) | (MemoryEdge.dst_id == node.id))).all():
                s.delete(e); removed += 1
            s.delete(node); removed += 1
        s.commit()
    return removed


# ----------------------------------------------------------------- reading ---
def _salience(node: MemoryNode, now) -> float:
    """Recency and confidence lift a fact; having used it recently pushes it down, so
    Sahara does not become the relative who tells the same story every visit."""
    age_days = max(0.0, (now - node.last_confirmed).total_seconds() / 86400)
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
    pinned = 0.3 if node.corrected_by == "child" else 0.0
    return 1.4 * recency + 1.0 * node.confidence + pinned - 0.25 * node.times_used


def briefing(parent_id: int, max_facts: int = MAX_FACTS) -> str:
    """The ~300-token block compiled into the persona before a call: a few salient facts,
    exactly one callback, and what must not be raised. Never the whole graph."""
    now = utcnow()
    with session() as s:
        nodes = s.exec(select(MemoryNode).where(
            MemoryNode.parent_id == parent_id, MemoryNode.status == "active")).all()
        edges = s.exec(select(MemoryEdge).where(MemoryEdge.parent_id == parent_id)).all()
    if not nodes:
        return ""

    by_id = {n.id: n for n in nodes}
    rel: dict[int, list[str]] = {}
    for e in edges:
        a, b = by_id.get(e.src_id), by_id.get(e.dst_id)
        if a and b:
            rel.setdefault(a.id, []).append(f"{e.kind.lower().replace('_', ' ')} {b.label}")

    hidden = [n for n in nodes if n.sensitivity in ("sensitive", "never_volunteer")]
    in_callback = _callback_covered(parent_id)      # already surfaced in the opening
    threads = [n for n in nodes if n.kind == "health_thread" and n.id not in in_callback]
    facts = [n for n in nodes if n.kind not in ("open_loop", "health_thread")
             and n.sensitivity == "normal"]

    facts.sort(key=lambda n: _salience(n, now), reverse=True)
    chosen = facts[:max_facts]

    lines: list[str] = []
    if chosen:
        lines.append("WHAT YOU ALREADY KNOW ABOUT THEM (from earlier calls — weave it in naturally, "
                     "never recite this list):")
        for n in chosen:
            who = f" ({n.relation})" if n.relation else ""
            extra = "; ".join(rel.get(n.id, []))
            bits = " — ".join(x for x in (n.detail, extra) if x)
            lines.append(f"- {n.label}{who}{': ' + bits if bits else ''}")
    if threads:
        lines.append("OPEN HEALTH THREADS (ask how it is now; do not diagnose):")
        for n in sorted(threads, key=lambda n: n.last_confirmed, reverse=True)[:3]:
            days = (now - n.last_confirmed).days
            when = "mentioned today" if days == 0 else f"last mentioned {days} day{'s' if days != 1 else ''} ago"
            lines.append(f"- {n.label} ({when}){': ' + n.detail if n.detail else ''}")
    if hidden:
        lines.append("NEVER RAISE THESE UNPROMPTED (respond warmly if they bring it up, but do not ask): "
                     + ", ".join(n.label for n in hidden) + ".")
    return "\n".join(lines)


def _priority(node: MemoryNode, now) -> float:
    """Severity dominates salience: a fall with swelling outranks a routine bill, whatever
    order they were mentioned in."""
    return SEVERITY_RANK.get(node.severity, 0) * 100 + _salience(node, now)


def _callback_nodes(parent_id: int):
    """Callback candidates: every open loop, plus health threads serious enough to lead a
    call (warn or urgent). A mild, routine symptom stays in the briefing list — it should
    not displace "you were making pickle" from the opening."""
    now = utcnow()
    with session() as s:
        cands = s.exec(select(MemoryNode).where(
            MemoryNode.parent_id == parent_id,
            MemoryNode.kind.in_(("open_loop", "health_thread")),
            MemoryNode.status == "active")).all()
    cands = [n for n in cands
             if n.kind == "open_loop" or SEVERITY_RANK.get(n.severity, 0) >= 1]
    return sorted(cands, key=lambda n: _priority(n, now), reverse=True)


def callback(parent_id: int) -> str:
    """The one thing to open the call with, phrased for the greeting. Kept separate from
    the knowledge block because it must sit beside the greeting: buried below the checklist,
    the model works through the agenda and never reaches it.

    When the top item is a warn/urgent health matter, the same-call fragments (a fall, the
    resulting pain, the swelling, the wish to see a doctor) are gathered into one episode,
    so tomorrow opens with "yesterday's fall — how is the ankle, did you see a doctor?"
    rather than a single scattered fragment or, worse, a routine bill."""
    ranked = _callback_nodes(parent_id)
    if not ranked:
        return ""
    top = ranked[0]

    if SEVERITY_RANK.get(top.severity, 0) >= 1:
        # gather the episode: every warn+ candidate from the same call as the top item
        episode = [n for n in ranked
                   if n.source_call_id == top.source_call_id
                   and SEVERITY_RANK.get(n.severity, 0) >= 1]
        details, seen = [], set()
        for n in episode:
            d = (n.detail or n.label).strip().rstrip(".")
            k = normalise(d)
            if k and k not in seen:
                seen.add(k)
                details.append(d)
        recap = "; ".join(details[:2]) if details else "how she was feeling"
        topics = "; ".join(n.label for n in episode)
        return (f"Right after the greeting, before anything else, gently follow up on what she told "
                f"you yesterday — {recap}. Ask how it is today and whether it has been dealt with "
                f"(a doctor seen, the swelling down). Listen with care, do not diagnose. Then call "
                f"close_loop once for each of these exact topics with what she says: {topics}. "
                f"After that, carry on warmly.")

    detail = f" — {top.detail}" if top.detail else ""
    return (f"Right after the greeting, before anything else, ask warmly about this one thing: "
            f"{top.label}{detail}. Ask it once, listen, then call close_loop with what they say and "
            f"move on. Do not raise it again.")


def _callback_covered(parent_id: int) -> set[int]:
    """Node ids the opening callback already speaks to, so the briefing does not repeat them."""
    ranked = _callback_nodes(parent_id)
    if not ranked:
        return set()
    top = ranked[0]
    if SEVERITY_RANK.get(top.severity, 0) >= 1:
        return {n.id for n in ranked
                if n.source_call_id == top.source_call_id and SEVERITY_RANK.get(n.severity, 0) >= 1}
    return {top.id}


def mark_used(parent_id: int, text: str) -> None:
    """Count the facts that made it into a briefing, so they yield to fresher ones."""
    if not text:
        return
    with session() as s:
        for n in s.exec(select(MemoryNode).where(
                MemoryNode.parent_id == parent_id, MemoryNode.status == "active")).all():
            if n.label and n.label in text:
                n.times_used += 1
                s.add(n)
        s.commit()


def seed_from_parent(parent_id: int, notes: str, meds: list[dict], child_name: str = "") -> int:
    """Turn what onboarding already collected into the first nodes, so the very first call
    has something to remember. Returns the number of nodes written."""
    n = 0
    if child_name:
        remember(parent_id, "person", child_name, "the child Sahara reports to",
                 relation="child", confidence=1.0, corrected_by="child")
        n += 1
    for m in meds or []:
        name = (m.get("name") or "").strip()
        if name:
            remember(parent_id, "medication", name, f"taken {m.get('when', 'daily')}",
                     confidence=1.0, corrected_by="child")
            n += 1
    note = (notes or "").strip()
    if note:
        # one node, not a parse: onboarding notes are prose the child wrote, and guessing
        # entities out of them is exactly the free-text parsing the design forbids
        remember(parent_id, "topic", "background", note, confidence=1.0, corrected_by="child")
        n += 1
    return n


def ensure_seeded(parent, child_name: str = "") -> int:
    """Seed the graph from the parent's own record if it is still empty. Idempotent, and
    called at call time as well as at onboarding, so a parent created by any route — the
    API, scripts/seed.py, or a database that predates memory — still starts with something."""
    with session() as s:
        if s.exec(select(MemoryNode).where(MemoryNode.parent_id == parent.id)).first():
            return 0
    return seed_from_parent(parent.id, parent.notes, parent.meds(), child_name)


def prior_health(parent_id: int, exclude_call_id: int | None = None) -> str:
    """A compact line per open health thread from earlier calls, for the escalation pass to
    judge trend against — is today's symptom new, or the same one mentioned for weeks?"""
    now = utcnow()
    with session() as s:
        threads = s.exec(select(MemoryNode).where(
            MemoryNode.parent_id == parent_id, MemoryNode.kind == "health_thread",
            MemoryNode.status == "active")).all()
    lines = []
    for n in sorted(threads, key=lambda n: n.last_confirmed, reverse=True):
        if exclude_call_id and n.source_call_id == exclude_call_id:
            continue
        days = (now - n.last_confirmed).days
        when = "today" if days == 0 else f"{days}d ago"
        lines.append(f"- {n.detail or n.label} (last mentioned {when}, severity {n.severity})")
    return "\n".join(lines)


def graph(parent_id: int) -> dict:
    """The whole graph for one parent — for the operator desk and the eventual export."""
    with session() as s:
        nodes = s.exec(select(MemoryNode).where(MemoryNode.parent_id == parent_id)).all()
        edges = s.exec(select(MemoryEdge).where(MemoryEdge.parent_id == parent_id)).all()
    now = utcnow()
    return {
        "nodes": [{**n.model_dump(), "salience": round(_salience(n, now), 3)} for n in nodes],
        "edges": [e.model_dump() for e in edges],
    }


def prune(parent_id: int, older_than_days: int = 365) -> int:
    """Drop low-confidence facts nobody has confirmed in a long time. Facts decay in
    salience rather than existence, but junk should not accumulate forever."""
    cutoff = utcnow() - timedelta(days=older_than_days)
    dropped = 0
    with session() as s:
        for n in s.exec(select(MemoryNode).where(
                MemoryNode.parent_id == parent_id, MemoryNode.confidence < 0.7,
                MemoryNode.last_confirmed < cutoff, MemoryNode.corrected_by == "model")).all():
            s.delete(n); dropped += 1
        s.commit()
    return dropped
