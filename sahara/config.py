"""All configuration from the environment; see .env.example."""
from __future__ import annotations

import os
from pathlib import Path


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def flag(name: str, default: bool = False) -> bool:
    v = env(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


# --- runtime -----------------------------------------------------------------
OFFLINE = flag("SAHARA_OFFLINE")                    # no Gemini, no telephony, no WhatsApp
PUBLIC_URL = env("SAHARA_PUBLIC_URL", "http://localhost:8080")   # reachable by the telephony provider
DATA_DIR = Path(env("SAHARA_DATA_DIR", "data"))
DATABASE_URL = env("SAHARA_DATABASE_URL") or f"sqlite:///{DATA_DIR / 'sahara.db'}"
TIMEZONE = env("SAHARA_TIMEZONE", "Asia/Kolkata")
RETENTION_DAYS = int(env("SAHARA_RETENTION_DAYS", "30"))         # DPDP: keep transcripts briefly
OPERATOR_TOKEN = env("SAHARA_OPERATOR_TOKEN")                     # protects the dashboard/API

# --- Gemini ------------------------------------------------------------------
GEMINI_LIVE_MODEL = env("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
GEMINI_TEXT_MODEL = env("GEMINI_TEXT_MODEL", "gemini-3.5-flash")
GEMINI_VOICE = env("GEMINI_VOICE", "Kore")
GEMINI_LOCATION = env("GEMINI_LOCATION", "global")
GOOGLE_CLOUD_PROJECT = env("GOOGLE_CLOUD_PROJECT")

# --- voice engine: "gemini_live" (default) or "cascade" (Sarvam ASR/TTS + Gemini text)
VOICE_ENGINE = env("SAHARA_VOICE_ENGINE", "gemini_live")
SARVAM_API_KEY = env("SARVAM_API_KEY")
SARVAM_STT_MODEL = env("SARVAM_STT_MODEL", "saarika:v2.5")
SARVAM_TTS_MODEL = env("SARVAM_TTS_MODEL", "bulbul:v2")
SARVAM_TTS_SPEAKER = env("SARVAM_TTS_SPEAKER", "anushka")

# --- telephony: "twilio" or "plivo" -------------------------------------------
TELEPHONY = env("SAHARA_TELEPHONY", "twilio")
CALLER_ID = env("SAHARA_CALLER_ID")                                # the number Sahara calls from
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN")
PLIVO_AUTH_ID = env("PLIVO_AUTH_ID")
PLIVO_AUTH_TOKEN = env("PLIVO_AUTH_TOKEN")

# --- WhatsApp to the child: "meta" (Cloud API), "twilio", or "console" --------
WHATSAPP = env("SAHARA_WHATSAPP", "console")
META_WA_TOKEN = env("META_WA_TOKEN")
META_WA_PHONE_ID = env("META_WA_PHONE_ID")
META_WA_TEMPLATE = env("META_WA_TEMPLATE", "sahara_daily_summary")   # approved template name
TWILIO_WA_FROM = env("TWILIO_WA_FROM", "whatsapp:+14155238886")       # sandbox by default

# --- call behaviour ------------------------------------------------------------
MAX_CALL_SECONDS = int(env("SAHARA_MAX_CALL_SECONDS", "240"))
RETRY_AFTER_MINUTES = int(env("SAHARA_RETRY_AFTER_MINUTES", "30"))
MAX_ATTEMPTS = int(env("SAHARA_MAX_ATTEMPTS", "2"))
