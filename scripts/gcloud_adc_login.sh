#!/usr/bin/env bash
# Re-auth ADC with the full scope list ForkCV needs.
set -euo pipefail

SCOPES=(
  "openid"
  "https://www.googleapis.com/auth/userinfo.email"
  "https://www.googleapis.com/auth/cloud-platform"
  "https://www.googleapis.com/auth/spreadsheets"
  "https://www.googleapis.com/auth/drive.file"
)

# Join with commas — no line wrapping.
SCOPES_CSV=$(IFS=, ; echo "${SCOPES[*]}")

echo "Revoking stale ADC token…"
gcloud auth application-default revoke --quiet || true

echo "Logging in with scopes: $SCOPES_CSV"
gcloud auth application-default login --scopes="$SCOPES_CSV"

echo "Setting quota project…"
gcloud auth application-default set-quota-project forkcv-497017

echo
echo "Done. Verify with:"
echo "  .venv/bin/python scripts/check_adc_scopes.py"
