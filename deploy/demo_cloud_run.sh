#!/usr/bin/env bash
# Deploy the shareable /try demo to Google Cloud Run.
#
# A single always-on instance keeps the demo's SQLite alive (session affinity + one
# instance). Demo mode disables the scheduler, so it never places a real call. Uses the
# Gemini API key, so no Vertex or GCP AI setup is needed — only a project to host the
# container and billing enabled (the free tier will not survive a shared link — see below).
#
#   PROJECT_ID=your-gcp-project GOOGLE_API_KEY=... deploy/demo_cloud_run.sh
#
# Output: a public https URL. Share <url>/try.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${PROJECT_ID:?set PROJECT_ID to your Google Cloud project}"
: "${GOOGLE_API_KEY:?set GOOGLE_API_KEY (from aistudio.google.com/apikey)}"
REGION="${REGION:-asia-south1}"
TOKEN="${SAHARA_OPERATOR_TOKEN:-$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')}"

gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com --project "$PROJECT_ID"
gcloud artifacts repositories describe sahara --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1 || \
  gcloud artifacts repositories create sahara --repository-format docker --location "$REGION" --project "$PROJECT_ID"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/sahara/sahara:latest"
gcloud builds submit --tag "$IMAGE" --project "$PROJECT_ID"

gcloud run deploy sahara-demo --image "$IMAGE" \
  --region "$REGION" --project "$PROJECT_ID" \
  --allow-unauthenticated --min-instances 1 --max-instances 1 \
  --timeout 300 --session-affinity --memory 512Mi \
  --set-env-vars "SAHARA_DEMO=1,GOOGLE_API_KEY=${GOOGLE_API_KEY},SAHARA_OPERATOR_TOKEN=${TOKEN},SAHARA_TIMEZONE=Asia/Kolkata,GEMINI_TEXT_MODEL=gemini-3.5-flash"

URL=$(gcloud run services describe sahara-demo --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')
echo
echo "  Deployed. Share this link:  ${URL}/try"
echo "  Operator desk (token-gated): ${URL}/   token: ${TOKEN}"
