# Sahara — project brief and research record

*Compiled 10 September 2026 from the research, decisions and code produced in this working session.
Load this file into a Claude Desktop project as knowledge. The block below can be pasted into the
project's custom instructions.*

---

## Suggested project instructions (paste into Claude Desktop)

> You are working with Tapas on **Sahara**, an AI "digital child" for elderly parents in India who live
> apart from their children. Read `SAHARA_PROJECT.md` before answering. Ground every claim in the numbers
> and sources in that file or in fresh research; say when something is an estimate. The founding rule is
> the parent's trust: never propose deceiving the parent, and always keep DPDP consent and the 2026 IT Rules
> on synthetic media in view. Prefer the smallest experiment that answers the riskiest question. Current
> phase: **week-one pilot** (cloud voice agent, no hardware). Code lives in the `sahara/` repository.

---

## 1. The problem space: an Indian day in numbers

| Pain point | Evidence | Source |
|---|---|---|
| Unpaid household and care work | Women 289 min/day domestic work + 140 min caregiving; men 88 + 74. 41% of women 15–59 do daily caregiving | NSO Time Use Survey 2024 |
| Health costs | Out-of-pocket ≈ 39–47% of all health spending; hospitalisation ₹31,500 rural / ₹47,000 urban with 83–95% paid from pocket; ~55 million pushed into poverty yearly | Frontiers 2025/2026 systematic reviews |
| Insurance claims | ₹26,000 crore repudiated in FY24 (+19%); 8–15% of claims rejected; 32% of reimbursement rejections due to incomplete/illegible discharge summaries; IRDAI penalties for <95% settlement compliance from July 2026 | Business Standard, Ditto, OneAssure |
| Bureaucracy and bribes | 51–56% of citizens paid a bribe in the past year; property registration the top bribe venue; 37% paid during passport police verification; two-thirds of civil litigation is land-related | LocalCircles / Transparency International |
| Cyber fraud | ₹22,495 crore lost in 2025 across 28 lakh cases (+24%); UPI fraud ₹805 crore in Apr–Nov 2025; digital-arrest scams target the elderly | I4C via ThePrint, The420 |
| Commute | Bengaluru 168 h/year lost, Pune 152, Mumbai 126 | TomTom Traffic Index 2025 |
| Ageing | 173 million elderly in 2026, 300 million by 2050; 26.7% live alone (20% urban); only 17% have reliable healthcare access; 7–10 million have children abroad or in another city | NCAER, UNFPA, YourStory market estimate |
| Diagnostic delay | TB total delay 43–55 days median; 51–63% of patients postpone help >1 year for some conditions | PMC studies |

**Reading.** The commute is the one large pain software cannot fix. The rest cluster around three
moments: a parent ageing without you, a hospital bill, and a government counter.

## 2. The three business ideas, ranked by revenue potential

| # | Idea | Pain | Payer | Revenue estimate (assumptions) |
|---|---|---|---|---|
| 1 | **Sahara** — AI digital child for parents living alone | Ageing, scams, missed medication, hospital coordination | Adult child, often NRI | 10 M target elders × 10% × ₹2,500/mo ≈ **₹3,000 cr/yr** |
| 2 | **Sarkari Saathi** — agent that gets government work done at a published price | ~5 crore transactions/yr; bribes; property/mutation | Citizen; NRIs high-ARPU; B2B (KYC, onboarding) | 5 cr × 5% × ₹600 ≈ **₹1,500 cr/yr** + property/NRI margin |
| 3 | **Claim Guardian** — household health-finance agent | ₹26,000 cr repudiated; discharge-summary rejections | Family subscription; success fee; hospitals/TPAs; insurers (penalty regime) | ₹420 cr subs + ₹300–500 cr fees/B2B ≈ **₹800–1,000 cr/yr** |

Sahara absorbs pieces of the other two (scam protection, claim handling) as features for the elderly.

## 3. Sahara: product definition

**Who pays.** The adult child in Bengaluru, Dubai or New Jersey carrying guilt and risk: the parent who
skips medication, the digital-arrest call at 11 am, the admission nobody coordinated, the pension paperwork
that lapsed.

