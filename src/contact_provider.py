"""Hunter.io people-search wrapper. Replaces the original Apollo client because
Apollo killed API access on their free tier in 2024.

Hunter endpoints used:
  - GET /v2/domain-search   — find emails + names + LinkedIn URLs at a company
  - GET /v2/email-finder    — find one specific person's email by name + domain

Free tier (as of 2025): 25 searches/mo + 50 verifications/mo. API access is
included on free, unlike Apollo. Each domain-search returns up to 10 emails
on free, up to 100 on paid. Get a key at https://hunter.io/api-keys

Compatibility shims:
  - `ApolloAuthError` is re-exported (as an alias for `ContactProviderAuthError`)
    so older imports keep working.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

import httpx

from .schemas import ContactCandidate, EducationEntry, EmploymentEntry

HUNTER_BASE = "https://api.hunter.io/v2"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class ContactProviderAuthError(Exception):
    pass


# Back-compat: older code imports ApolloAuthError.
ApolloAuthError = ContactProviderAuthError


def _api_key() -> str:
    key = os.getenv("HUNTER_API_KEY", "").strip()
    if not key:
        raise ContactProviderAuthError(
            "HUNTER_API_KEY not set. Sign up at https://hunter.io and paste your "
            "key into .env.local. Free tier includes 25 searches + 50 verifications/mo."
        )
    return key


def _get(path: str, params: dict) -> dict:
    params = {**params, "api_key": _api_key()}
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.get(f"{HUNTER_BASE}{path}", params=params)
    if r.status_code in (401, 403):
        raise ContactProviderAuthError(
            f"Hunter rejected the API key ({r.status_code}). Check it at https://hunter.io/api-keys."
        )
    if r.status_code == 429:
        raise RuntimeError(
            "Hunter rate-limit hit (429). Free tier is 25 searches/mo; wait or upgrade."
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Hunter {path} returned {r.status_code}: {r.text[:300]}")
    body = r.json()
    return body.get("data") or {}


# ── Role title → Hunter department mapping ───────────────────────────────
# Hunter's `department` param accepts a fixed taxonomy (comma-separated):
#   executive, it, finance, management, sales, legal, support, hr,
#   marketing, communication, education, design, health, operations, engineering
_DEPT_PATTERNS = [
    (r"\b(engineer|developer|sde|swe|scientist|architect|sre|devops|technical|software|"
     r"data|machine\s+learning|ml|ai|infrastructure|platform|backend|frontend|fullstack|"
     r"qa|test)\b", "engineering"),
    (r"\b(designer|design|ux|ui|product\s+designer|graphic|visual)\b", "design"),
    (r"\b(product\s+manager|product\s+lead|product\s+owner|pm\b|tpm)\b", "engineering"),  # PMs often searchable under engineering
    (r"\b(marketing|growth|brand|content|seo|sem|demand\s+gen|copywriter)\b", "marketing"),
    (r"\b(sales|account\s+executive|business\s+development|bdr|sdr|customer\s+success)\b", "sales"),
    (r"\b(recruiter|talent\s+(acquisition|partner|sourcer)?|sourcer|technical\s+recruiter|"
     r"head\s+of\s+talent|people\s+partner|people\s+ops|hr\b|human\s+resources)\b", "hr"),
    (r"\b(finance|accounting|controller|fp&a|treasurer|cfo)\b", "finance"),
    (r"\b(operations|coo|chief\s+of\s+staff|program\s+manager|ops\b)\b", "operations"),
    (r"\b(support|customer\s+success|csm|technical\s+support)\b", "support"),
    (r"\b(legal|counsel|paralegal|attorney)\b", "legal"),
    (r"\b(security|infosec|information\s+security|ciso)\b", "it"),
    (r"\b(executive|ceo|cto|cfo|coo|president|vp|vice\s+president|chief|founder)\b", "executive"),
    (r"\b(communication|pr\b|public\s+relations)\b", "communication"),
    (r"\b(education|teacher|professor|instructor)\b", "education"),
    (r"\b(medical|nurse|doctor|clinical|health)\b", "health"),
]


def _guess_departments(role_title: str) -> List[str]:
    """Return relevant Hunter department codes for a role title. Always
    includes `hr` so we get recruiters alongside the role's own department."""
    rt = (role_title or "").lower()
    out: List[str] = []
    for pat, dept in _DEPT_PATTERNS:
        if re.search(pat, rt) and dept not in out:
            out.append(dept)
    # Always include hr so recruiters / talent partners surface.
    if "hr" not in out:
        out.append("hr")
    # Hunter accepts comma-separated; cap at 3 to keep results focused.
    return out[:3]


