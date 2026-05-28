"""Check Application Default Credentials. Run:
    .venv/bin/python scripts/check_adc.py
"""
import google.auth

creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
print(f"quota project: {project}")
print(f"creds class:   {type(creds).__name__}")
print(f"valid:         {creds.valid if hasattr(creds, 'valid') else 'n/a'}")
