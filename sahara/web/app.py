"""Sahara web: telephony webhooks, the audio WebSocket, the operator API and dashboard."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import select

from .. import __version__, calls, config, scheduler
from ..db import init_db, session
from .. import memory
from ..engine import make_engine
from ..gemini import using_vertex
from ..models import Alert, Call, Family, Parent, utcnow
from ..persona import LANGUAGES
from ..telephony import make_telephony

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = FastAPI(title="Sahara", version=__version__)
STATIC = Path(__file__).resolve().parent / "static"
_sched = None
init_db()


@app.on_event("startup")
async def _startup():
    global _sched
    if not config.OFFLINE:
        _sched = scheduler.build_scheduler(); _sched.start()


def operator(x_sahara_token: str | None = Header(default=None)):
    if config.OPERATOR_TOKEN and x_sahara_token != config.OPERATOR_TOKEN:
        raise HTTPException(401, "operator token required")


# ------------------------------------------------------------ dashboard ---
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/mic")
def mic():
    return FileResponse(STATIC / "mic.html")


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "offline": config.OFFLINE, "engine": config.VOICE_ENGINE,
            "telephony": config.TELEPHONY, "whatsapp": config.WHATSAPP, "live_model": config.GEMINI_LIVE_MODEL,
            "languages": LANGUAGES, "gemini_auth": _gemini_auth()}


def _gemini_auth() -> str:
    """How Gemini will authenticate, named not valued. Note that GEMINI_API_KEY alone
    routes to Vertex, because using_vertex() only looks at GOOGLE_API_KEY."""
    if config.OFFLINE:
        return "offline (null engine)"
    if not using_vertex():
        return "api_key (GOOGLE_API_KEY)"
    have_adc = bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")) or \
        (Path.home() / ".config/gcloud/application_default_credentials.json").exists()
    if have_adc:
        return f"vertex (project={config.GOOGLE_CLOUD_PROJECT or 'unset'})"
    return "NONE — no GOOGLE_API_KEY and no application default credentials"


# ------------------------------------------------------------- families ---
class FamilyIn(BaseModel):
    child_name: str
    child_name_native: str = ""
    child_phone: str
    child_language: str = "en"


class ParentIn(BaseModel):
    family_id: int
    name: str
    name_native: str = ""
    phone: str
    language: str = "hi-IN"
    call_time: str = "08:30"
    consent: bool = False
    medications: list[dict] = []
    notes: str = ""


@app.post("/api/families", status_code=201, dependencies=[Depends(operator)])
def create_family(body: FamilyIn):
    with session() as s:
        f = Family(**body.model_dump()); s.add(f); s.commit(); s.refresh(f); return f


@app.post("/api/parents", status_code=201, dependencies=[Depends(operator)])
def create_parent(body: ParentIn):
    if body.language not in LANGUAGES:
        raise HTTPException(400, f"language must be one of {list(LANGUAGES)}")
    with session() as s:
        if s.get(Family, body.family_id) is None:
            raise HTTPException(404, "no such family")
        family = s.get(Family, body.family_id)
        p = Parent(**{**body.model_dump(exclude={"medications"}), "medications": json.dumps(body.medications),
                      "consent_at": utcnow() if body.consent else None})
        s.add(p); s.commit(); s.refresh(p)
    memory.ensure_seeded(p, family.child_name_native or family.child_name)      # give the first call something to remember
    return p


@app.get("/api/parents/{pid}/memory", dependencies=[Depends(operator)])
def parent_memory(pid: int):
    with session() as s:
        if s.get(Parent, pid) is None:
            raise HTTPException(404, "no such parent")
    return {**memory.graph(pid), "briefing": memory.briefing(pid), "callback": memory.callback(pid)}


@app.delete("/api/parents/{pid}/memory/{label}", dependencies=[Depends(operator)])
def forget_memory(pid: int, label: str):
    """The erasure right, in practice: the node and its edges go."""
    return {"removed": memory.forget(pid, label)}


@app.post("/api/parents/{pid}/consent", dependencies=[Depends(operator)])
def record_consent(pid: int, granted: bool = True):
    with session() as s:
        p = s.get(Parent, pid)
        if p is None:
            raise HTTPException(404, "no such parent")
        p.consent, p.consent_at = granted, utcnow() if granted else None
        s.add(p); s.commit(); s.refresh(p); return p


@app.get("/api/parents", dependencies=[Depends(operator)])
def list_parents():
    with session() as s:
        out = []
        for p in s.exec(select(Parent)).all():
            f = s.get(Family, p.family_id)
            last = s.exec(select(Call).where(Call.parent_id == p.id, Call.kind == "checkin")
                          .order_by(Call.id.desc())).first()
            out.append({**p.model_dump(), "child_name": f.child_name if f else "",
                        "last_call": last.model_dump() if last else None})
        return out


@app.get("/api/parents/{pid}/calls", dependencies=[Depends(operator)])
def parent_calls(pid: int):
    with session() as s:
        return [c.model_dump() for c in s.exec(select(Call).where(Call.parent_id == pid).order_by(Call.id.desc())).all()]


@app.get("/api/calls/{cid}", dependencies=[Depends(operator)])
def get_call(cid: int):
    with session() as s:
        c = s.get(Call, cid)
        if c is None:
            raise HTTPException(404, "no such call")
        d = c.model_dump()
        d["turns"], d["obs"] = c.turns(), c.obs()
        d["summary"] = json.loads(c.summary) if c.summary else None
        d["alerts"] = [a.model_dump() for a in s.exec(select(Alert).where(Alert.call_id == cid)).all()]
        return d


@app.post("/api/parents/{pid}/call-now", dependencies=[Depends(operator)])
async def call_now(pid: int):
    try:
        c = await calls.start_checkin(pid)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return c


@app.post("/api/parents/{pid}/mic-call", dependencies=[Depends(operator)])
def mic_call(pid: int):
    """A check-in row for the browser mic client. No telephony provider is dialled;
    the browser speaks the Twilio media-stream envelope on /ws/twilio/<call_id>."""
    with session() as s:
        parent = s.get(Parent, pid)
        if parent is None or not parent.active:
            raise HTTPException(404, "no such active parent")
        if not parent.consent:
            raise HTTPException(403, f"{parent.name} has not consented; not calling")
        call = Call(parent_id=parent.id, kind="checkin", status="scheduled", attempt=1,
                    provider="browser", provider_call_id="browser",
                    engine=make_engine().name, started_at=utcnow())
        s.add(call); s.commit(); s.refresh(call)
        return {"call_id": call.id, "parent": parent.name, "language": parent.language,
                "engine": call.engine}


class SimulateIn(BaseModel):
    parent_lines: list[str]


@app.post("/api/parents/{pid}/simulate", dependencies=[Depends(operator)])
async def simulate(pid: int, body: SimulateIn):
    with session() as s:
        if s.get(Parent, pid) is None:
            raise HTTPException(404, "no such parent")
    c = await calls.simulate_checkin(pid, body.parent_lines)
    return get_call(c.id)


@app.post("/api/scheduler/tick", dependencies=[Depends(operator)])
async def tick_now():
    return await scheduler.tick()


@app.get("/api/metrics", dependencies=[Depends(operator)])
def metrics(days: int = 7):
    """The pilot's three numbers: answer rate, call length, parent-initiated talk."""
    since = utcnow() - timedelta(days=days)
    with session() as s:
        cs = s.exec(select(Call).where(Call.kind == "checkin", Call.created_at >= since,
                                       Call.status.in_(["completed", "no_answer", "failed"]))).all()
        done = [c for c in cs if c.status == "completed"]
        summaries = [json.loads(c.summary) for c in done if c.summary]
        eng = [x.get("engagement", 0) for x in summaries]
        initiated = [x for x in summaries if x.get("parent_initiated_topics")]
        alerts = s.exec(select(Alert).where(Alert.created_at >= since)).all()
        return {"days": days, "calls": len(cs), "answered": len(done),
                "answer_rate": round(len(done) / len(cs), 3) if cs else None,
                "avg_duration_s": round(sum(c.duration_s for c in done) / len(done)) if done else None,
                "avg_engagement": round(sum(eng) / len(eng), 2) if eng else None,
                "parent_initiated_share": round(len(initiated) / len(summaries), 2) if summaries else None,
                "follow_ups": sum(1 for x in summaries if x.get("follow_up")),
                "scam_alerts": sum(1 for a in alerts if a.kind == "scam"),
                "summaries_delivered": sum(1 for a in alerts if a.kind == "summary" and a.delivered)}