# ── Domain resolution ────────────────────────────────────────────────────
def _resolve_domain_via_clearbit(company: str) -> Optional[str]:
    """Clearbit autocomplete is a public, no-auth endpoint that maps
    free-text company names to domains. We use it because Hunter's
    domain-search prefers a domain over a company name."""
    if not company.strip():
        return None
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0)) as c:
            r = c.get(
                "https://autocomplete.clearbit.com/v1/companies/suggest",
                params={"query": company.strip()},
            )
        if r.status_code == 200:
            arr = r.json()
            if arr and isinstance(arr, list):
                return (arr[0].get("domain") or None)
    except Exception:
        return None
    return None


# ── Main search ──────────────────────────────────────────────────────────
def search_contacts(
    company: str,
    role_title: str = "",
    role_keywords: Optional[List[str]] = None,
    per_page: int = 10,
) -> List[ContactCandidate]:
    """Return up to `per_page` candidates at the company, filtered by Hunter
    department codes inferred from the role title. Each result includes
    name + position + LinkedIn URL + email (Hunter returns emails inline).

    Hunter free tier caps results at 10 per call. Paid plans allow up to 100.
    """
    if not company.strip():
        return []

    domain = _resolve_domain_via_clearbit(company)
    departments = _guess_departments(role_title)
    # Hunter free tier rejects limit > 10 with a 400. Cap defensively.
    limit = max(1, min(per_page, 10))
    params: dict = {
        "limit": limit,
        "type": "personal",
    }
    if domain:
        params["domain"] = domain
    else:
        params["company"] = company.strip()
    if departments:
        params["department"] = ",".join(departments)

    data = _get("/domain-search", params)
    org_name = data.get("organization") or company
    emails = data.get("emails") or []
    out: List[ContactCandidate] = []
    for e in emails:
        try:
            out.append(_to_candidate(e, org_name))
        except Exception:
            # One malformed entry must not nuke the whole list.
            continue
    return out


def _to_candidate(e: dict, org_name: str) -> ContactCandidate:
    first = e.get("first_name") or ""
    last = e.get("last_name") or ""
    name = (f"{first} {last}").strip()
    if not name:
        name = e.get("value") or "Unknown"  # fall back to email local-part

    verification = e.get("verification") or {}
    email_value = e.get("value")
    email_status = verification.get("status")  # 'valid' | 'invalid' | 'accept_all' | 'webmail' | 'disposable' | 'unknown'

    department = e.get("department") or ""
    departments = [department] if department else []

    seniority = e.get("seniority") or None

    return ContactCandidate(
        apollo_id=None,
        name=name,
        title=e.get("position") or "",
        headline="",
        linkedin_url=e.get("linkedin"),
        email=email_value,
        email_status=email_status,
        organization_name=org_name,
        departments=departments,
        seniority=seniority,
        employment_history=[],  # Hunter doesn't expose career history
        education=[],           # Hunter doesn't expose education
        photo_url=None,
    )


# ── Reveal / find-by-name (used when search didn't return an email) ──────
def reveal_email(
    *,
    apollo_id: Optional[str] = None,   # ignored, kept for API compat
    linkedin_url: Optional[str] = None,  # ignored, Hunter doesn't take LI URL
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    organization_name: Optional[str] = None,
) -> dict:
    """Call Hunter's /v2/email-finder for a specific person. Counts against
    your monthly Hunter search quota (1 of the 25 free)."""
    if not first_name or not organization_name:
        raise RuntimeError("Hunter email-finder needs first_name + organization_name.")

    domain = organization_name.strip()
    if "." not in domain:
        resolved = _resolve_domain_via_clearbit(domain)
        if resolved:
            domain = resolved

    params: dict = {"domain": domain, "first_name": first_name}
    if last_name:
        params["last_name"] = last_name

    data = _get("/email-finder", params)
    email = data.get("email")
    verification = data.get("verification") or {}
    return {
        "email": email,
        "email_status": verification.get("status") or None,
    }


# Legacy shim: older code may import these for Apollo-shape calls.
def find_org_domain(company_name: str) -> Optional[str]:
    """Back-compat alias: resolve a company name to a primary domain.
    Kept so the sanity-check snippet in earlier docs still runs."""
    return _resolve_domain_via_clearbit(company_name)
