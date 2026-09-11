"""Transcript-drift detection: deterministic guards around a non-deterministic ASR.
The contract: every stored parent turn is either consistent with the declared language
or carries a flag saying it is not."""
from fastapi.testclient import TestClient

from sahara import transcheck
from sahara.engine.gemini_live import transcription_languages
from sahara.web.app import app

client = TestClient(app)

# the exact transcription the live incident produced for a Hindi answer about medicine
PORTUGUESE = "porra Ah, eu já tomei remédio."
HINDI = "ठीक है, सब ठीक है। आप कैसे हैं?"
CODE_SWITCH = "Doctor ने बोला BP normal है, tension मत लो"
INDIAN_ENGLISH = "I already took the medicine, no problem at all"


def test_the_portuguese_incident_is_flagged():
    assert transcheck.check(PORTUGUESE, "hi-IN") == "latin_diacritics"


def test_genuine_hindi_and_code_switching_pass():
    assert transcheck.check(HINDI, "hi-IN") == ""
    assert transcheck.check(CODE_SWITCH, "hi-IN") == ""       # Hinglish is normal, not drift
    assert transcheck.check(INDIAN_ENGLISH, "en-IN") == ""


def test_wrong_indic_script_is_flagged():
    assert transcheck.check("நான் மருந்து எடுத்துக்கொண்டேன் நன்றாக இருக்கிறேன்", "hi-IN") == "script_mismatch"


def test_tiny_turns_are_never_flagged():
    assert transcheck.check("हाँ", "hi-IN") == ""
    assert transcheck.check("Ok", "hi-IN") == ""
    assert transcheck.check("olá", "hi-IN") == ""             # too short to judge


def test_ascii_english_on_a_hindi_call_is_tolerated():
    # a fully-English answer on a Hindi call is code-switching, not Portuguese
    assert transcheck.check(INDIAN_ENGLISH, "hi-IN") == ""


def test_server_reported_language_comparison():
    assert transcheck.language_mismatch("pt-BR", "hi-IN") == "lang_mismatch:pt-BR"
    assert transcheck.language_mismatch("hi-IN", "hi-IN") == ""
    assert transcheck.language_mismatch("en-US", "hi-IN") == ""   # code-switch, never a mismatch
    assert transcheck.language_mismatch(None, "hi-IN") == ""      # absent is unknown, not drift
    assert transcheck.language_mismatch("or-IN", "od-IN") == ""   # Sarvam's od == BCP-47 or


def test_transcription_hint_is_valid_bcp47_with_english():
    assert transcription_languages("hi-IN") == ["hi-IN", "en-IN"]
    assert transcription_languages("od-IN") == ["or-IN", "en-IN"]  # od-IN is not valid BCP-47
    assert transcription_languages("en-IN") == ["en-IN"]


def _seed():
    f = client.post("/api/families", json={"child_name": "Ravi", "child_phone": "+919800000300"}).json()
    p = client.post("/api/parents", json={"family_id": f["id"], "name": "Sushila Devi",
                                          "phone": "+919700000300", "language": "hi-IN",
                                          "consent": True}).json()
    return p["id"]


async def test_drifted_turns_are_annotated_not_deleted():
    from sahara.calls import LiveCall

    pid = _seed()
    call_id = client.post(f"/api/parents/{pid}/mic-call").json()["call_id"]
    live = LiveCall(call_id)
    await live.on_transcript("parent", HINDI, True)
    await live.on_transcript("parent", PORTUGUESE, True)
    await live.on_transcript("parent", "कल", False, {"language_code": "pt-BR"})
    await live.on_transcript("parent", " मिलेंगे ज़रूर", True)

    assert "asr_flag" not in live.turns[0]
    assert live.turns[1]["asr_flag"] == "latin_diacritics"
    assert live.turns[2]["asr_flag"] == "lang_mismatch:pt-BR"     # server-reported wins
    assert live.turns[2]["text"] == "कल मिलेंगे ज़रूर"             # the text itself is kept

    await live.finish({"frames_in": 999})
    stored = client.get(f"/api/calls/{call_id}").json()["turns"]
    assert "asr_flag" in stored[1] and "asr_flag" in stored[2]     # flags persist 30 days
    assert all("lang" not in t and "final" not in t for t in stored)


async def test_a_need_observation_opens_a_loop():
    """The first real call logged "wants to see a doctor" and wrote no memory. The
    deterministic fan-out turns exactly that call into a pass."""
    from sahara import memory
    from sahara.calls import LiveCall

    pid = _seed()
    call_id = client.post(f"/api/parents/{pid}/mic-call").json()["call_id"]
    live = LiveCall(call_id)
    r = await live.on_tool_call({"id": "1", "name": "log_observation",
                                 "args": {"kind": "need", "detail": "Wants to see a doctor.",
                                          "severity": "info"}})
    assert r == {"ok": True}
    loops = [n for n in memory.graph(pid)["nodes"] if n["kind"] == "open_loop"]
    assert len(loops) == 1 and loops[0]["detail"] == "Wants to see a doctor."
    assert "doctor" in memory.callback(pid)
    assert live.mem_writes == 1