# ------------------------------------------------------------ telephony ---
async def _form(request: Request) -> dict:
    try:
        return dict(await request.form())
    except Exception:
        return dict(request.query_params)


@app.api_route("/telephony/{provider}/answer", methods=["GET", "POST"])
async def answer(provider: str, call_id: int = Query(...)):
    """The provider fetches this when the parent picks up: start streaming audio to us."""
    with session() as s:
        c = s.get(Call, call_id)
        if c is None:
            raise HTTPException(404)
        c.status = "in_progress"; s.add(c); s.commit()
    xml = make_telephony(provider).answer_xml(call_id)
    return Response(content=xml, media_type="application/xml")


@app.api_route("/telephony/{provider}/status", methods=["GET", "POST"])
async def status(provider: str, request: Request, call_id: int = Query(...)):
    form = await _form(request)
    st = make_telephony(provider).status_from_callback(form)
    if st:
        await calls.mark_status(call_id, st)
    return Response(content="ok")


@app.api_route("/telephony/{provider}/inbound", methods=["GET", "POST"])
async def inbound(provider: str, request: Request):
    """Someone called the Sahara number (or the parent forwarded an unknown caller): screen it."""
    form = await _form(request)
    caller = form.get("From") or form.get("from") or ""
    forwarded_for = form.get("ForwardedFrom") or form.get("forwarded_from") or form.get("To") or form.get("to") or ""
    sid = form.get("CallSid") or form.get("CallUUID") or ""
    c = calls.start_screen(caller, forwarded_for, sid)
    t = make_telephony(provider)
    if c is None:
        return Response(content=t.hangup_xml(), media_type="application/xml")
    after = f"{config.PUBLIC_URL.rstrip('/')}/telephony/{provider}/after-screen?call_id={c.id}"
    return Response(content=t.answer_xml(c.id, after_url=after), media_type="application/xml")