**What it does.**
- Daily voice call in the parent's mother tongue: sleep, food, medicines by name, pain/dizziness/falls,
  needs, then whatever they want to talk about. Notices confusion or breathlessness in the voice.
- Two-line summary to the child on WhatsApp; urgent alerts for chest pain, falls, scam contact.
- Call and payment guardian: screens unknown callers in real time (any Android, later the hub); holds
  first-time UPI transfers above a threshold until the child approves.
- Health coordination: books doctor visits, pulls ABHA-linked records (110 crore records linked as of
  mid-2026), briefs the child before appointments.
- Small human care-manager network for the physical last mile, dispatched by the agent.

**Why now.** Sarvam and AI4Bharat speech models at conversational latency; Gemini 3.1 Flash Live with
90+ languages including the major Indian ones; ABHA records at scale; Truecaller and Google shipping
family scam protection in 2026 as features, not a service; NCAER's call for an elder-care policy response.

**Competitors.** Emoha (~100k users, 120 cities, IoT + care), Samarth (30k elders, 110 cities,
₹200–15,000/month), Khyaal (3 M users, community app), Primus (senior living; demand 4–6 lakh units vs
20k supply). All human-heavy; Sahara's margin comes from AI doing the daily contact and humans handling
exceptions. Google Pixel scam detection (Gemini Nano, on-device, Pixel-only in India); Truecaller Family
Protection expanding to India in 2026.

**Business model.** ₹2,000–4,000/month paid by the child; premium tier with care-manager visits;
hospital and pharmacy referral income later. Hardware hub sold at ~₹4,999 or bundled with an annual plan
to halve churn.

**Main risk.** Trust after one missed emergency. Mitigation: escalate early and loudly; never claim to
replace a hospital; hard-coded emergency path that works without the cloud.

## 4. The hardware: a "family phone" hub (phase after the pilot)

Tabletop speakerphone that becomes the parent's landline and owns their number, so every call passes
through the agent. Three photo buttons call the children; a red button is emergency; a small display
shows time, today's medicine and who called. Optional wrist pendant for falls and SOS.

| Block | Choice | Why |
|---|---|---|
| Controller | ESP32-S3 class MCU, or Qualcomm QCM2290 for local models | Wake word, VAD, echo cancellation local; everything else in the cloud |
| Cellular | 4G LTE Cat-1bis with **VoLTE** (Quectel EC200 family or Indian supplier such as Cavli), Wi-Fi fallback | VoLTE is what makes the device the phone |
| Audio | 2–4 I2S MEMS mics, 3 W speaker, codec with hardware AEC | Elders speak quietly; AEC lets the agent listen while speaking |
| Controls | 3 photo buttons, 1 emergency button, rotary volume, 2.7" e-paper or 3.5" LCD | Physical, labelled, no touchscreen |
| Presence | 60 GHz mmWave radar (breathing, presence, fall) | No camera; the privacy line families accept |
| Health | BLE reads from BP monitor, glucometer, scale; snap-on sensed pillbox | Turns "did you take your medicine" into a fact |
| Power | Mains + 24–48 h battery backup | Power cuts are normal; the emergency call must still go out |

- **BOM** ≈ ₹3,000–4,500 at 10k units (LTE module the largest line, ₹900–1,400).
- **Certifications (India):** TEC mandatory testing for cellular equipment, WPC ETA for Wi-Fi/BLE, BIS
  registration for electronics and adapter, e-waste EPR, DPDP for audio/health data. Market fall and
  breathing detection as *alerts*, not diagnosis, to stay outside CDSCO medical-device rules. Budget 6–9
  months and a certification consultant.
- **Manufacturing:** Indian ODM/EMS (VVDN, Syrma SGS) with a type-approved LTE module; local content
  helps with operators and government schemes.
- **Distribution:** children buy, parents receive. Diaspora online plus an operator elder plan (Jio/Airtel)
  shipping the hub with an M2M SIM and billing the child.
