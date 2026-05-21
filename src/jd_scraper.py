"""Scrape job descriptions from URLs (LinkedIn + generic career sites).

Returns ScrapedJob{url, company, jd, source, error} so the bulk endpoint can
report per-URL outcomes without failing the whole batch.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(20.0, connect=10.0)

LI_JOB_ID_RE = re.compile(r"(?:jobs/view/|currentJobId=|/jobs/(?:collections/[^?]+\?currentJobId=))(\d{6,})")
LI_FALLBACK_ID_RE = re.compile(r"/(\d{8,})(?:[/?]|$)")


@dataclass
class ScrapedJob:
    url: str
    company: str = ""
    jd: str = ""
    source: str = ""  # "linkedin-guest" | "linkedin-auth" | "generic"
    error: Optional[str] = None

    def to_dict(self):
        return {
            "url": self.url,
            "company": self.company,
            "jd": self.jd,
            "source": self.source,
            "error": self.error,
        }


def _extract_li_job_id(url: str) -> Optional[str]:
    m = LI_JOB_ID_RE.search(url)
    if m:
        return m.group(1)
    # Some share links: /jobs/view/<id>?... or /jobs/.../<id>
    m = LI_FALLBACK_ID_RE.search(urlparse(url).path)
    if m:
        return m.group(1)
    return None


def _is_linkedin(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host.endswith("linkedin.com")


def _strip_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # Inline-replace structural tags so list items + breaks become clean lines.
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.replace_with(f"• {li.get_text(' ', strip=True)}\n")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _scrape_linkedin(url: str, li_cookie: Optional[str]) -> ScrapedJob:
    job_id = _extract_li_job_id(url)
    if not job_id:
        return ScrapedJob(url=url, error="Could not parse LinkedIn job ID from URL")

    # 1. Guest endpoint (no auth required, returns full HTML JD)
    guest_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True) as c:
            r = c.get(guest_url)
        if r.status_code == 200 and r.text.strip():
            soup = BeautifulSoup(r.text, "html.parser")
            company_el = (
                soup.select_one(".topcard__org-name-link")
                or soup.select_one(".topcard__flavor")
                or soup.select_one("a.sub-nav-cta__optional-url")
            )
            desc_el = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
            company = company_el.get_text(strip=True) if company_el else ""
            jd = _strip_html(str(desc_el)) if desc_el else ""
            if jd:
                return ScrapedJob(url=url, company=company, jd=jd, source="linkedin-guest")
    except Exception as e:
        # fall through to authed
        guest_err = str(e)
    else:
        guest_err = f"guest returned {r.status_code if 'r' in locals() else '?'}"

    # 2. Authed (li_at cookie required)
    if not li_cookie:
        return ScrapedJob(
            url=url,
            error=f"LinkedIn guest endpoint failed ({guest_err}); paste li_at cookie and retry.",
        )

    view_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            headers={"User-Agent": UA},
            cookies={"li_at": li_cookie},
            follow_redirects=True,
        ) as c:
            r = c.get(view_url)
        if r.status_code != 200:
            return ScrapedJob(url=url, error=f"LinkedIn authed fetch returned {r.status_code}")
        soup = BeautifulSoup(r.text, "html.parser")
        # JSON-LD JobPosting block
        for s in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(s.string or "")
            except Exception:
                continue
            blocks = data if isinstance(data, list) else [data]
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                if blk.get("@type") == "JobPosting":
                    company = (blk.get("hiringOrganization") or {}).get("name") or ""
                    jd_html = blk.get("description") or ""
                    jd = _strip_html(jd_html)
                    if jd:
                        return ScrapedJob(url=url, company=company, jd=jd, source="linkedin-auth")
        return ScrapedJob(url=url, error="LinkedIn authed page had no JobPosting JSON-LD")
    except Exception as e:
        return ScrapedJob(url=url, error=f"LinkedIn authed fetch failed: {e}")


def _company_from_url(url: str) -> str:
    host = urlparse(url).hostname or ""
    host = host.removeprefix("www.")
    # careers.stripe.com → stripe ; jobs.lever.co/foo → foo (handled below)
    parts = host.split(".")
    if len(parts) >= 2:
        # If a known ATS host, prefer first path segment.
        ats_hosts = {"lever.co", "greenhouse.io", "ashbyhq.com", "workable.com", "jobs.ashbyhq.com", "boards.greenhouse.io"}
        tail = ".".join(parts[-2:])
        if tail in ats_hosts:
            path = urlparse(url).path.strip("/").split("/")
            if path:
                return path[0].replace("-", " ").title()
        # Drop common subdomains
        if parts[0] in {"careers", "jobs", "boards", "apply", "hire", "work", "join"}:
            return parts[1].title()
        return parts[0].title()
    return host or "Company"


def _scrape_generic(url: str) -> ScrapedJob:
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code != 200:
            return ScrapedJob(url=url, error=f"HTTP {r.status_code}")
        html = r.text
    except Exception as e:
        return ScrapedJob(url=url, error=f"fetch failed: {e}")

    company = ""
    jd = ""

    # Try JSON-LD JobPosting first (Greenhouse, Lever, Ashby all expose this).
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        blocks = data if isinstance(data, list) else [data]
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            if blk.get("@type") == "JobPosting":
                company = (blk.get("hiringOrganization") or {}).get("name") or company
                jd_html = blk.get("description") or ""
                jd_text = _strip_html(jd_html)
                if jd_text and len(jd_text) > len(jd):
                    jd = jd_text

    # Fallback to trafilatura main-content extraction.
    if not jd:
        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if extracted:
            jd = extracted.strip()

    if not jd:
        return ScrapedJob(url=url, error="Could not extract job description from page")

    if not company:
        # og:site_name
        og = soup.find("meta", {"property": "og:site_name"})
        if og and og.get("content"):
            company = og["content"].strip()
    if not company:
        company = _company_from_url(url)

    return ScrapedJob(url=url, company=company, jd=jd, source="generic")


def scrape(url: str, li_cookie: Optional[str] = None) -> ScrapedJob:
    url = url.strip()
    if not url:
        return ScrapedJob(url=url, error="empty URL")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        if _is_linkedin(url):
            return _scrape_linkedin(url, li_cookie)
        return _scrape_generic(url)
    except Exception as e:
        return ScrapedJob(url=url, error=f"unexpected: {e}")
