"""Score + rank contact candidates for outreach.

Signals (max 100):
  - Title relevance to role  ............................. 40
  - Team / department match .............................. 25
  - Shared school or past employer with the user ......... 20
  - Tenure sweet spot (6mo-3yr at current company) ....... 15

The scorer also produces a list of human-readable "shared signals" the
outreach generator can weave into the message ("we both went to UT
Austin", "you joined Stripe in 2024 from Databricks where I interned").
"""
from __future__ import annotations

import re
from datetime import date
from typing import List, Optional, Tuple

from .schemas import (
    ContactCandidate,
    EducationEntry,
    JDAnalysis,
    MasterCV,
    ScoredContact,
)

RECRUITER_RE = re.compile(
    r"\b(recruiter|talent\s+(acquisition|partner|sourcer)|sourcer|technical\s+recruiter|"
    r"head\s+of\s+talent|people\s+partner|university\s+recruiting)\b",
    re.IGNORECASE,
)
HIRING_LEAD_RE = re.compile(
    r"\b(engineering\s+manager|manager\s+of|head\s+of|director|vp|vice\s+president|"
    r"chief|principal|staff)\b",
    re.IGNORECASE,
)


# ── User-side signals (pulled from MasterCV) ─────────────────────────────
def _user_schools(master: MasterCV) -> List[str]:
    return [(e.institution or "").strip() for e in (master.education or []) if (e.institution or "").strip()]


def _user_past_companies(master: MasterCV) -> List[str]:
    return [(e.company or "").strip() for e in (master.experience or []) if (e.company or "").strip()]


# ── Title categorization ─────────────────────────────────────────────────
def _categorize(candidate: ContactCandidate, role_title: str) -> str:
    title = (candidate.title or "").lower()
    if RECRUITER_RE.search(title):
        return "recruiter"
    rt = (role_title or "").lower().strip()
    role_tokens = [t for t in re.findall(r"\w+", rt) if len(t) >= 4]
    role_keyword_in_title = any(tok in title for tok in role_tokens) if role_tokens else False
    has_lead = bool(HIRING_LEAD_RE.search(title))
    if rt and role_keyword_in_title and has_lead:
        return "hiring_manager"
    if rt and rt in title:
        return "team_ic"
    if role_keyword_in_title:
        return "team_ic"
    return "other"


# ── Tenure ───────────────────────────────────────────────────────────────
def _parse_month(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})", s)
    if not m:
        return None
    y, mo = int(m.group(1)), max(1, min(12, int(m.group(2))))
    try:
        return date(y, mo, 1)
    except Exception:
        return None


def _tenure_months_current(candidate: ContactCandidate) -> int:
    for e in candidate.employment_history:
        if not e.current:
            continue
        start = _parse_month(e.start_date)
        if not start:
            continue
        today = date.today()
        months = (today.year - start.year) * 12 + (today.month - start.month)
        return max(0, months)
    return 0


# ── Shared signals ───────────────────────────────────────────────────────
def _name_key(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\b(university|college|inc|inc\.|llc|ltd|corp|corporation)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _shared_schools(candidate: ContactCandidate, user_schools: List[str]) -> List[str]:
    out: List[str] = []
    cand_keys = {_name_key(ed.school or "") for ed in candidate.education}
    for s in user_schools:
        k = _name_key(s)
        if k and k in cand_keys:
            out.append(s)
    return out


def _shared_past_companies(candidate: ContactCandidate, user_companies: List[str]) -> List[str]:
    out: List[str] = []
    cand_orgs = []
    for e in candidate.employment_history:
        if e.organization_name:
            cand_orgs.append(_name_key(e.organization_name))
    cand_set = set(cand_orgs)
    for c in user_companies:
        k = _name_key(c)
        if k and k in cand_set:
            out.append(c)
    return out


# ── Team / department match ──────────────────────────────────────────────
def _team_match(candidate: ContactCandidate, jd: Optional[JDAnalysis]) -> Tuple[bool, str]:
    if not jd:
        return False, ""
    blob = " ".join([
        candidate.title or "",
        candidate.headline or "",
        " ".join(candidate.departments or []),
    ]).lower()
    if not blob.strip():
        return False, ""

    tokens: List[str] = []
    if jd.role_title:
        tokens += re.findall(r"\w+", jd.role_title.lower())
    for kw in (jd.core_impact_areas or [])[:6]:
        tokens += re.findall(r"\w+", kw.lower())
    if jd.domain:
        tokens += re.findall(r"\w+", jd.domain.lower())
    tokens = [t for t in tokens if len(t) >= 4]
    seen = set()
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        if t in blob:
            return True, t
    return False, ""


# ── Scoring ──────────────────────────────────────────────────────────────
def score_candidate(
    candidate: ContactCandidate,
    master: MasterCV,
    role_title: str,
    jd: Optional[JDAnalysis] = None,
) -> ScoredContact:
    breakdown: dict = {}
    shared: List[str] = []

    # 1. Title relevance — 40 pts max
    category = _categorize(candidate, role_title)
    title_pts = {"recruiter": 40, "hiring_manager": 35, "team_ic": 25, "other": 8}.get(category, 0)
    breakdown["title_relevance"] = title_pts

    # 2. Team / department match — 25 pts
    team_hit, team_token = _team_match(candidate, jd)
    team_pts = 25 if team_hit else 0
    breakdown["team_match"] = team_pts
    if team_hit and team_token:
        shared.append(f"works on {team_token}")

    # 3. Shared school / past employer — 20 pts (split: 12 school, 8 employer; both → 20)
    user_schools = _user_schools(master)
    user_companies = _user_past_companies(master)
    shared_schools = _shared_schools(candidate, user_schools)
    shared_companies = _shared_past_companies(candidate, user_companies)
    school_pts = 12 if shared_schools else 0
    company_pts = 8 if shared_companies else 0
    breakdown["shared_school"] = school_pts
    breakdown["shared_past_employer"] = company_pts
    for s in shared_schools:
        shared.append(f"shared school: {s}")
    for c in shared_companies:
        shared.append(f"shared past employer: {c}")

    # 4. Tenure sweet spot — 15 pts
    tenure = _tenure_months_current(candidate)
    if 6 <= tenure <= 36:
        tenure_pts = 15
    elif 3 <= tenure < 6 or 36 < tenure <= 60:
        tenure_pts = 8
    else:
        tenure_pts = 0
    breakdown["tenure"] = tenure_pts

    total = min(100, title_pts + team_pts + school_pts + company_pts + tenure_pts)

    return ScoredContact(
        contact=candidate,
        score=total,
        score_breakdown=breakdown,
        tenure_months=tenure,
        category=category,
        shared_signals=shared,
    )


def rank_candidates(
    candidates: List[ContactCandidate],
    master: MasterCV,
    role_title: str,
    jd: Optional[JDAnalysis] = None,
    top_k: int = 10,
) -> List[ScoredContact]:
    scored = [score_candidate(c, master, role_title, jd) for c in candidates]
    # Sort by score desc, then by category priority, then by name for stability.
    cat_pri = {"recruiter": 0, "hiring_manager": 1, "team_ic": 2, "other": 3}
    scored.sort(key=lambda s: (-s.score, cat_pri.get(s.category, 4), s.contact.name.lower()))
    return scored[:top_k]
