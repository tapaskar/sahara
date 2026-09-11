"""Memory v1: the per-parent graph, the briefing it compiles, and the rules that keep it
honest — scoped per parent, deduplicated, decaying, and erasable. See docs/MEMORY.md."""
from fastapi.testclient import TestClient

from sahara import memory
from sahara.models import utcnow
from sahara.persona import CHECKIN_TOOLS, checkin_prompt
from sahara.web.app import app

client = TestClient(app)


def _parent(name="Sushila Devi", notes="", meds=None, child="Ravi"):
    f = client.post("/api/families", json={"child_name": child, "child_phone": "+919800000100"}).json()
    p = client.post("/api/parents", json={"family_id": f["id"], "name": name, "phone": "+919700000100",
                                          "language": "hi-IN", "consent": True,
                                          "medications": meds or [], "notes": notes}).json()
    return p["id"]


def test_normalise_folds_case_and_punctuation_but_keeps_indic_vowel_signs():
    assert memory.normalise("  Ayaan's  ") == memory.normalise("ayaan s")
    assert memory.normalise("Dr. Mishra") == memory.normalise("dr mishra") == "dr mishra"
    assert memory.normalise("Café") == "cafe"                      # Latin diacritics fold
    assert memory.normalise("   ") == ""
    # matras are letters, not decoration: strip them and unrelated words collide
    for word in ("अचार", "सुशीला", "मंदिर"):
        assert memory.normalise(word) == word
    assert memory.normalise("मंदिर") != memory.normalise("मदर")


def test_remembering_twice_updates_rather_than_duplicates():
    pid = _parent()
    a = memory.remember(pid, "person", "Ayaan", "in 4th standard", relation="grandson")
    b = memory.remember(pid, "person", "  ayaan  ", "in 5th standard now", relation="grandson")
    assert a.id == b.id, "a re-mention must refresh the node, not create a second one"
    assert b.detail == "in 5th standard now"
    assert b.confidence > a.confidence, "being mentioned again is evidence"


def test_memory_never_crosses_parents():
    one, two = _parent(name="Sushila"), _parent(name="Kamala")
    memory.remember(one, "topic", "cricket", "follows every India match")
    assert "cricket" in memory.briefing(one)
    assert "cricket" not in memory.briefing(two)
    assert not any(n["label"] == "cricket" for n in memory.graph(two)["nodes"])
    assert all(n["parent_id"] == two for n in memory.graph(two)["nodes"])


def test_briefing_carries_exactly_one_callback_and_hides_sensitive_facts():
    pid = _parent()
    memory.remember(pid, "topic", "cricket", "follows every India match")
    memory.remember(pid, "topic", "money worry", "anxious about the pension", sensitivity="sensitive")
    memory.open_loop(pid, "making achaar", "was going to make it yesterday")
    memory.open_loop(pid, "Ayaan's exam", "exam was on Friday")

    b, ask = memory.briefing(pid), memory.callback(pid)
    assert ask and ask.count("ask warmly about this one thing") == 1, "one callback, not a recitation"
    assert ("making achaar" in ask) ^ ("Ayaan's exam" in ask), "exactly one of the open loops"
    assert "cricket" in b
    assert "NEVER RAISE THESE UNPROMPTED" in b and "money worry" in b
    # the sensitive fact must not appear as something to weave in
    facts_block = b.split("NEVER RAISE")[0]
    assert "money worry" not in facts_block


def test_closing_a_loop_retires_it_as_a_callback():
    pid = _parent()
    memory.open_loop(pid, "making achaar", "was going to make it")
    assert "making achaar" in memory.callback(pid)
    memory.close_loop(pid, "making achaar", "made it, too spicy")
    assert memory.callback(pid) == ""


def test_recently_used_facts_yield_to_fresher_ones():
    pid = _parent()
    for i in range(memory.MAX_FACTS + 1):
        memory.remember(pid, "topic", f"fact {i}", f"detail {i}")
    stale = memory.remember(pid, "topic", "fact 0")
    now = utcnow()
    before = memory._salience(stale, now)
    stale.times_used = 4
    assert memory._salience(stale, now) < before, "fatigue must push a well-worn fact down"


def test_forget_removes_the_node_and_its_edges():
    pid = _parent()
    ravi = memory.remember(pid, "person", "Ravi", "the son", relation="son")
    ayaan = memory.remember(pid, "person", "Ayaan", "grandson", relation="grandson")
    memory.link(pid, ravi, ayaan, "PARENT_OF")
    assert len(memory.graph(pid)["edges"]) == 1
    assert memory.forget(pid, "Ayaan") >= 2                     # the node and the edge
    g = memory.graph(pid)
    assert not any(n["label"] == "Ayaan" for n in g["nodes"])
    assert g["edges"] == []