- **Privacy rule as a feature:** audio leaves the device only during a call, after the wake word, or after
  the emergency button, with a visible light when streaming.
- **Phases:** bench kit (Raspberry Pi 5 + 4-mic array + 4G hat with VoLTE + buttons + display, ~₹25k) →
  custom board (months 3–6, 50 units, second city/language) → certified product (months 6–12, 1,000
  units, operator pilot).
- **Test early with:** hearing-impaired and Parkinsonian speech; a home with a ceiling fan, pressure cooker
  and television on.

## 5. The plan: prove the riskiest assumption first

The risk is not building a speakerphone. It is whether a 72-year-old picks up a daily call from an AI
and whether her son pays ₹2,000 a month for the summary.

| When | What | Metric |
|---|---|---|
| Week 1 | No-hardware test: 15–20 parents, one language, one city; daily call on their existing mobile via telephony API; WhatsApp summary to the child. Ask every child for a real ₹1,999 payment before the trial ends | Answer rate, call length, unprompted talk; paying children |
| Week 2 | Order the bench kit (~₹25k). Register the company; DPDP consent and data policy before the first recording; quotes from an ODM and a certification consultant | |
| Weeks 3–6 | First hub: same agent on the bench kit, calls arrive on the box, photo buttons, red button; 5 homes | Where it sits, fan noise, box vs room |
| Week 8 | **Decision gate:** renewals; answer rate >70%; at least one real catch (missed medicine, scam call, bad morning). Pass all three → commission the board | |

Budget under ₹3 lakh, two people: one who builds the voice agent, one who sits in parents' homes. The
second matters more.

## 6. What is built: the week-one pilot (`sahara/`)

**Architecture.** Scheduler (per-parent local call time, once a day, one retry after 30 min) → Twilio or
Plivo places the call → 8 kHz mu-law audio over a WebSocket → bridge converts to PCM 16 kHz → **Gemini
Live** (audio in/out, transcripts both ways, tool calls, barge-in) → facts logged via tools → typed summary
→ WhatsApp to the child (Meta Cloud API template or Twilio; console offline). Inbound unknown callers reach
a **screener persona** that connects, takes a message or blocks, with heuristics that can only raise the
model's scam risk. Consent gate on every call; spoken recording notice in the parent's language.

**Key facts baked in.**
- Live model: `gemini-3.1-flash-live-preview` (90+ languages incl. Hindi, Bengali, Tamil, Telugu,
  Marathi, Gujarati, Kannada, Malayalam); native-audio models pick the language from the prompt, so the
  parent's language is pinned in the persona. Alternate on Vertex:
  `gemini-live-2.5-flash-preview-native-audio-09-2025`. Gemini 3.x on Vertex is served from `global`.
- Cascade fallback: Sarvam Saarika/Saaras (ASR, 22 languages, v3 realtime over WebSocket) and Bulbul TTS
  (sub-250 ms, 11 languages) around Gemini text. Endpoint field names must be confirmed against
  docs.sarvam.ai (blocked from the build sandbox).
- Telephony: Twilio Media Streams (`<Connect><Stream>`) and Plivo Audio Streams (`<Stream bidirectional>`).
- WhatsApp: proactive messages to the child need an approved Meta template (`sahara_daily_summary`, one
  body parameter) or the Twilio sandbox for the pilot.

**Verified:** 18 tests (audio maths, scam heuristics in English and Hindi including the digital-arrest
script, rule-based summary, every webhook, screening override, WebSocket bridge end to end with the
offline engine, once-a-day scheduling); a fake-phone run against a live offline server.
**Not verified (needs accounts):** a real Gemini Live call; a real phone call. First live run will need
end-of-speech tuning for slow speakers (already set to low sensitivity).

**Run offline:** `pip install -e ".[test]"`, `SAHARA_OFFLINE=1 uvicorn sahara.web.app:app --port 8080`,
open the desk, add a family and a parent, press Simulate. **First live call:** Vertex project or Gemini
key; Twilio Indian number; ngrok for `SAHARA_PUBLIC_URL`; `scripts/seed.py --consent` with yourself as
the parent; `Call now`.

