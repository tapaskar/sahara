"""Every minute: place the morning calls that are due, and the retries."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select

from . import calls, config
from .db import session
from .models import Call, Parent

log = logging.getLogger("sahara.scheduler")


def due_parents(now_local: datetime) -> list[int]:
    """Parents whose call_time is now (HH:MM) and who have no check-in started today."""
    hhmm = now_local.strftime("%H:%M")
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    out = []
    with session() as s:
        for p in s.exec(select(Parent).where(Parent.active == True, Parent.consent == True)).all():  # noqa: E712
            if p.call_time != hhmm:
                continue
            today = s.exec(select(Call).where(Call.parent_id == p.id, Call.kind == "checkin",
                                              Call.created_at >= day_start)).first()
            if today is None:
                out.append(p.id)
    return out


async def tick(now_utc: datetime | None = None) -> dict:
    now_utc = now_utc or datetime.utcnow()
    now_local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(config.TIMEZONE))
    started, retried = [], []
    for pid in due_parents(now_local):
        try:
            c = await calls.start_checkin(pid); started.append(c.id)
        except Exception as e:
            log.warning("could not start call for parent %s: %s", pid, e)
    for old in calls.due_retries(now_utc):
        try:
            c = await calls.start_checkin(old.parent_id, attempt=old.attempt + 1); retried.append(c.id)
        except Exception as e:
            log.warning("retry failed for call %s: %s", old.id, e)
    return {"started": started, "retried": retried}


def build_scheduler() -> AsyncIOScheduler:
    sch = AsyncIOScheduler(timezone="UTC")
    sch.add_job(tick, "cron", second=0, id="sahara-tick", max_instances=1, coalesce=True)
    return sch
