#!/usr/bin/env bash
# Deploy Sahara to Cloud Run with a persistent SQLite volume is not advisable; use
# Cloud SQL (SAHARA_DATABASE_URL=postgresql+psycopg://...) for anything past the pilot.
# For week one, a single always-on instance with min-instances=1 keeps the scheduler alive.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${PROJECT_ID:?set PROJECT_ID}"; REGION="${REGION:-asia-south1}"
set -a; source .env; set +a
gcloud services enable run.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com cloudbuild.googleapis.com --project "$PROJECT_ID"
gcloud artifacts repositories describe sahara --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1 || \
  gcloud artifacts repositories create sahara --repository-format docker --location "$REGION" --project "$PROJECT_ID"
gcloud builds submit --tag "${REGION}-docker.pkg.dev/${PROJECT_ID}/sahara/sahara:latest" --project "$PROJECT_ID"
gcloud run deploy sahara --image "${REGION}-docker.pkg.dev/${PROJECT_ID}/sahara/sahara:latest" \
  --region "$REGION" --project "$PROJECT_ID" --allow-unauthenticated --min-instances 1 --max-instances 1 \
  --timeout 600 --session-affinity --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GEMINI_LOCATION=global,SAHARA_TIMEZONE=Asia/Kolkata,SAHARA_TELEPHONY=${SAHARA_TELEPHONY},SAHARA_WHATSAPP=${SAHARA_WHATSAPP},SAHARA_CALLER_ID=${SAHARA_CALLER_ID},SAHARA_OPERATOR_TOKEN=${SAHARA_OPERATOR_TOKEN},TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID},TWILIO_AUTH_TOKEN=${TWILIO_AUTH_TOKEN}"
URL=$(gcloud run services describe sahara --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')
gcloud run services update sahara --region "$REGION" --project "$PROJECT_ID" --update-env-vars "SAHARA_PUBLIC_URL=${URL}"
echo "$URL"
