"""Tables. JSON-ish fields are stored as text; helpers below keep that honest."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Family(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    child_name: str
    child_name_native: str = ""           # the child's name in the parent's script, as she says it
    child_phone: str                      # E.164, WhatsApp-capable
    child_language: str = "en"            # language of the summary
    created_at: datetime = Field(default_factory=utcnow)


class Parent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    family_id: int = Field(foreign_key="family.id")
    name: str
    name_native: str = ""                 # the parent's name in their own script
    gender: str = ""                      # female | male | "" — Indic verbs conjugate on it
    demo_visitor: str = Field(default="", index=True)   # /try only: whose persona this is
    phone: str                            # E.164
    language: str = "hi-IN"               # BCP-47; see persona.LANGUAGES
    call_time: str = "08:30"              # local time, HH:MM
    consent: bool = False                 # explicit, recorded; nothing is called without it
    consent_at: datetime | None = None
    medications: str = "[]"               # JSON list of {"name","when"}
    relation: str = ""                    # what THEY are to the child: mother, father, dadi...
    conditions: str = ""                  # doctor-recorded, comma separated; see guardrails.py
    notes: str = ""                       # context for the agent: town, habits, what they like to talk about
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)

    def meds(self) -> list[dict]:
        try:
            return json.loads(self.medications or "[]")
        except json.JSONDecodeError:
            return []


class Call(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    parent_id: int = Field(foreign_key="parent.id")
    kind: str = "checkin"                 # checkin | screen
    provider: str = ""
    provider_call_id: str = ""
    status: str = "scheduled"             # scheduled ringing in_progress completed no_answer failed
    attempt: int = 1
    engine: str = ""
    caller_number: str = ""               # inbound screens only
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_s: int = 0
    transcript: str = "[]"                # JSON list of {"who": "parent"|"sahara"|"caller", "text"}
    observations: str = "[]"              # JSON list from tool calls
    summary: str = ""                     # JSON CallSummary / ScreenDecision
    escalation: str = ""                  # JSON Escalation (reasoning pass)
    created_at: datetime = Field(default_factory=utcnow)

    def turns(self) -> list[dict]:
        return json.loads(self.transcript or "[]")

    def obs(self) -> list[dict]:
        return json.loads(self.observations or "[]")


class MemoryNode(SQLModel, table=True):
    """One remembered fact about one parent. Written only by tool calls during a call;
    survives the transcript purge because it holds a sentence, never an utterance."""
    id: int | None = Field(default=None, primary_key=True)
    parent_id: int = Field(foreign_key="parent.id", index=True)   # hard scope; no query crosses it
    kind: str                             # person place topic routine health_thread medication event organisation open_loop
    label: str                            # as spoken: "Ayaan", "achaar", "knee pain"
    label_key: str = Field(index=True)    # normalised for matching; see memory.normalise
    relation: str = ""                    # person nodes: relation to the parent (son, grandson, neighbour)
    detail: str = ""                      # one sentence, English
    status: str = "active"                # active | closed (open_loops) | superseded | deleted
    severity: str = "info"                # info | warn | urgent — a fall outranks a bill
    sensitivity: str = "normal"           # normal | sensitive | never_volunteer
    confidence: float = 0.6               # model 0.6, parent-confirmed 0.9, child-corrected 1.0
    corrected_by: str = "model"           # model | parent | child
    source_call_id: int | None = None
    times_used: int = 0                   # fatigue: how often this was put in a briefing
    first_seen: datetime = Field(default_factory=utcnow)
    last_confirmed: datetime = Field(default_factory=utcnow)


class MemoryEdge(SQLModel, table=True):
    """A typed relation between two of one parent's nodes: Ravi PARENT_OF Ayaan."""
    id: int | None = Field(default=None, primary_key=True)
    parent_id: int = Field(foreign_key="parent.id", index=True)
    src_id: int = Field(foreign_key="memorynode.id")
    dst_id: int = Field(foreign_key="memorynode.id")
    kind: str                             # PARENT_OF TREATS PRESCRIBED_FOR KNOWS ...
    label: str = ""
    confidence: float = 0.6
    source_call_id: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Alert(SQLModel, table=True):
    """Anything the child should hear about, and the record that we told them."""
    id: int | None = Field(default=None, primary_key=True)
    parent_id: int = Field(foreign_key="parent.id")
    call_id: int | None = None
    kind: str                             # summary | scam | health | no_answer | need
    severity: str = "info"                # info | warn | urgent
    message: str
    channel: str = ""                     # console | meta | twilio
    delivered: bool = False
    created_at: datetime = Field(default_factory=utcnow)