## 7. Decisions log

| Decision | Why |
|---|---|
| Prove answer rate and willingness to pay before any hardware | Riskiest assumption; hardware is the known part |
| Hub form factor that owns the parent's number, not a phone app or a wearable alone | Parent does nothing new; screening happens before the phone rings |
| Gemini Live as default engine, Sarvam cascade as fallback | Latency and barge-in; cascade is the path to on-device later |
| Facts through tools, not parsed from free text | Summaries never depend on transcript parsing |
| Heuristics may raise scam risk but never lower it | A naive "connect" for a bank-OTP caller is overridden |
| **No undisclosed voice cloning of the child** | Deceives a vulnerable person; trains the parent to accept cloned relatives' voices, the exact scam vector; IT Rules amendment (effective 20 Feb 2026) requires conspicuous labelling of synthetically generated information, so on a call the label must be spoken; DPDP consent must be informed |
| Instead: the son's real 30-second recorded greeting; a consistent Sahara voice per region; a family safe word | More effect on answer rate, no deception, turns scam risk into a feature |
| If a familiar voice is ever tested: disclosed every call ("speaking in Ravi's voice with his permission"), son's consent on file, check-in only, never the screener; cascade engine with ElevenLabs, Cartesia or self-hosted AI4Bharat IndicF5 | Legal and defensible; ~0.5 s more latency |
| Fall/breathing detection marketed as alerts, not diagnosis | Stays outside CDSCO medical-device rules |
| Audio never stored; transcripts kept 30 days; operator token on the API | DPDP minimisation |

## 8. Open questions and next steps

- [ ] Choose the first city and language from where recruits are, not market size.
- [ ] Create Vertex/Gemini and Twilio accounts; first call to yourself; tune end-of-speech and voice.
- [ ] Recruit 15–20 parents through personal network; consent script in their language.
- [ ] WhatsApp: Twilio sandbox for the pilot; submit the Meta template in parallel.
- [ ] Decide the pre-trial payment ask (₹1,999) and how it is collected.
- [ ] Confirm Sarvam endpoint shapes if the cascade is needed.
- [ ] Quotes: one ODM (VVDN / Syrma SGS), one certification consultant (TEC, WPC, BIS).
- [ ] Data policy under DPDP before the first recording; retention 30 days; deletion on request.
- [ ] Measure: answer rate, call length, unprompted-topic share, renewals at week four.
- [ ] Test speech quality with hearing-impaired and dialectal speakers early.

## 9. Sources

**Daily-life research**
- NSO Time Use Survey 2024 — https://www.drishtiias.com/daily-updates/daily-news-analysis/nso-time-use-survey-2024
- Public health financing and out-of-pocket spending, Frontiers 2026 — https://www.frontiersin.org/journals/public-health/articles/10.3389/fpubh.2026.1903360/full
- Out-of-pocket expenditure systematic review, Frontiers 2025 — https://www.frontiersin.org/journals/public-health/articles/10.3389/fpubh.2025.1594542/full
- Health claims rejection up 19.1% in FY24, Business Standard — https://www.business-standard.com/finance/personal-finance/health-insurance-claims-rejection-up-19-10-in-fy24-irdai-report-124122700754_1.html
- Ditto health insurance data lab — https://joinditto.in/health-insurance/data-lab/
- IRDAI annual report 2026 summary, OneAssure — https://www.oneassure.in/insurance/insurance-updates/irdai-annual-report-2026-summary
- 51% of Indians paid a bribe, Moneylife — https://www.moneylife.in/article/51-percentage-indians-admit-paying-a-bribe-in-past-12-months-survey/58782.html
- Passport police verification bribes, LocalCircles — https://www.localcircles.com/a/press/page/passport-seva-kendra-37-per-cent-citizens-paid-bribe-during-police-verification-for-passport-survey
- ₹22,495 crore lost to cyber fraud in 2025, ThePrint — https://theprint.in/india/cybercrime-saw-24-spike-in-2025-indians-lost-rs-22495-crore-mainly-in-investment-scams/2859930/
- TomTom Traffic Index 2025, Down To Earth — https://www.downtoearth.org.in/urbanisation/bengaluru-kolkata-among-worlds-slowest-cities-as-india-ranks-high-on-congestion-index
- India's silent elderly care crisis, NCAER — https://ncaer.org/publication/india-is-facing-a-silent-elderly-care-crisis-budget-2026-must-confront-it/
- Elder care startups, YourStory — https://yourstory.com/2024/12/reimagining-elder-care-startups-innovating-greying-population-senior
- Senior care in India booming, ThePrint — https://theprint.in/ground-reports/indias-silver-economy-is-booming-app-startups-part-time-daughters-dementia-centres/2533818/
- ABDM crosses 100 crore ABHA-linked records, eHealth — https://ehealth.eletsonline.com/2026/06/india-surpasses-100-crore-health-records-linked-to-abha-strengthening-the-worlds-largest-digital-health-ecosystem/
- Google AI scam protection in India, TechCrunch — https://techcrunch.com/2025/11/20/google-steps-up-ai-scam-protection-in-india-but-gaps-remain
- Truecaller family protection, TechCrunch — https://techcrunch.com/2026/03/12/truecallers-now-lets-you-hang-up-on-scammers-on-behalf-of-your-family/
- Landeed Terra, RealtyNMore — https://realtynmore.com/landeed-launches-terra-to-transform-fragmented/
- MSME credit gap, RXIL — https://www.rxil.in/financial-challenges-faced-by-msmes-in-india/

