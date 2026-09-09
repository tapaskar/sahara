# Sahara

**A voice agent that phones a parent every morning, in their language, and tells their child how they are.**
The week-one pilot of Sahara: no hardware, no app for the parent. Their ordinary mobile rings, a warm
voice talks with them for two or three minutes, the child gets a short WhatsApp, and unknown callers
are screened for scams. Everything is built to answer one question: will the parent pick up, and will
the child pay.

```
08:30 IST  scheduler ──▶ Twilio/Plivo places the call ──▶ parent's phone rings
                                   │ 8 kHz audio over a WebSocket
                                   ▼
                        Gemini Live (audio in / audio out, 90+ languages)
                        tools: log_observation, end_call
                                   │
                    transcript + facts ──▶ summary ──▶ WhatsApp to the child
                                                    ──▶ urgent alerts, scam alerts

unknown caller ──▶ Sahara number ──▶ screener persona ──▶ connect / take message / block ──▶ child told
```

## What is in the box

| Piece | File | Notes |
|---|---|---|
| Morning check-in persona | `sahara/persona.py` | Recording notice first (DPDP), one question at a time, safety rules for chest pain, falls, OTP requests; facts via tools |
| Call screener persona | `sahara/persona.py` | Answers on the parent's behalf; `decide` tool; heuristics can raise the model's scam risk, never lower it |
| Voice engines | `sahara/engine/` | `gemini_live` (default, audio to audio, barge-in), `cascade` (Sarvam speech to text and text to speech around Gemini text), `null` (offline) |
| Telephony | `sahara/telephony/` | Twilio Media Streams and Plivo Audio Streams, both bidirectional; the bridge converts mu-law 8 kHz to PCM 16 kHz and back |
| Summary | `sahara/summarize.py` | Gemini with a typed schema; rule-based fallback so a call is never lost |
| Notifications | `sahara/notify.py` | WhatsApp via Meta Cloud API (template) or Twilio; console offline |
| Scheduler | `sahara/scheduler.py` | Each parent's local call time, one call a day, retry once after 30 minutes, tell the child after the last miss |
| Operator desk | `sahara/web/` | Add families and parents, consent, call now, simulate, transcripts, the three pilot metrics |

## The three numbers

`GET /api/metrics` reports, for the last seven days: **answer rate**, **average call length**, and the
**share of calls where the parent raised a topic unprompted**. Those, plus how many children pay at the
end of week four, are the whole pilot.

## Run it

### Offline, in five minutes

```bash
pip install -e ".[test]"
SAHARA_OFFLINE=1 uvicorn sahara.web.app:app --port 8080
# open http://localhost:8080, add a family and a parent, press Simulate
pytest -q          # 17 tests: audio, scam heuristics, summaries, webhooks, the audio bridge, scheduling
```

Offline mode runs the whole data path with a scripted parent, the rule-based summary and a console
WhatsApp, so the desk and the API can be exercised before any account exists.

### Live, for the first real calls

1. **Gemini.** A Google Cloud project with Vertex AI, or a Gemini API key. `GEMINI_LIVE_MODEL` defaults
   to `gemini-3.1-flash-live-preview`; native-audio Live models pick the language from the prompt, so
   the parent's language is pinned in the persona, not in a config field.
2. **Telephony.** A Twilio or Plivo account with an Indian number (`SAHARA_CALLER_ID`). Point the number's
   inbound voice URL at `/telephony/<provider>/inbound` for screening. Sahara must be reachable from the
   provider: `SAHARA_PUBLIC_URL` is an ngrok URL in week one and the Cloud Run URL after.
3. **WhatsApp.** Meta Cloud API needs an approved template; the default name is `sahara_daily_summary`
   with one body parameter. Suggested template text: `Sahara update: {{1}}`. Twilio's WhatsApp sandbox
   works for the pilot without approval.
4. Copy `.env.example` to `.env`, then:

```bash
scripts/seed.py --child Ravi --child-phone +91... --parent "Sushila Devi" --parent-phone +91... \
    --language hi-IN --time 08:30 --consent --notes "Lives alone in Cuttack, loves cricket"
uvicorn sahara.web.app:app --port 8080         # the scheduler starts with the app
curl -X POST -H "X-Sahara-Token: $SAHARA_OPERATOR_TOKEN" localhost:8080/api/parents/1/call-now
```

`scripts/fake_phone.py` pretends to be Twilio: it streams a WAV of a "parent" into a call and saves what
Sahara said, so the voice loop can be tuned on a laptop before a number is bought.

### Cloud Run

`deploy/cloud_run.sh` builds and deploys a single always-on instance (the scheduler lives in the
process). Move `SAHARA_DATABASE_URL` to Cloud SQL before the second city.

## Consent and data

Nothing is called without `consent=true` on the parent, recorded with a timestamp. Every call opens with
a spoken recording notice in the parent's language. Transcripts are kept `SAHARA_RETENTION_DAYS` days.
Audio is never stored; only transcripts and the facts the agent logged. The operator API is protected by
`SAHARA_OPERATOR_TOKEN`.

## Honest limits

- The screener only sees calls that reach the Sahara number: parents forward unanswered or unknown calls
  to it with their operator's conditional-forwarding code. Screening the parent's own number directly is
  the hardware device's job, not this pilot's.
- Speech models degrade on hearing-impaired, very slow or dialectal speech. The pilot's first task is to
  find out by how much, with real parents, which is why transcripts are kept.
- Sarvam endpoint shapes in the cascade engine are written from their public API and must be checked
  against `docs.sarvam.ai` before use; they version models often.

## Layout

```
sahara/
  audio.py        mu-law, resampling, VAD, WAV helpers (numpy only)
  persona.py      prompts, tools, CallSummary and ScreenDecision schemas, recording notices
  scam.py         multilingual scam heuristics
  summarize.py    Gemini summary with typed output; offline rules
  notify.py       WhatsApp adapters
  engine/         gemini_live.py, cascade.py, null.py
  telephony/      twilio.py, plivo.py, stream.py (the bridge)
  calls.py        start, run, finish a call; screening; retries; simulation
  scheduler.py    the minute tick
  web/app.py      webhooks, WebSocket, operator API, dashboard
scripts/seed.py   scripts/fake_phone.py   deploy/cloud_run.sh   tests/
```

## Licence

MIT.
