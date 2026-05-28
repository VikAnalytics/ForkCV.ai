"""Score a DiscoveredJob against the user's JobPreferences + MasterCV.

Pipeline:
  1. Hard filters — reject jobs that don't meet visa/language/YoE/keyword/
     company constraints. Sets `rejected=True` + `rejection_reason`.
  2. Soft score (0-100) — keyword overlap with master CV bullets + skills +
     role-title match + salary signal + recency.

Caller is expected to have already run sponsorship + language + YoE
extractors and populated those fields on the DiscoveredJob.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .schemas import DiscoveredJob, JobPreferences, MasterCV


def _norm_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) >= 3}


def _master_corpus_tokens(master: MasterCV) -> set[str]:
    parts: List[str] = []
    if master.professional_summary:
        parts.append(master.professional_summary)
    for sk in master.skills or []:
        parts.append(sk.category)
        parts.extend(sk.items or [])
    for e in master.experience or []:
        parts.append(e.role or "")
        for b in e.bullet_pool or []:
            parts.append(b.text)
            parts.extend(b.tags or [])
    for p in master.projects or []:
        parts.append(p.name or "")
        for b in p.bullet_pool or []:
            parts.append(b.text)
            parts.extend(b.tags or [])
    return _norm_tokens(" ".join(parts))


def _normalize_lang(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def apply_hard_filters(
    job: DiscoveredJob, prefs: JobPreferences
) -> Tuple[bool, str]:
    """Return (passes, rejection_reason). True if the job passes all hard filters."""
    title_lc = (job.title or "").lower()
    blob_lc = f"{job.title} {job.description}".lower()
    company_lc = (job.company or "").lower()

    # Companies-exclude — title-insensitive substring match against company name.
    if prefs.companies_exclude:
        for blocked in prefs.companies_exclude:
            if blocked and blocked.strip().lower() in company_lc:
                return False, f"company blocked ({blocked})"

    # Keywords-exclude — any match in title or JD → reject.
    if prefs.keywords_exclude:
        for kw in prefs.keywords_exclude:
            kw_lc = (kw or "").strip().lower()
            if kw_lc and kw_lc in blob_lc:
                return False, f"excluded keyword ({kw})"

    # Visa: if user needs sponsorship and JD explicitly says no.
    if prefs.visa_sponsorship_needed and job.sponsorship_status == "not_available":
        return False, "no visa sponsorship offered"

    # Languages — every JD-required language must be in user's list.
    if job.languages_required:
        my = {_normalize_lang(x) for x in prefs.my_languages}
        for lang in job.languages_required:
            if _normalize_lang(lang) not in my:
                return False, f"requires language not in your list ({lang})"

    # Years of experience — JD asks for more than user's cap.
    if prefs.max_required_yoe is not None and job.yoe_required is not None:
        if job.yoe_required > prefs.max_required_yoe:
            return False, f"requires {job.yoe_required}+ YoE (over your cap {prefs.max_required_yoe})"

    # Keywords-include — must contain at least ONE if list is non-empty.
    if prefs.keywords_include:
        if not any((kw or "").strip().lower() in blob_lc for kw in prefs.keywords_include):
            return False, "no required-keyword match"

    return True, ""


# ── Soft score (0-100) ───────────────────────────────────────────────────
def score_job(job: DiscoveredJob, prefs: JobPreferences, master: MasterCV) -> Tuple[int, dict]:
    """Return (score, breakdown). Components:
       - Master-CV token overlap with JD       : 40
       - Role-title match against prefs.roles  : 25
       - companies_include match               : 10
       - keywords_include hit count            : 10
       - Salary at or above min                : 8
       - Posted recency (<3 days)              : 7
    """
    breakdown: dict = {}

    # 1) Master CV ↔ JD token overlap (40)
    master_toks = _master_corpus_tokens(master)
    jd_toks = _norm_tokens(f"{job.title} {job.description}")
    if master_toks and jd_toks:
        overlap = len(master_toks & jd_toks)
        # Saturate at 40 overlapping tokens for full marks; small jobs scale linearly.
        master_pts = min(40, int(round(40 * overlap / max(40, len(jd_toks) // 4))))
    else:
        master_pts = 0
    breakdown["master_overlap"] = master_pts

    # 2) Role-title match (25)
    title_lc = (job.title or "").lower()
    role_pts = 0
    for role in prefs.roles or []:
        rl = (role or "").strip().lower()
        if not rl:
            continue
        if rl == title_lc:
            role_pts = 25
            break
        if rl in title_lc:
            role_pts = max(role_pts, 20)
        # Token overlap
        rt = _norm_tokens(rl)
        tt = _norm_tokens(title_lc)
        if rt and tt and len(rt & tt) >= max(1, len(rt) // 2):
            role_pts = max(role_pts, 15)
    breakdown["role_title_match"] = role_pts

    # 3) Companies-include bonus (10)
    company_lc = (job.company or "").lower()
    inc_pts = 0
    for c in prefs.companies_include or []:
        cl = (c or "").strip().lower()
        if cl and cl in company_lc:
            inc_pts = 10
            break
    breakdown["company_include_bonus"] = inc_pts

    # 4) Keywords-include hit count (10) — up to 5 hits give full marks
    kw_hits = 0
    blob_lc = f"{job.title} {job.description}".lower()
    for kw in prefs.keywords_include or []:
        kl = (kw or "").strip().lower()
        if kl and kl in blob_lc:
            kw_hits += 1
    kw_pts = min(10, 2 * kw_hits)
    breakdown["keyword_include_hits"] = kw_pts

    # 5) Salary signal (8) — extract any 4-6 digit number; if all >= min, full marks
    sal_pts = 0
    if prefs.salary_min_usd and job.salary:
        nums = [int(m.group()) for m in re.finditer(r"\b(\d{2,3}[,\.]?\d{3})\b", job.salary.replace(",", ""))]
        cleaned: List[int] = []
        for raw in re.findall(r"\d{2,3}[,]?\d{3}", job.salary):
            try:
                cleaned.append(int(raw.replace(",", "")))
            except ValueError:
                continue
        if cleaned and min(cleaned) >= prefs.salary_min_usd:
            sal_pts = 8
        elif cleaned and max(cleaned) >= prefs.salary_min_usd:
            sal_pts = 4  # in-range
    breakdown["salary_signal"] = sal_pts

    # 6) Recency (7) — only if posted_at parseable
    rec_pts = 0
    if job.posted_at:
        s = job.posted_at.lower()
        if "today" in s or "1 day" in s or "yesterday" in s or "hours" in s or "hour" in s:
            rec_pts = 7
        elif "2 day" in s or "3 day" in s:
            rec_pts = 5
        elif "week" in s:
            rec_pts = 2
    breakdown["recency"] = rec_pts

    total = min(100, master_pts + role_pts + inc_pts + kw_pts + sal_pts + rec_pts)
    return total, breakdown
