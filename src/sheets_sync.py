"""Google Sheets sync for discovered jobs. Uses a service-account JSON key
(no OAuth dance) — simpler for a local tool.

Setup (one-time, ~5 min):
  1. Create a Google Cloud project at https://console.cloud.google.com
  2. Enable the Sheets API: APIs & Services → Library → "Google Sheets API" → Enable
  3. Create a service account: IAM & Admin → Service Accounts → Create
  4. Generate a JSON key: Keys → Add Key → JSON; download to e.g. data/sheets-sa.json
  5. Open your target Google Sheet, share it (Editor) with the service-account
     email (looks like xxxx@your-project.iam.gserviceaccount.com)
  6. Set env vars in .env.local:
       GOOGLE_SERVICE_ACCOUNT_JSON=data/sheets-sa.json
       SHEETS_SPREADSHEET_ID=<the id from the sheet's URL>
       SHEETS_RANGE=Sheet1!A1   (optional; defaults to Sheet1!A1)
"""
from __future__ import annotations

import os
from typing import List, Optional

from .schemas import DiscoveredJob


SHEET_HEADERS = [
    "Discovered at",
    "Company",
    "Title",
    "Location",
    "Posted",
    "Salary",
    "Sponsorship",
    "Languages",
    "YoE",
    "Score",
    "Application link",
    "Applied",
    "Resume generation_id",
]


# ── Region routing ───────────────────────────────────────────────────────
# Order matters: UK is matched before Europe so "London" doesn't fall into Europe.
import re as _re

_US_STATES_RE = _re.compile(
    r"\b(?:al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|ms|mo|"
    r"mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy)\b",
    _re.IGNORECASE,
)


def _is_uk(loc: str) -> bool:
    return bool(_re.search(
        r"\b(united\s+kingdom|england|scotland|wales|northern\s+ireland|"
        r"london|manchester|edinburgh|birmingham|glasgow|leeds|liverpool|bristol|sheffield|cambridge|oxford)\b",
        loc, _re.IGNORECASE,
    )) or _re.search(r"(^|[\s,])uk([\s,]|$)", loc, _re.IGNORECASE) is not None


def _is_uae(loc: str) -> bool:
    return bool(_re.search(
        r"\b(united\s+arab\s+emirates|uae|dubai|abu\s+dhabi|sharjah|ajman)\b",
        loc, _re.IGNORECASE,
    ))


def _is_aunz(loc: str) -> bool:
    return bool(_re.search(
        r"\b(australia|aus\b|new\s+zealand|nz\b|sydney|melbourne|brisbane|perth|adelaide|"
        r"canberra|auckland|wellington|christchurch)\b",
        loc, _re.IGNORECASE,
    ))


def _is_us(loc: str) -> bool:
    if _re.search(
        r"\b(united\s+states|u\.?s\.?a?|america|washington,?\s*d\.?c\.?|california|"
        r"new\s+york|texas|florida|illinois|massachusetts|pennsylvania|virginia|georgia|colorado|"
        r"washington\s+state|north\s+carolina|arizona|oregon|ohio|michigan|minnesota|utah|tennessee)\b",
        loc, _re.IGNORECASE,
    ):
        return True
    # Two-letter state codes (only safe if location contains a comma — avoids matching random tokens)
    if "," in loc and _US_STATES_RE.search(loc):
        return True
    return False


def _is_europe(loc: str) -> bool:
    return bool(_re.search(
        r"\b(europe|european\s+union|eu\b|germany|france|spain|italy|netherlands|sweden|"
        r"norway|denmark|finland|poland|portugal|ireland|belgium|austria|switzerland|czech|"
        r"romania|hungary|greece|berlin|paris|amsterdam|dublin|madrid|barcelona|rome|milan|"
        r"munich|frankfurt|hamburg|stockholm|copenhagen|oslo|helsinki|warsaw|prague|vienna|"
        r"zurich|geneva|brussels|lisbon|porto|athens|budapest|bucharest)\b",
        loc, _re.IGNORECASE,
    ))


REGION_TABS = ["US", "Europe", "UAE", "Australia & NZ", "UK", "Other"]


def classify_region(location: str) -> str:
    if not location:
        return "Other"
    if _is_uk(location):
        return "UK"
    if _is_uae(location):
        return "UAE"
    if _is_aunz(location):
        return "Australia & NZ"
    if _is_us(location):
        return "US"
    if _is_europe(location):
        return "Europe"
    return "Other"


_ADC_DEFAULT_PATH = os.path.expanduser(
    "~/.config/gcloud/application_default_credentials.json"
)
_TOKEN_PATH = "data/sheets_token.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _has_adc() -> bool:
    return os.path.exists(_ADC_DEFAULT_PATH)


def _has_cached_token() -> bool:
    return os.path.exists(_TOKEN_PATH)


