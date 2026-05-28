"""Apify sanity check. Usage: .venv/bin/python scripts/check_apify.py"""
import os

from dotenv import load_dotenv
import httpx

load_dotenv(".env.local")

tok = os.getenv("APIFY_API_TOKEN", "").strip()
actor = os.getenv("APIFY_ACTOR_ID", "").strip() or "bebity~linkedin-jobs-scraper"

print(f"Token len: {len(tok)}  prefix: {tok[:10] if tok else '(empty)'}")
print(f"Actor ID:  {actor}")

if not tok:
    raise SystemExit("APIFY_API_TOKEN is empty in .env.local")

r = httpx.get("https://api.apify.com/v2/users/me", params={"token": tok})
print(f"\n[whoami]   status={r.status_code}")
print(f"           body={r.text[:200]}")

r2 = httpx.get(f"https://api.apify.com/v2/acts/{actor}", params={"token": tok})
print(f"\n[actor info] status={r2.status_code}")
print(f"             body={r2.text[:400]}")

# Inspect pricing / rental requirement
import json
try:
    actor_data = r2.json().get("data", {})
    pricing = actor_data.get("pricingInfos") or actor_data.get("currentPricingInfo")
    print(f"\n[pricing]    {json.dumps(pricing, indent=2)[:500] if pricing else '(none surfaced)'}")
except Exception:
    pass

# Probe the run endpoint with a minimal body — same call our code makes
probe_body = {
    "urls": ["https://www.linkedin.com/jobs/search?keywords=Data%20Engineer&location=United%20States"],
    "count": 1,
}
r3 = httpx.post(
    f"https://api.apify.com/v2/acts/{actor}/runs",
    json=probe_body,
    params={"token": tok},
    timeout=30.0,
)
print(f"\n[start run]  status={r3.status_code}")
print(f"             body={r3.text[:500]}")