@app.api_route("/telephony/{provider}/after-screen", methods=["GET", "POST"])
async def after_screen(provider: str, call_id: int = Query(...)):
    """The screen stream ended: connect the caller to the parent, or hang up."""
    t = make_telephony(provider)
    with session() as s:
        c = s.get(Call, call_id)
        if c is None:
            return Response(content=t.hangup_xml(), media_type="application/xml")
        d = json.loads(c.summary) if c.summary else {}
        parent = s.get(Parent, c.parent_id)
    if d.get("action") == "connect":
        return Response(content=t.dial_xml(parent.phone, config.CALLER_ID or ""), media_type="application/xml")
    return Response(content=t.hangup_xml(), media_type="application/xml")


@app.websocket("/ws/{provider}/{call_id}")
async def stream(ws: WebSocket, provider: str, call_id: int):
    await ws.accept()
    with session() as s:
        if s.get(Call, call_id) is None:
            await ws.close(code=4404); return
    try:
        stats = await calls.LiveCall(call_id).run(ws, provider)
        logging.getLogger("sahara.web").info("call %s ended: %s", call_id, stats)
    finally:
        try:
            await ws.close()
        except Exception:
            pass


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception):
    logging.getLogger("sahara.web").exception("request failed")
    return JSONResponse(status_code=500, content={"error": str(exc)})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