async def test_the_transcript_is_readable_while_the_call_is_still_running():
    """A live view can only show the conversation as it happens if turns are written down
    mid-call; before this they only landed when the line dropped."""
    from sahara.calls import LiveCall

    pid = _seed()
    call_id = client.post(f"/api/parents/{pid}/mic-call").json()["call_id"]
    live = LiveCall(call_id)

    await live.on_transcript("sahara", "नमस्ते, मैं सहारा हूँ।", False)
    await live.on_turn_end()
    mid = client.get(f"/api/calls/{call_id}").json()["turns"]
    assert [t["text"] for t in mid] == ["नमस्ते, मैं सहारा हूँ।"], mid

    await live.on_transcript("parent", "ठीक हूँ बेटा", True)
    mid2 = client.get(f"/api/calls/{call_id}").json()["turns"]
    assert [t["who"] for t in mid2] == ["sahara", "parent"], mid2


def test_indic_grammar_pins_both_speakers_genders():
    """Hindi conjugates the verb on the speaker's gender and inflects questions on the
    listener's. Unstated, the model drifts masculine and addresses an elderly woman as a
    man — which reads as carelessness, not a glitch."""
    from sahara.models import Family, Parent
    from sahara.persona import checkin_prompt, grammar_note

    she = Parent(name="Sushila", language="hi-IN", gender="female")
    he = Parent(name="Gopal", language="hi-IN", gender="male")
    tamil = Parent(name="Lakshmi", language="ta-IN", gender="female")

    # Sahara is always a woman, in every language
    for p in (she, he, tamil):
        assert "you are a woman" in grammar_note(p)

    assert "कैसी हैं" in grammar_note(she) and "कैसे हैं" not in grammar_note(she)
    assert "कैसे हैं" in grammar_note(he)
    assert "समझ गई" in grammar_note(she)          # feminine self-reference is spelled out

    # and it reaches the actual prompt
    fam = Family(child_name="Ravi", child_phone="+91")
    assert "कैसी हैं" in checkin_prompt(she, fam)

    # an unrecorded gender must not be guessed
    unknown = Parent(name="X", language="hi-IN", gender="")
    assert "not recorded" in grammar_note(unknown)


def test_a_visitor_owns_their_conversations_and_no_one_elses(monkeypatch):
    """The demo's identity is an opaque per-browser id. Everything you create belongs to it;
    another visitor on the same shared link can neither list nor open it."""
    from sahara import config
    monkeypatch.setattr(config, "DEMO", True)

    me, you = "visitor-" + "a" * 20, "visitor-" + "b" * 20

    mine = client.post("/api/try/start", json={"visitor": me, "child_name": "Ravi",
                                               "parent_name": "Sushila", "relation": "mother",
                                               "language": "hi-IN"}).json()
    yours = client.post("/api/try/start", json={"visitor": you, "child_name": "Meera",
                                                "parent_name": "Kamala", "relation": "mother",
                                                "language": "ta-IN"}).json()

    # each of us sees only our own
    mine_list = client.get(f"/api/try/mine?visitor={me}").json()["personas"]
    assert [p["parent_name"] for p in mine_list] == ["Sushila"]
    yours_list = client.get(f"/api/try/mine?visitor={you}").json()["personas"]
    assert [p["parent_name"] for p in yours_list] == ["Kamala"]
    assert client.get("/api/try/mine").json()["personas"] == []      # a stranger sees nothing

    # I can call my persona; you cannot
    cid = client.post(f"/api/try/{mine['parent_id']}/voice-call?token={me}").json()["call_id"]
    assert client.post(f"/api/try/{mine['parent_id']}/voice-call?token={you}").status_code == 403
    assert client.get(f"/api/try/{cid}/report?token={me}").status_code == 200
    assert client.get(f"/api/try/{cid}/report?token={you}").status_code == 403
    assert client.get(f"/api/try/{cid}/report").status_code == 403

    # and my second persona joins my list, so I can choose between them
    client.post("/api/try/start", json={"visitor": me, "child_name": "Ravi",
                                        "parent_name": "Gopal", "relation": "father",
                                        "language": "hi-IN", "gender": "male"})
    assert {p["parent_name"] for p in client.get(f"/api/try/mine?visitor={me}").json()["personas"]} \
        == {"Sushila", "Gopal"}


def test_demo_conversations_are_private_to_the_visitor(monkeypatch):
    """A public link means strangers share one server. One visitor must not be able to
    read another's conversation by guessing a call id — the demo holds real speech."""
    from sahara import config
    from sahara.web.app import demo_token

    monkeypatch.setattr(config, "DEMO", True)
    r = client.post("/api/try/start", json={"child_name": "Ravi", "parent_name": "Sushila",
                                            "relation": "mother", "language": "hi-IN"})
    assert r.status_code == 200
    me = r.json()
    assert me["token"] == demo_token(me["parent_id"])

    call = client.post(f"/api/try/{me['parent_id']}/voice-call?token={me['token']}")
    assert call.status_code == 200
    cid = call.json()["call_id"]

    # my own token works
    assert client.get(f"/api/try/{cid}/report?token={me['token']}").status_code == 200
    # a stranger guessing the call id does not
    assert client.get(f"/api/try/{cid}/report").status_code == 403
    assert client.get(f"/api/try/{cid}/report?token=deadbeef").status_code == 403
    # nor can they start a call on someone else's persona
    assert client.post(f"/api/try/{me['parent_id']}/voice-call?token=deadbeef").status_code == 403