def is_configured() -> bool:
    sheet = os.getenv("SHEETS_SPREADSHEET_ID", "").strip()
    key = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    return bool(sheet and (key or _has_cached_token() or _has_adc()))


def _client():
    """Credential search order:
      1. GOOGLE_SERVICE_ACCOUNT_JSON env var (org-key flow)
      2. data/sheets_token.json (OAuth flow via scripts/sheets_oauth.py)
      3. gcloud Application Default Credentials
    """
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            "google-api-python-client not installed. "
            "Run: .venv/bin/pip install google-api-python-client google-auth google-auth-oauthlib"
        ) from e

    # 1) Service account JSON.
    key_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if key_path:
        if not os.path.exists(key_path):
            raise RuntimeError(f"Service-account JSON not found at {key_path}.")
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    # 2) Cached OAuth token from scripts/sheets_oauth.py.
    if _has_cached_token():
        from google.oauth2.credentials import Credentials
        import google.auth.transport.requests
        creds = Credentials.from_authorized_user_file(_TOKEN_PATH, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
            # Persist the refreshed token.
            with open(_TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    # 3) ADC fallback.
    if _has_adc():
        import google.auth
        creds, _project = google.auth.default(scopes=SCOPES)
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    raise RuntimeError(
        "No Google credentials found. Run: .venv/bin/python scripts/sheets_oauth.py "
        "(after downloading data/oauth-client.json from GCP)."
    )


def _row_for_job(j: DiscoveredJob) -> list:
    return [
        j.discovered_at,
        j.company,
        j.title,
        j.location,
        j.posted_at or "",
        j.salary or "",
        j.sponsorship_status,
        ", ".join(j.languages_required or []),
        j.yoe_required if j.yoe_required is not None else "",
        j.score,
        j.application_link,
        "yes" if j.applied else "",
        j.generation_id or "",
    ]


def _ensure_tabs(svc, spreadsheet_id: str, needed: list) -> None:
    """Create any missing tabs in the spreadsheet. Existing tabs untouched."""
    meta = svc.get(spreadsheetId=spreadsheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    to_create = [t for t in needed if t not in existing]
    if not to_create:
        return
    requests = [{"addSheet": {"properties": {"title": t}}} for t in to_create]
    svc.batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


def _normalize_sheet_id(raw: str) -> str:
    """Accept either the bare ID or a full Sheets URL pasted into env."""
    s = (raw or "").strip()
    if not s:
        return s
    # https://docs.google.com/spreadsheets/d/<ID>/edit#... → <ID>
    import re as _re
    m = _re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    return s


def sync_jobs(jobs: List[DiscoveredJob]) -> dict:
    """Push current discovered jobs to the configured spreadsheet, routed by
    region into 5 named tabs (US / Europe / UAE / Australia & NZ / UK) plus
    an "Other" catch-all. Rejected jobs are NOT pushed — local UI still shows
    them with their rejection reason, but the Sheet stays focused on the
    actionable ones."""
    if not is_configured():
        raise RuntimeError("Sheets not configured. Set SHEETS_SPREADSHEET_ID + auth credentials.")

    spreadsheet_id = _normalize_sheet_id(os.getenv("SHEETS_SPREADSHEET_ID", ""))
    svc = _client().spreadsheets()

    # 1) Ensure all region tabs exist (no-op if already present).
    _ensure_tabs(svc, spreadsheet_id, REGION_TABS)

    # 2) Drop rejected jobs.
    actionable = [j for j in jobs if not j.rejected]
    actionable.sort(key=lambda j: j.discovered_at or "", reverse=True)

    # 3) Bucket by region.
    by_region: dict = {t: [] for t in REGION_TABS}
    for j in actionable:
        by_region[classify_region(j.location)].append(j)

    # 4) For each tab: clear + write header + rows.
    rows_written = 0
    for tab in REGION_TABS:
        clear_range = f"'{tab}'!A1:Z100000"
        svc.values().clear(spreadsheetId=spreadsheet_id, range=clear_range, body={}).execute()
        values = [SHEET_HEADERS] + [_row_for_job(j) for j in by_region[tab]]
        svc.values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()
        rows_written += len(values) - 1  # exclude header row

    return {
        "rows_written": rows_written,
        "tabs": {t: len(by_region[t]) for t in REGION_TABS},
        "skipped_rejected": sum(1 for j in jobs if j.rejected),
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}",
    }


def status_dict() -> dict:
    key = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "") or None
    if key:
        mode = "service_account"
    elif _has_cached_token():
        mode = "oauth_token"
    elif _has_adc():
        mode = "adc"
    else:
        mode = "none"
    return {
        "configured": is_configured(),
        "spreadsheet_id": os.getenv("SHEETS_SPREADSHEET_ID", "") or None,
        "range": os.getenv("SHEETS_RANGE", "Sheet1!A1"),
        "service_account_path": key,
        "auth_mode": mode,
    }
