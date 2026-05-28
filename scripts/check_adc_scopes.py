"""Verify ADC account + scopes + Sheets API access. Run:
    .venv/bin/python scripts/check_adc_scopes.py
"""
import json
import os
import httpx
import google.auth
import google.auth.transport.requests

ADC_PATH = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")

with open(ADC_PATH) as f:
    data = json.load(f)
print(f"ADC account:        {data.get('account')}")
print(f"ADC quota project:  {data.get('quota_project_id')}")

creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
creds.refresh(google.auth.transport.requests.Request())
print(f"Token starts with:  {creds.token[:24]}...")

# Inspect what scopes the access token actually carries
r = httpx.get(
    "https://oauth2.googleapis.com/tokeninfo",
    params={"access_token": creds.token},
)
print(f"Tokeninfo HTTP:     {r.status_code}")
if r.status_code == 200:
    info = r.json()
    print(f"  email:            {info.get('email')}")
    print(f"  scopes (granted): {info.get('scope')}")
else:
    print(f"  body:             {r.text[:300]}")

# Final test: try a no-op Sheets API call (will 404 on dummy ID but proves auth+scope)
print()
print("--- Sheets API auth probe (404 = good, 403 = bad scope) ---")
r2 = httpx.get(
    "https://sheets.googleapis.com/v4/spreadsheets/dummy-id-for-probe",
    headers={"Authorization": f"Bearer {creds.token}"},
)
print(f"HTTP {r2.status_code}: {r2.text[:300]}")
