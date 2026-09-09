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
    child_phone: str                      # E.164, WhatsApp-capable
    child_language: str = "en"            # language of the summary
    created_at: datetime = Field(default_factory=utcnow)


class Parent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    family_id: int = Field(foreign_key="family.id")
    name: str
    phone: str                            # E.164
    language: str = "hi-IN"               # BCP-47; see persona.LANGUAGES
    call_time: str = "08:30"              # local time, HH:MM
    consent: bool = False                 # explicit, recorded; nothing is called without it
    consent_at: datetime | None = None
    medications: str = "[]"               # JSON list of {"name","when"}
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
    created_at: datetime = Field(default_factory=utcnow)

    def turns(self) -> list[dict]:
        return json.loads(self.transcript or "[]")

    def obs(self) -> list[dict]:
        return json.loads(self.observations or "[]")


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