def test_onboarding_seeds_the_graph_so_the_first_call_knows_something():
    pid = _parent(notes="Lives alone in Cuttack, loves cricket",
                  meds=[{"name": "Amlodipine", "when": "morning"}])
    labels = {n["label"] for n in memory.graph(pid)["nodes"]}
    assert {"Ravi", "Amlodipine", "background"} <= labels
    assert "Cuttack" in memory.briefing(pid)


def test_briefing_reaches_the_persona_and_the_tools_exist():
    pid = _parent()
    memory.open_loop(pid, "making achaar", "was going to make it yesterday")
    from sahara.db import session
    from sahara.models import Family, Parent
    with session() as s:
        parent = s.get(Parent, pid)
        family = s.get(Family, parent.family_id)
    prompt = checkin_prompt(parent, family, memory.briefing(pid), memory.callback(pid))
    assert "making achaar" in prompt and "close_loop" in prompt
    # the callback must sit beside the greeting, not below the numbered agenda: buried
    # under the checklist the model works through the agenda and never reaches it
    lines = prompt.splitlines()
    ask_at = next(i for i, l in enumerate(lines) if "making achaar" in l)
    agenda_at = next(i for i, l in enumerate(lines) if l.startswith("1. How did they sleep"))
    assert ask_at < agenda_at, "the callback must come before the checklist"
    names = {t["name"] for t in CHECKIN_TOOLS}
    assert {"remember_person", "remember_fact", "open_loop", "close_loop"} <= names


async def test_tool_calls_write_the_graph_through_a_live_call():
    from sahara.calls import LiveCall
    pid = _parent()
    call_id = client.post(f"/api/parents/{pid}/mic-call").json()["call_id"]
    live = LiveCall(call_id)

    await live.on_tool_call({"id": "1", "name": "remember_person",
                             "args": {"name": "Ayaan", "relation": "grandson", "detail": "plays cricket"}})
    await live.on_tool_call({"id": "2", "name": "remember_fact",
                             "args": {"kind": "routine", "label": "market day", "detail": "goes on Tuesdays"}})
    await live.on_tool_call({"id": "3", "name": "open_loop",
                             "args": {"topic": "making achaar", "detail": "planned for today"}})
    await live.on_tool_call({"id": "4", "name": "log_observation",
                             "args": {"kind": "health", "detail": "Knee pain since yesterday",
                                      "severity": "info"}})

    g = memory.graph(pid)
    kinds = {n["label"]: n["kind"] for n in g["nodes"]}
    assert kinds["Ayaan"] == "person" and kinds["market day"] == "routine"
    assert kinds["making achaar"] == "open_loop"
    assert "Knee pain since yesterday" in kinds and kinds["Knee pain since yesterday"] == "health_thread"
    assert "OPEN HEALTH THREADS" in memory.briefing(pid)
    assert "making achaar" in memory.callback(pid)


async def test_a_bad_memory_write_does_not_derail_the_call():
    from sahara.calls import LiveCall
    pid = _parent()
    call_id = client.post(f"/api/parents/{pid}/mic-call").json()["call_id"]
    live = LiveCall(call_id)
    # no label at all: the model got it wrong, and a wrong fact is worse than a missing one
    r = await live.on_tool_call({"id": "1", "name": "remember_person", "args": {"relation": "grandson"}})
    assert r == {"ok": False}
    assert memory.graph(pid)["nodes"], "seeded nodes survive"
    assert not any(n["kind"] == "person" and not n["label"] for n in memory.graph(pid)["nodes"])


def test_memory_api_exposes_and_erases():
    pid = _parent()
    memory.remember(pid, "topic", "cricket", "follows every India match")
    got = client.get(f"/api/parents/{pid}/memory").json()
    assert any(n["label"] == "cricket" for n in got["nodes"]) and "cricket" in got["briefing"]
    assert client.delete(f"/api/parents/{pid}/memory/cricket").json()["removed"] >= 1
    assert not any(n["label"] == "cricket" for n in client.get(f"/api/parents/{pid}/memory").json()["nodes"])


