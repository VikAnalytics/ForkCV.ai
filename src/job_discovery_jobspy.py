"""Free alternative to Apify — uses python-jobspy to scrape LinkedIn,
Indeed, Glassdoor, and Google Jobs directly. No API key, no per-job cost.

Trade-off vs Apify: more fragile (sites change selectors), and LinkedIn is
aggressively rate-limited (often returns empty without backoff). Indeed and
Google Jobs are the most reliable in practice.

Defaults to scraping Indeed + LinkedIn + Google for each role × location
combo from your preferences.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlparse, urlunparse

from .schemas import DiscoveredJob, JobPreferences


def _sites_from_env() -> List[str]:
    """Comma-separated list of sites in JOBSPY_SITES env. Defaults to a balanced set."""
    raw = os.getenv("JOBSPY_SITES", "indeed,linkedin,google").strip()
    out = [s.strip().lower() for s in raw.split(",") if s.strip()]
    # Jobspy normalizes Google as 'google'.
    return out or ["indeed", "linkedin", "google"]


def _canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))
    except Exception:
        return url


def _dedup_key(url: str) -> str:
    canon = _canonical_url(url)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:16] if canon else ""


def _format_salary(min_amt, max_amt, interval, currency) -> str:
    """Jobspy returns salary as separate numeric fields. Reassemble into a
    human-readable string for storage + display."""
    cur = currency or "USD"
    if not min_amt and not max_amt:
        return ""
    def _fmt(v):
        try:
            return f"{int(float(v)):,}"
        except (TypeError, ValueError):
            return str(v)
    parts: List[str] = []
    if min_amt and max_amt:
        parts.append(f"{cur} {_fmt(min_amt)} - {_fmt(max_amt)}")
    elif max_amt:
        parts.append(f"{cur} up to {_fmt(max_amt)}")
    elif min_amt:
        parts.append(f"{cur} from {_fmt(min_amt)}")
    if interval:
        parts.append(f"({interval})")
    return " ".join(parts)


def discover_jobs_jobspy(prefs: JobPreferences, *, max_results: int = 100) -> List[DiscoveredJob]:
    """Run Jobspy across the configured sites for each role × location and
    return DiscoveredJobs. Empty/failed sites are skipped silently."""
    from jobspy import scrape_jobs  # lazy import — keeps server boot fast

    sites = _sites_from_env()
    hours_old = max(1, prefs.post_age_days_max * 24) if prefs.post_age_days_max else 168
    per_query = max(10, max_results // max(1, len(prefs.roles or [""])))
    country = os.getenv("JOBSPY_COUNTRY_INDEED", "USA").strip()

    out: List[DiscoveredJob] = []
    seen_keys: set[str] = set()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    roles = prefs.roles or [""]
    locations = prefs.locations or [""]

    for role in roles:
        for loc in locations:
            try:
                df = scrape_jobs(
                    site_name=sites,
                    search_term=role or None,
                    location=loc or None,
                    results_wanted=per_query,
                    hours_old=hours_old,
                    country_indeed=country,
                    linkedin_fetch_description=True,
                    is_remote=prefs.remote_ok or None,
                )
            except Exception:
                continue
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                url = str(row.get("job_url") or "")
                key = _dedup_key(url)
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)

                title = str(row.get("title") or "").strip()
                company = str(row.get("company") or "").strip()
                location = str(row.get("location") or loc or "").strip()
                description = str(row.get("description") or "")[:6000]
                date_posted = row.get("date_posted")
                posted = str(date_posted) if date_posted and str(date_posted) != "nan" else None
                salary = _format_salary(
                    row.get("min_amount"), row.get("max_amount"),
                    row.get("interval"), row.get("currency"),
                )

                out.append(DiscoveredJob(
                    id=uuid.uuid4().hex,
                    dedup_key=key,
                    source=f"jobspy-{str(row.get('site') or 'unknown')}",
                    source_id=str(row.get("id") or ""),
                    company=company,
                    title=title,
                    location=location,
                    posted_at=posted,
                    salary=salary or None,
                    description=description,
                    application_link=url,
                    discovered_at=now,
                    last_seen_at=now,
                ))
                if len(out) >= max_results:
                    return out
    return out
