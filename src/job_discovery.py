"""Apify-backed job discovery. Wraps the apify/linkedin-jobs-scraper actor.

The actor signature is documented at https://apify.com/bebity/linkedin-jobs-scraper
(we use this one as the default — it's the most actively maintained free
LinkedIn-jobs actor; can be overridden with APIFY_ACTOR_ID).

Inputs (subset we use):
  - urls             : seeded LinkedIn search URLs (we build them from prefs)
  - count            : max results per run
  - scrapeCompany    : enrich with company info

Outputs are dataset items with fields like { title, company, location,
companyUrl, jobUrl, description, salary, postedAt, applyType, applicants, ...}.
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlencode, urlparse, urlunparse

import httpx

from .schemas import DiscoveredJob, JobPreferences

APIFY_BASE = "https://api.apify.com/v2"
RUN_TIMEOUT_SEC = 600  # wait up to 10 min for a run to complete


def _actor_id() -> str:
    """Lazy lookup so the env var is read fresh on each call (no stale module-import value)."""
    return os.getenv("APIFY_ACTOR_ID", "bebity~linkedin-jobs-scraper").strip()
POLL_INTERVAL_SEC = 5

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class ApifyAuthError(Exception):
    pass


def _token() -> str:
    t = os.getenv("APIFY_API_TOKEN", "").strip()
    if not t:
        raise ApifyAuthError(
            "APIFY_API_TOKEN not set. Sign up at https://console.apify.com and "
            "paste your key into .env.local."
        )
    return t


def _post(path: str, body: dict) -> dict:
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.post(f"{APIFY_BASE}{path}", json=body, params={"token": _token()})
    if r.status_code in (401, 403):
        raise ApifyAuthError(f"Apify rejected the token ({r.status_code}).")
    if r.status_code >= 400:
        raise RuntimeError(f"Apify {path} {r.status_code}: {r.text[:300]}")
    return r.json()


def _get(path: str, params: Optional[dict] = None) -> dict:
    p = {"token": _token(), **(params or {})}
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.get(f"{APIFY_BASE}{path}", params=p)
    if r.status_code in (401, 403):
        raise ApifyAuthError(f"Apify rejected the token ({r.status_code}).")
    if r.status_code >= 400:
        raise RuntimeError(f"Apify {path} {r.status_code}: {r.text[:300]}")
    return r.json()


def _get_raw(path: str, params: Optional[dict] = None) -> list:
    """Used for dataset/items where the response is a bare JSON array."""
    p = {"token": _token(), "format": "json", "clean": "true", **(params or {})}
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.get(f"{APIFY_BASE}{path}", params=p)
    if r.status_code in (401, 403):
        raise ApifyAuthError(f"Apify rejected the token ({r.status_code}).")
    if r.status_code >= 400:
        raise RuntimeError(f"Apify {path} {r.status_code}: {r.text[:300]}")
    return r.json() if r.text.strip() else []


# ── Build LinkedIn search URLs from prefs ────────────────────────────────
def _build_linkedin_search_urls(prefs: JobPreferences) -> List[str]:
    """For each (role, location) combination, build a LinkedIn jobs search URL.
    LI uses /jobs/search with query params; the Apify actor follows these URLs."""
    base = "https://www.linkedin.com/jobs/search"
    urls: List[str] = []
    roles = prefs.roles or [""]
    locs = prefs.locations or [""]
    for role in roles:
        for loc in locs:
            params = {
                "keywords": role,
                "location": loc,
                # LI's f_TPR filter for "posted in last X days"
                # r86400  = past 24h
                # r604800 = past week
                # r2592000 = past month
                "f_TPR": f"r{prefs.post_age_days_max * 86400}" if prefs.post_age_days_max else "",
                "f_WT": "2" if prefs.remote_ok else "",  # 2 = Remote on LI
            }
            params = {k: v for k, v in params.items() if v}
            urls.append(f"{base}?{urlencode(params)}")
    return urls


# ── Canonical URL → dedup key ────────────────────────────────────────────
def _canonical_url(url: str) -> str:
    """Strip tracking params + fragments so the same job posting hashes the same."""
    if not url:
        return ""
    try:
        p = urlparse(url)
        # Drop query + fragment entirely for LI job URLs (the job ID is in the path).
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))
    except Exception:
        return url


def _dedup_key(url: str) -> str:
    canon = _canonical_url(url)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:16] if canon else ""


# ── Run the actor + fetch results ────────────────────────────────────────
def _build_actor_input(prefs: JobPreferences, max_results: int) -> dict:
    """Different Apify LinkedIn actors take different input shapes. We send a
    superset that covers the popular ones — extra fields are ignored by actors
    that don't use them.
      - bebity~linkedin-jobs-scraper          : expects `urls[]` + `count`
      - curious_coder~linkedin-jobs-scraper   : expects `searches[]` + `rows`
      - apimaestro~linkedin-jobs-pagination…  : expects `searchUrl` (single)
    """
    urls = _build_linkedin_search_urls(prefs)
    searches = [
        {"query": role, "location": loc}
        for role in (prefs.roles or [""])
        for loc in (prefs.locations or [""])
        if role or loc
    ]
    # curious_coder enforces count >= 10; other actors don't mind larger values.
    safe_count = max(10, int(max_results))
    return {
        # bebity / generic
        "urls": urls,
        "count": safe_count,
        "scrapeCompany": False,
        # curious_coder
        "searches": searches,
        "rows": safe_count,
        # apimaestro (single URL form)
        "searchUrl": urls[0] if urls else "",
        # common knobs
        "maxItems": safe_count,
        "limit": safe_count,
        "proxy": {"useApifyProxy": True},
    }


def discover_jobs_apify(prefs: JobPreferences, *, max_results: int = 100) -> List[DiscoveredJob]:
    """Kick off the Apify actor with prefs-derived inputs and return parsed
    DiscoveredJob list. Blocks until the run completes (or RUN_TIMEOUT_SEC)."""
    urls = _build_linkedin_search_urls(prefs)
    if not urls:
        return []

    body = _build_actor_input(prefs, max_results)
    # Start the actor run (synchronous endpoint waits up to default; we use async).
    run = _post(f"/acts/{_actor_id()}/runs", body)
    run_id = (run.get("data") or {}).get("id")
    dataset_id = (run.get("data") or {}).get("defaultDatasetId")
    if not run_id:
        raise RuntimeError(f"Apify did not return a run id: {run}")

    # Poll until done.
    deadline = time.time() + RUN_TIMEOUT_SEC
    status = "RUNNING"
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SEC)
        info = _get(f"/actor-runs/{run_id}")
        data = info.get("data") or {}
        status = data.get("status") or status
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            dataset_id = data.get("defaultDatasetId") or dataset_id
            break
    if status != "SUCCEEDED":
        raise RuntimeError(f"Apify run ended with status {status}")
    if not dataset_id:
        return []

    items = _get_raw(f"/datasets/{dataset_id}/items")
    return [_to_discovered_job(it) for it in items if it]


# ── Parse one Apify dataset item ─────────────────────────────────────────
def _to_discovered_job(item: dict) -> DiscoveredJob:
    import uuid

    # Prefer the external/company-side apply URL when the actor returns one —
    # avoids LinkedIn-Easy-Apply rejection during auto-apply. Falls back to the
    # LI job URL only when no external link is available.
    url = (
        item.get("companyApplyUrl")
        or item.get("applyUrl")
        or item.get("externalApplyUrl")
        or item.get("link") or item.get("jobUrl") or item.get("url")
        or item.get("companyLinkedinUrl") or ""
    )
    company = (
        item.get("companyName") or item.get("company") or item.get("companyDetails", {}).get("name")
        if isinstance(item.get("companyDetails"), dict) else (item.get("companyName") or item.get("company"))
    ) or ""
    posted = (
        item.get("postedAt") or item.get("posted") or item.get("postedTime")
        or item.get("listedAt") or item.get("publishedAt") or None
    )
    salary = item.get("salary") or item.get("salaryInfo") or item.get("salaryRange")
    if isinstance(salary, dict):
        # Some actors return salary as a structured object.
        salary = salary.get("text") or " ".join(str(v) for v in salary.values() if v)
    elif isinstance(salary, list):
        salary = ", ".join(str(s) for s in salary if s)

    description = item.get("description") or item.get("descriptionText") or item.get("descriptionHtml") or ""
    if isinstance(description, dict):
        description = description.get("text") or description.get("html") or ""

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return DiscoveredJob(
        id=uuid.uuid4().hex,
        dedup_key=_dedup_key(url),
        source="apify-linkedin",
        source_id=str(item.get("id") or item.get("jobId") or item.get("urn") or ""),
        company=str(company).strip(),
        title=str(item.get("title") or "").strip(),
        location=str(item.get("location") or item.get("place") or "").strip(),
        posted_at=str(posted) if posted else None,
        salary=str(salary) if salary else None,
        description=str(description)[:6000],
        application_link=str(url),
        discovered_at=now,
        last_seen_at=now,
    )