**Speech and models**
- Gemini 3.1 Flash Live preview — https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview
- Gemini Live API capabilities — https://ai.google.dev/gemini-api/docs/live-api/capabilities
- Gemini 2.5 Flash Live API on Vertex — https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/2-5-flash-live-api
- Search Live in Indian languages (powered by 3.1 Flash Live) — https://blog.google/intl/en-in/products/explore-communicate/search-live-comes-to-more-indian-languages/
- AI4Bharat IndicConformer — https://ai4bharat.iitm.ac.in/areas/model/ASR/IndicConformer/
- ai4bharat/indic-parler-tts — https://huggingface.co/ai4bharat/indic-parler-tts
- ai4bharat/indic-seamless — https://huggingface.co/ai4bharat/indic-seamless
- Sarvam models overview — https://docs.sarvam.ai/api/getting-started/models
- Saaras speech translation — https://docs.sarvam.ai/api-reference-docs/models/saaras

**Law**
- IT Rules 2026 deepfake regulation and AI labelling, Mondaq — https://www.mondaq.com/india/new-technology/1760554/it-rules-2026-deepfake-regulation-three-hour-takedowns-and-ai-labelling-obligations
- Synthetically generated information under the IT Amendment Rules 2026 — https://www.khuranaandkhurana.com/synthetically-generated-information-regulation-under-the-information-technology-amendment-rules-2

---

## Appendix: other work from the same session (for context, separate projects)

- **wound-proto** (`github.com/tapaskar/wound`): wound-measurement prototype — ArUco marker homography
  (area error 0.54% mean) + MobileSAM box-prompt segmentation; Phase 1 decoder fine-tune on FUSeg lifted
  per-wound Dice 0.860 → 0.901. imito's published model is DeepLabv3+/ResNet50 on ~4,000 wounds (Dice 92%).
  MedGemma is not a segmenter; MedSigLIP probe is the cheap tissue-classification experiment.
- **backlot** (`github.com/tapaskar/backlot`): Agentic Cinema hackathon entry, Grafana track — an AI
  production office for indie filmmakers on ADK + Gemini + Grafana MCP, with an Agent Studio front desk.
- **Vi AI-NOC capacity model**: GuideLLM-based GPU sizing (final 4× H200 for FM + Change Management,
  pan-India); IBM Plex/Carbon deck.
- **Speech-to-speech translation hardware**: cascade of IndicConformer → IndicTrans2 → Indic-Parler-TTS on a
  Jetson Orin Nano Super class board; latency budget ~1.1 s.