def test_names_are_spoken_in_the_parents_script():
    """A Latin name inside an otherwise Devanagari sentence reads as a code-switch: the
    recording notice is spoken verbatim, so the native form has to reach it."""
    from sahara.db import session
    from sahara.models import Family, Parent
    from sahara.persona import checkin_prompt, recording_notice

    f = client.post("/api/families", json={"child_name": "Ravi", "child_name_native": "रवि",
                                           "child_phone": "+919800000200"}).json()
    p = client.post("/api/parents", json={"family_id": f["id"], "name": "Sushila Devi",
                                          "name_native": "सुशीला देवी", "phone": "+919700000200",
                                          "language": "hi-IN", "consent": True}).json()
    with session() as s:
        parent, family = s.get(Parent, p["id"]), s.get(Family, f["id"])

    notice = recording_notice(parent, family)
    assert "रवि" in notice and "Ravi" not in notice
    assert "रवि" in checkin_prompt(parent, family)

    # the graph remembers them as the parent says them
    assert any(n["label"] == "रवि" for n in memory.graph(p["id"])["nodes"])


def test_a_family_without_a_native_name_still_works():
    from sahara.db import session
    from sahara.models import Family, Parent
    from sahara.persona import recording_notice

    pid = _parent(child="Meera")
    with session() as s:
        parent = s.get(Parent, pid)
        family = s.get(Family, parent.family_id)
    assert "Meera" in recording_notice(parent, family)      # falls back, never blank


async def test_a_fall_leads_tomorrows_call_not_a_routine_bill():
    """Reproduces call 9: three warn health facts (leg pain, a fall, ankle swelling), a
    warn need (doctor) and an info need (bill). Tomorrow must open with the fall episode
    and follow it up as one thing — never with the bill."""
    from sahara import memory
    from sahara.calls import LiveCall

    pid = _parent()
    call_id = client.post(f"/api/parents/{pid}/mic-call").json()["call_id"]
    live = LiveCall(call_id)

    async def obs(kind, detail, severity):
        await live.on_tool_call({"id": "x", "name": "log_observation",
                                 "args": {"kind": kind, "detail": detail, "severity": severity}})

    await obs("health", "Pain in leg since morning", "warn")
    await obs("health", "Fell yesterday, causing leg pain", "warn")
    await obs("health", "Ankle swelling after the fall", "warn")
    await obs("need", "Wants to consult a doctor about the fall", "warn")
    await obs("need", "Needs a bill paid", "info")

    cb = memory.callback(pid)
    assert "bill" not in cb.lower(), "a routine bill must not lead a call after a fall"
    assert "fell" in cb.lower() or "fall" in cb.lower() or "ankle" in cb.lower()
    # it is one grouped episode, not a single scattered fragment
    assert "close_loop once for each" in cb

    # the bill still survives as a lower-priority loop for a later call
    loops = [n["label"] for n in memory.graph(pid)["nodes"] if n["kind"] == "open_loop"]
    assert any("bill" in l.lower() for l in loops)

    # the briefing must not also re-list the health threads the opening already covers
    brief = memory.briefing(pid)
    assert "OPEN HEALTH THREADS" not in brief or "ankle" not in brief.lower()


async def test_resolving_a_thread_stops_it_leading():
    from sahara import memory
    from sahara.calls import LiveCall

    pid = _parent()
    call_id = client.post(f"/api/parents/{pid}/mic-call").json()["call_id"]
    live = LiveCall(call_id)
    await live.on_tool_call({"id": "1", "name": "log_observation",
                             "args": {"kind": "health", "detail": "Ankle swelling", "severity": "warn"}})
    assert "ankle" in memory.callback(pid).lower()
    # next call: she says it's healed
    memory.close_loop(pid, "Ankle swelling", "healed, no more swelling")
    assert "ankle" not in memory.callback(pid).lower()


async def test_a_restated_fact_is_logged_once_but_can_get_worse():
    """The model re-states earlier facts when it circles back to an unanswered question,
    so the same observation arrives twice in one call. Keep one — but a later, more
    serious mention must still raise the severity."""
    from sahara.calls import LiveCall

    pid = _parent()
    call_id = client.post(f"/api/parents/{pid}/mic-call").json()["call_id"]
    live = LiveCall(call_id)

    async def obs(kind, detail, severity="info"):
        return await live.on_tool_call({"id": "x", "name": "log_observation",
                                        "args": {"kind": kind, "detail": detail, "severity": severity}})

    await obs("meal", "Ate poha for breakfast")
    await obs("meal", "ate poha for breakfast.")        # same fact, restated
    await obs("sleep", "Slept well")
    assert [o["kind"] for o in live.obs] == ["meal", "sleep"], live.obs

    # the knee ache turns out to be worse than first stated
    await obs("health", "Knee pain", "info")
    await obs("health", "knee pain", "warn")
    health = [o for o in live.obs if o["kind"] == "health"]
    assert len(health) == 1 and health[0]["severity"] == "warn"
