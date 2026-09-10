#!/usr/bin/env bash
# Start Sahara locally.
#
# The Gemini key is read from the environment, and prompted for if it is missing,
# so it never lands in a file or in shell history. Everything else non-secret comes
# from .env. Serves HTTPS when cert.pem and key.pem exist, which the browser needs
# before it will hand the microphone to /mic over anything but localhost.
#
#   scripts/run.sh                  # https on 0.0.0.0:8090 if certs exist
#   PORT=9000 scripts/run.sh        # somewhere else
#   SAHARA_OFFLINE=1 scripts/run.sh # scripted engine, no key needed
set -euo pipefail
cd "$(dirname "$0")/.."

# Parse .env rather than sourcing it. Sourcing executes whatever is in the file, so a
# single unquoted value containing a space runs the rest of the line as a command — and
# prints your key in the error. Only well-formed NAME=VALUE lines are honoured.
if [ -f .env ]; then
  n=0
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in
      [A-Za-z_]*=*)
        name=${line%%=*}
        value=${line#*=}
        case "$value" in
          \"*\") value=${value#\"}; value=${value%\"} ;;
          \'*\') value=${value#\'}; value=${value%\'} ;;
        esac
        export "$name=$value"
        ;;
      *) echo "warning: .env line $n is not NAME=VALUE, ignoring it" >&2 ;;
    esac
  done < .env
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8090}"
PYTHON="${PYTHON:-.venv/bin/uvicorn}"
[ -x "$PYTHON" ] || PYTHON="uvicorn"

# Free the port first: uvicorn logs "address already in use" *after* it claims to have
# started, so a stale server silently keeps serving the old code.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PORT is in use; stopping the process holding it"
  pids=$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN || true)
  [ -n "$pids" ] && kill $pids || true
  sleep 1
fi

if [ -z "${GOOGLE_API_KEY:-}" ] && [ -z "${SAHARA_OFFLINE:-}" ]; then
  if [ -t 0 ]; then
    printf 'Gemini API key (GOOGLE_API_KEY), input hidden: '
    read -rs GOOGLE_API_KEY
    printf '\n'
    export GOOGLE_API_KEY
  else
    echo "error: GOOGLE_API_KEY is not set and there is no terminal to ask on." >&2
    echo "       export it, or run with SAHARA_OFFLINE=1 for the scripted engine." >&2
    exit 1
  fi
fi
[ -n "${GOOGLE_API_KEY:-}" ] && export GOOGLE_API_KEY

TLS=()
if [ -f cert.pem ] && [ -f key.pem ]; then
  TLS=(--ssl-keyfile key.pem --ssl-certfile cert.pem)
  scheme=https
else
  scheme=http
  echo "note: no cert.pem/key.pem, serving plain HTTP — /mic will not get the"
  echo "      microphone from anywhere but localhost."
fi

addr=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo localhost)
echo "desk: $scheme://$addr:$PORT      mic: $scheme://$addr:$PORT/mic"
exec "$PYTHON" sahara.web.app:app --host "$HOST" --port "$PORT" ${TLS[@]+"${TLS[@]}"}
