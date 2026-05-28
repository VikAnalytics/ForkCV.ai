"""Local-only server — no auth, no Supabase. Reads data/master_cv_bank.json,
serves the pre-auth web UI from web_local/. Use this for your personal
workflow while server.py remains the productionized multi-tenant version.

Run:
    .venv/bin/python -m uvicorn local_server:app --port 8001 --reload
"""
from __future__ import annotations

import asyncio
import base64
import io
import json as _json
import os
import re
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.jd_analyzer import analyze_jd
from src.jd_scraper import scrape as scrape_job
from src.latex_compiler import (
    compile_pdf, render_tex, _count_pdf_pages, TIGHTNESS_PRESETS,
)
from src.report import build_report
from src.schemas import (
    AppliedRecord,
    ContactCandidate,
    DiscoveredJob,
    DiscoveryRun,
    GenerationRecord,
    GenerationSummary,
    JDAnalysis,
    JobPreferences,
    MasterCV,
    OutreachContact,
    OutreachDraft,
    OutreachRecord,
    PipelineReport,
    ScoredContact,
)
from src.contact_provider import ApolloAuthError, reveal_email as apollo_reveal_email, search_contacts
from src.contact_scorer import rank_candidates
from src.outreach_generator import generate_outreach
from src.job_discovery import ApifyAuthError, discover_jobs_apify
from src.job_discovery_jobspy import discover_jobs_jobspy
from src.job_fit_scorer import apply_hard_filters, score_job
from src.years_extractor import extract_min_yoe
from src import sheets_sync


def _discover_backend():
    """Return the configured discovery backend. Defaults to jobspy (free)."""
    backend = os.getenv("DISCOVERY_BACKEND", "jobspy").strip().lower()
    if backend == "apify":
        return discover_jobs_apify
    return discover_jobs_jobspy
from src.selector_agent import apply_selection, select
from src.skills_enricher import enrich_skills
from src.sponsorship_extractor import extract_sponsorship
from src.language_extractor import extract_languages
load_dotenv(".env.local", override=True)
load_dotenv(".env")

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"
WEB_LOCAL_DIR = ROOT / "web_local"
MASTER_CV_PATH = DATA_DIR / "master_cv_bank.json"
GENERATIONS_PATH = DATA_DIR / "generations.json"
APPLIED_PATH = DATA_DIR / "applied_jobs.json"
OUTREACH_PATH = DATA_DIR / "outreach.json"
PREFERENCES_PATH = DATA_DIR / "preferences.json"
DISCOVERED_PATH = DATA_DIR / "discovered_jobs.json"
DISCOVERY_RUNS_PATH = DATA_DIR / "discovery_runs.json"

_store_lock = threading.Lock()


def _load_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = _json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_list(path: Path, items: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_json.dumps(items, indent=2, default=str))
    tmp.replace(path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

app = FastAPI(title="Master Resume Builder — local")

_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_stem(s: str) -> str:
    s = _SAFE_STEM.sub("_", s.strip())
    return s or "Company"


def _candidate_last_first(master: MasterCV) -> str:
    name = master.personal_info.name.strip()
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[-1]}"
    return name.replace(" ", "_") or "Candidate"


def _render_and_compile(cv: MasterCV, stem: str) -> bytes:
    """Render + compile the resume; auto-tighten typography until it fits on
    one page. Walks through TIGHTNESS_PRESETS in order; returns the first PDF
    that lands on a single page. If even the tightest preset overflows,
    returns the tightest attempt anyway (caller can decide what to do)."""
    with tempfile.TemporaryDirectory(prefix="rmach_local_") as td:
        td_path = Path(td)
        last_pdf_path: Optional[Path] = None
        for tightness in range(len(TIGHTNESS_PRESETS)):
            tex = render_tex(
                cv, templates_dir=TEMPLATES_DIR, template_name="resume.tex.j2",
                tightness=tightness,
            )
            tex_path = td_path / f"{stem}.tex"
            tex_path.write_text(tex)
            pdf_path = compile_pdf(tex_path, out_dir=td_path)
            last_pdf_path = pdf_path
            pages = _count_pdf_pages(pdf_path)
            if pages <= 1:
                return pdf_path.read_bytes()
        # Tightest still > 1 page — return whatever the last attempt produced.
        return last_pdf_path.read_bytes() if last_pdf_path else b""


class TailorRequest(BaseModel):
    company: str
    jd: str
    source_url: Optional[str] = None
    # Summary is permanently locked to the text in master_cv_bank.json — the
    # rewriter is never invoked in the local server even if the client asks.
    rewrite_summary: bool = False
    enrich_skills: bool = True
    # If True, the selector sees each bullet's `text_rac` as the source (with
    # `text` as fallback when RAC is empty). Default False = original phrasing.
    use_rac: bool = False


class TailorResponse(BaseModel):
    cv: MasterCV
    pdf_base64: str
    download_filename: str
    report: PipelineReport
    generation_id: str = ""


class RenderRequest(BaseModel):
    cv: MasterCV
    company: str


class RenderResponse(BaseModel):
    pdf_base64: str
    download_filename: str


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/master", response_model=MasterCV)
async def get_master():
    if not MASTER_CV_PATH.exists():
        raise HTTPException(404, f"master_cv_bank.json not found at {MASTER_CV_PATH}")
    return MasterCV.model_validate_json(MASTER_CV_PATH.read_text())


@app.post("/api/master", response_model=MasterCV)
async def save_master(cv: MasterCV):
    # Atomic write: tmp file in same dir, then rename.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MASTER_CV_PATH.with_suffix(".json.tmp")
    tmp.write_text(cv.model_dump_json(indent=2))
    tmp.replace(MASTER_CV_PATH)
    return cv


class RacGenerateRequest(BaseModel):
    only_missing: bool = True   # if False, regenerate even bullets that already have text_rac


@app.post("/api/master/generate-rac", response_model=MasterCV)
async def generate_master_rac(req: RacGenerateRequest):
    """Walk every bullet in master_cv_bank.json and populate text_rac via GPT.
    Skips bullets that already have text_rac unless `only_missing=false`."""
    if not MASTER_CV_PATH.exists():
        raise HTTPException(404, "master CV not found")
    master = MasterCV.model_validate_json(MASTER_CV_PATH.read_text())

    from src.rac_generator import rewrite_to_rac
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(4)

    async def one(text: str) -> str:
        async with sem:
            return await loop.run_in_executor(None, rewrite_to_rac, text)

    # Collect every bullet whose RAC is missing (or all, if forced).
    to_do: list[tuple[str, int, int, str]] = []   # (section, parent_idx, bullet_idx, source_text)
    for ei, e in enumerate(master.experience or []):
        for bi, b in enumerate(e.bullet_pool or []):
            if req.only_missing and b.text_rac:
                continue
            if b.text:
                to_do.append(("exp", ei, bi, b.text))
    for pi, p in enumerate(master.projects or []):
        for bi, b in enumerate(p.bullet_pool or []):
            if req.only_missing and b.text_rac:
                continue
            if b.text:
                to_do.append(("proj", pi, bi, b.text))

    if not to_do:
        return master  # nothing to do

    results = await asyncio.gather(*(one(item[3]) for item in to_do))
    for (section, p_idx, b_idx, _), rac in zip(to_do, results):
        if section == "exp":
            master.experience[p_idx].bullet_pool[b_idx].text_rac = rac
        else:
            master.projects[p_idx].bullet_pool[b_idx].text_rac = rac

    # Atomic write.
    tmp = MASTER_CV_PATH.with_suffix(".json.tmp")
    tmp.write_text(master.model_dump_json(indent=2))
    tmp.replace(MASTER_CV_PATH)
    return master


def _master_with_rac_swapped(master: MasterCV) -> MasterCV:
    """Return a copy of `master` where each Bullet.text is replaced by its
    text_rac (when non-empty). Used when the caller asks for RAC-sourced
    selection. `text_rac` is preserved alongside so apply_selection's index
    lookup stays valid."""
    cv = master.model_copy(deep=True)
    for e in (cv.experience or []):
        for b in (e.bullet_pool or []):
            if b.text_rac:
                b.text = b.text_rac
    for p in (cv.projects or []):
        for b in (p.bullet_pool or []):
            if b.text_rac:
                b.text = b.text_rac
    return cv


async def _run_tailor_pipeline(
    *, company: str, jd: str, source_url: Optional[str] = None,
    enrich_skills_flag: bool = True, use_rac: bool = False,
) -> tuple[GenerationRecord, bytes]:
    """Shared tailor pipeline. Used by /api/tailor and by discovery's auto-gen.
    Returns (saved GenerationRecord, PDF bytes)."""
    if not MASTER_CV_PATH.exists():
        raise HTTPException(500, "Master CV bank not found; run `ingest` first.")
    master = MasterCV.model_validate_json(MASTER_CV_PATH.read_text())
    source_cv = _master_with_rac_swapped(master) if use_rac else master

    loop = asyncio.get_running_loop()
    analysis = await loop.run_in_executor(None, analyze_jd, jd)
    selection = await loop.run_in_executor(None, select, source_cv, analysis)
    optimized = apply_selection(source_cv, selection)

    if enrich_skills_flag and optimized.skills:
        enriched = await loop.run_in_executor(None, enrich_skills, master, analysis)
        optimized = optimized.model_copy(update={"skills": enriched})

    stem = f"{_candidate_last_first(master)}_{_safe_stem(company)}_Resume"
    pdf_bytes = await loop.run_in_executor(None, _render_and_compile, optimized, stem)
    report = build_report(analysis, master, optimized)
    report.sponsorship = extract_sponsorship(jd)
    report.languages = extract_languages(jd)

    record = GenerationRecord(
        id=uuid.uuid4().hex,
        company=company,
        jd=jd,
        source_url=source_url,
        cv=optimized,
        report=report,
        pdf_filename=f"{stem}.pdf",
        created_at=_now_iso(),
    )
    with _store_lock:
        items = _load_list(GENERATIONS_PATH)
        items.insert(0, record.model_dump())
        _save_list(GENERATIONS_PATH, items)
    return record, pdf_bytes


@app.post("/api/tailor", response_model=TailorResponse)
async def tailor(req: TailorRequest):
    record, pdf_bytes = await _run_tailor_pipeline(
        company=req.company, jd=req.jd,
        source_url=req.source_url, enrich_skills_flag=req.enrich_skills,
        use_rac=req.use_rac,
    )
    return TailorResponse(
        cv=record.cv,
        pdf_base64=base64.b64encode(pdf_bytes).decode("ascii"),
        download_filename=record.pdf_filename,
        report=record.report,
        generation_id=record.id,
    )


@app.post("/api/render", response_model=RenderResponse)
async def render(req: RenderRequest):
    master = (
        MasterCV.model_validate_json(MASTER_CV_PATH.read_text())
        if MASTER_CV_PATH.exists() else None
    )
    candidate = _candidate_last_first(master) if master else _candidate_last_first(req.cv)
    stem = f"{candidate}_{_safe_stem(req.company)}_Resume"
    loop = asyncio.get_running_loop()
    pdf_bytes = await loop.run_in_executor(None, _render_and_compile, req.cv, stem)
    return RenderResponse(
        pdf_base64=base64.b64encode(pdf_bytes).decode("ascii"),
        download_filename=f"{stem}.pdf",
    )


class BulkParseRequest(BaseModel):
    urls: List[str]
    li_cookie: Optional[str] = None


class BulkParseItem(BaseModel):
    url: str
    company: str = ""
    jd: str = ""
    source: str = ""
    error: Optional[str] = None


class BulkParseResponse(BaseModel):
    items: List[BulkParseItem]


@app.post("/api/bulk/parse", response_model=BulkParseResponse)
async def bulk_parse(req: BulkParseRequest):
    urls = [u.strip() for u in req.urls if u.strip()]
    if not urls:
        raise HTTPException(400, "No URLs provided.")
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(5)

    async def one(u: str):
        async with sem:
            return await loop.run_in_executor(None, scrape_job, u, req.li_cookie)

    scraped = await asyncio.gather(*(one(u) for u in urls))
    return BulkParseResponse(items=[BulkParseItem(**s.to_dict()) for s in scraped])


class BulkZipItem(BaseModel):
    cv: MasterCV
    company: str


class BulkZipRequest(BaseModel):
    items: List[BulkZipItem]


@app.post("/api/bulk/zip")
async def bulk_zip(req: BulkZipRequest):
    if not req.items:
        raise HTTPException(400, "No items provided.")
    master = (
        MasterCV.model_validate_json(MASTER_CV_PATH.read_text())
        if MASTER_CV_PATH.exists() else None
    )
    loop = asyncio.get_running_loop()

    def _render_one(item: BulkZipItem) -> tuple[str, bytes]:
        candidate = _candidate_last_first(master) if master else _candidate_last_first(item.cv)
        stem = f"{candidate}_{_safe_stem(item.company)}_Resume"
        pdf_bytes = _render_and_compile(item.cv, stem)
        return f"{stem}.pdf", pdf_bytes

    sem = asyncio.Semaphore(3)  # pdflatex is CPU-heavy

    async def run(item):
        async with sem:
            return await loop.run_in_executor(None, _render_one, item)

    results = await asyncio.gather(*(run(it) for it in req.items), return_exceptions=True)

    buf = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in results:
            if isinstance(r, Exception):
                continue
            name, data = r
            # de-dup names in case two companies collide
            base = name
            i = 2
            while name in used_names:
                name = base.replace(".pdf", f"_{i}.pdf")
                i += 1
            used_names.add(name)
            zf.writestr(name, data)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="Resumes.zip"'},
    )


# ─── Generation history ──────────────────────────────────────────────────
@app.get("/api/generations", response_model=List[GenerationSummary])
async def list_generations():
    with _store_lock:
        items = _load_list(GENERATIONS_PATH)
        applied = _load_list(APPLIED_PATH)
    applied_gen_ids = {a.get("generation_id") for a in applied if a.get("generation_id")}
    out: List[GenerationSummary] = []
    for it in items:
        out.append(GenerationSummary(
            id=it.get("id", ""),
            company=it.get("company", ""),
            source_url=it.get("source_url"),
            pdf_filename=it.get("pdf_filename", ""),
            created_at=it.get("created_at", ""),
            applied=it.get("id") in applied_gen_ids,
        ))
    return out


@app.get("/api/generations/{gen_id}", response_model=GenerationRecord)
async def get_generation(gen_id: str):
    with _store_lock:
        items = _load_list(GENERATIONS_PATH)
    for it in items:
        if it.get("id") == gen_id:
            return GenerationRecord.model_validate(it)
    raise HTTPException(404, "generation not found")


@app.delete("/api/generations/{gen_id}")
async def delete_generation(gen_id: str):
    with _store_lock:
        items = _load_list(GENERATIONS_PATH)
        new = [i for i in items if i.get("id") != gen_id]
        if len(new) == len(items):
            raise HTTPException(404, "generation not found")
        _save_list(GENERATIONS_PATH, new)
    return {"ok": True}


# ─── Applied jobs ────────────────────────────────────────────────────────
class AppliedCreate(BaseModel):
    company: str
    job_title: str = ""
    job_link: str = ""
    applied_at: Optional[str] = None  # default to today
    status: str = "applied"
    notes: str = ""
    generation_id: Optional[str] = None


class AppliedPatch(BaseModel):
    company: Optional[str] = None
    job_title: Optional[str] = None
    job_link: Optional[str] = None
    applied_at: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


@app.get("/api/applied", response_model=List[AppliedRecord])
async def list_applied():
    with _store_lock:
        items = _load_list(APPLIED_PATH)
    return [AppliedRecord.model_validate(i) for i in items]


@app.post("/api/applied", response_model=AppliedRecord)
async def create_applied(req: AppliedCreate):
    rec = AppliedRecord(
        id=uuid.uuid4().hex,
        company=req.company,
        job_title=req.job_title,
        job_link=req.job_link,
        applied_at=req.applied_at or datetime.now(timezone.utc).date().isoformat(),
        status=req.status or "applied",
        notes=req.notes,
        generation_id=req.generation_id,
    )
    with _store_lock:
        items = _load_list(APPLIED_PATH)
        items.insert(0, rec.model_dump())
        _save_list(APPLIED_PATH, items)
    return rec


@app.patch("/api/applied/{app_id}", response_model=AppliedRecord)
async def update_applied(app_id: str, patch: AppliedPatch):
    with _store_lock:
        items = _load_list(APPLIED_PATH)
        for i, it in enumerate(items):
            if it.get("id") == app_id:
                for k, v in patch.model_dump(exclude_none=True).items():
                    it[k] = v
                items[i] = it
                _save_list(APPLIED_PATH, items)
                return AppliedRecord.model_validate(it)
    raise HTTPException(404, "applied record not found")


@app.delete("/api/applied/{app_id}")
async def delete_applied(app_id: str):
    with _store_lock:
        items = _load_list(APPLIED_PATH)
        new = [i for i in items if i.get("id") != app_id]
        if len(new) == len(items):
            raise HTTPException(404, "applied record not found")
        _save_list(APPLIED_PATH, new)
    return {"ok": True}


# ─── Outreach ────────────────────────────────────────────────────────────
class OutreachDiscoverRequest(BaseModel):
    generation_id: str
    top_k: int = 10
    refresh: bool = False  # if True, ignore cache and re-query Apollo


@app.get("/api/outreach/{gen_id}", response_model=Optional[OutreachRecord])
async def get_outreach(gen_id: str):
    with _store_lock:
        items = _load_list(OUTREACH_PATH)
    for it in items:
        if it.get("generation_id") == gen_id:
            return OutreachRecord.model_validate(it)
    return None  # 200 with empty body; client treats as "not yet generated"


@app.post("/api/outreach/discover", response_model=OutreachRecord)
async def discover_outreach(req: OutreachDiscoverRequest):
    # 1. Load the parent generation (for company, jd, cv).
    with _store_lock:
        gens = _load_list(GENERATIONS_PATH)
    gen = next((g for g in gens if g.get("id") == req.generation_id), None)
    if gen is None:
        raise HTTPException(404, "generation not found")

    # 2. Cached?
    if not req.refresh:
        with _store_lock:
            existing = _load_list(OUTREACH_PATH)
        cached = next((o for o in existing if o.get("generation_id") == req.generation_id), None)
        if cached:
            return OutreachRecord.model_validate(cached)

    # 3. Load master + JD analysis from the cached generation.
    master = MasterCV.model_validate(gen["cv"])
    analysis_dict = (gen.get("report") or {}).get("analysis") or {}
    try:
        jd_analysis = JDAnalysis.model_validate(analysis_dict) if analysis_dict else None
    except Exception:
        jd_analysis = None
    company = gen.get("company") or ""
    role_title = (jd_analysis.role_title if jd_analysis else "") or ""

    # 4. Apollo search.
    try:
        candidates = await asyncio.get_running_loop().run_in_executor(
            None,
            search_contacts,
            company,
            role_title,
            (jd_analysis.must_have_keywords[:6] if jd_analysis else []),
        )
    except ApolloAuthError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(502, f"Contact search failed: {e}")

    if not candidates:
        # Persist empty result so the UI shows "no contacts" without re-querying.
        record = OutreachRecord(
            generation_id=req.generation_id,
            company=company,
            role_title=role_title,
            created_at=_now_iso(),
            contacts=[],
        )
        _upsert_outreach(record)
        return record

    # 5. Score + rank.
    scored = rank_candidates(candidates, master, role_title, jd_analysis, top_k=req.top_k)

    # 6. Generate outreach drafts (parallel up to 4 at a time).
    #    One failing draft must not kill the batch — fall back to a stub draft
    #    so the contact still surfaces with name + email + LinkedIn URL.
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(4)

    async def gen_one(sc: ScoredContact) -> OutreachContact:
        async with sem:
            try:
                draft = await loop.run_in_executor(
                    None,
                    lambda: generate_outreach(sc, master, company, role_title, jd=jd_analysis),
                )
            except Exception as e:
                first = (sc.contact.name or "there").split()[0] if sc.contact.name else "there"
                draft = OutreachDraft(
                    linkedin_note=f"{first}, applying for {role_title} at {company}. Open to a quick chat?",
                    email_subject=f"{role_title} at {company}",
                    email_body=(
                        f"Hi {first},\n\nJust applied for the {role_title} role at {company}. "
                        f"Happy to share a tailored resume and answer any questions.\n\n"
                        f"(Draft generation failed for this contact: {e}. Edit me before sending.)\n\n"
                        f"{(master.personal_info.name or '').split()[0]}"
                    ),
                )
        return OutreachContact(scored=sc, draft=draft)

    contacts = await asyncio.gather(*(gen_one(s) for s in scored))

    # 7. Persist.
    record = OutreachRecord(
        generation_id=req.generation_id,
        company=company,
        role_title=role_title,
        created_at=_now_iso(),
        contacts=list(contacts),
    )
    _upsert_outreach(record)
    return record


def _upsert_outreach(record: OutreachRecord) -> None:
    with _store_lock:
        items = _load_list(OUTREACH_PATH)
        items = [i for i in items if i.get("generation_id") != record.generation_id]
        items.insert(0, record.model_dump())
        _save_list(OUTREACH_PATH, items)


class OutreachRevealRequest(BaseModel):
    generation_id: str
    contact_index: int


@app.post("/api/outreach/reveal", response_model=OutreachContact)
async def reveal_outreach_email(req: OutreachRevealRequest):
    """Burn 1 Apollo credit to reveal a single contact's email. Updates the
    cached outreach record in place."""
    with _store_lock:
        items = _load_list(OUTREACH_PATH)
    record = next((i for i in items if i.get("generation_id") == req.generation_id), None)
    if record is None:
        raise HTTPException(404, "outreach record not found")
    contacts = record.get("contacts") or []
    if req.contact_index < 0 or req.contact_index >= len(contacts):
        raise HTTPException(400, "contact_index out of range")
    target = contacts[req.contact_index]
    contact = (target.get("scored") or {}).get("contact") or {}

    name = contact.get("name") or ""
    parts = name.strip().split()
    first = parts[0] if parts else None
    last = parts[-1] if len(parts) > 1 else None

    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: apollo_reveal_email(
                apollo_id=contact.get("apollo_id"),
                linkedin_url=contact.get("linkedin_url"),
                first_name=first,
                last_name=last,
                organization_name=contact.get("organization_name"),
            ),
        )
    except ApolloAuthError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(502, f"Email find failed: {e}")

    revealed_email = result.get("email")
    revealed_status = result.get("email_status")
    if not revealed_email:
        # Provider returned no email — surface as a soft error.
        raise HTTPException(404, "Could not find a verified email for this contact.")

    # Patch the cached record + persist.
    contact["email"] = revealed_email
    contact["email_status"] = revealed_status or "verified"
    target["scored"]["contact"] = contact
    contacts[req.contact_index] = target
    record["contacts"] = contacts
    with _store_lock:
        items = [i for i in items if i.get("generation_id") != req.generation_id]
        items.insert(0, record)
        _save_list(OUTREACH_PATH, items)

    return OutreachContact.model_validate(target)


@app.delete("/api/outreach/{gen_id}")
async def delete_outreach(gen_id: str):
    with _store_lock:
        items = _load_list(OUTREACH_PATH)
        new = [i for i in items if i.get("generation_id") != gen_id]
        _save_list(OUTREACH_PATH, new)
    return {"ok": True}


# ─── Preferences ─────────────────────────────────────────────────────────
def _load_prefs() -> JobPreferences:
    if not PREFERENCES_PATH.exists():
        return JobPreferences()
    try:
        return JobPreferences.model_validate_json(PREFERENCES_PATH.read_text())
    except Exception:
        return JobPreferences()


def _save_prefs(prefs: JobPreferences) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PREFERENCES_PATH.with_suffix(".json.tmp")
    tmp.write_text(prefs.model_dump_json(indent=2))
    tmp.replace(PREFERENCES_PATH)


@app.get("/api/preferences", response_model=JobPreferences)
async def get_preferences():
    return _load_prefs()


@app.post("/api/preferences", response_model=JobPreferences)
async def save_preferences(prefs: JobPreferences):
    _save_prefs(prefs)
    return prefs


# ─── Job discovery ───────────────────────────────────────────────────────
# In-memory tracker for the latest in-flight or just-finished run.
_current_run: dict = {"id": None, "task": None, "record": None}


@app.get("/api/discovery/jobs", response_model=List[DiscoveredJob])
async def list_discovered_jobs():
    with _store_lock:
        raw = _load_list(DISCOVERED_PATH)
    out: List[DiscoveredJob] = []
    for it in raw:
        try:
            out.append(DiscoveredJob.model_validate(it))
        except Exception:
            continue
    # Newest first.
    out.sort(key=lambda j: j.discovered_at or "", reverse=True)
    return out


class DiscoveryJobPatch(BaseModel):
    applied: Optional[bool] = None
    rejected: Optional[bool] = None
    rejection_reason: Optional[str] = None
    applied_id: Optional[str] = None
    generation_id: Optional[str] = None


@app.patch("/api/discovery/jobs/{job_id}", response_model=DiscoveredJob)
async def patch_discovered_job(job_id: str, patch: DiscoveryJobPatch):
    with _store_lock:
        raw = _load_list(DISCOVERED_PATH)
        for i, it in enumerate(raw):
            if it.get("id") == job_id:
                for k, v in patch.model_dump(exclude_none=True).items():
                    it[k] = v
                raw[i] = it
                _save_list(DISCOVERED_PATH, raw)
                return DiscoveredJob.model_validate(it)
    raise HTTPException(404, "discovered job not found")


@app.delete("/api/discovery/jobs/{job_id}")
async def delete_discovered_job(job_id: str):
    with _store_lock:
        raw = _load_list(DISCOVERED_PATH)
        new = [i for i in raw if i.get("id") != job_id]
        if len(new) == len(raw):
            raise HTTPException(404, "discovered job not found")
        _save_list(DISCOVERED_PATH, new)
    return {"ok": True}


@app.get("/api/discovery/status", response_model=Optional[DiscoveryRun])
async def discovery_status():
    """Return the most recent discovery run (in-flight or finished)."""
    rec = _current_run.get("record")
    if rec is not None:
        return rec
    with _store_lock:
        runs = _load_list(DISCOVERY_RUNS_PATH)
    if runs:
        try:
            return DiscoveryRun.model_validate(runs[0])
        except Exception:
            return None
    return None


@app.post("/api/discovery/run", response_model=DiscoveryRun)
async def start_discovery_run(max_results: int = 100):
    """Kick off a discovery run in the background. Returns the initial run
    record so the client can poll /api/discovery/status."""
    if _current_run.get("task") and not _current_run["task"].done():
        raise HTTPException(409, "A discovery run is already in progress.")

    prefs = _load_prefs()
    if not prefs.roles:
        raise HTTPException(400, "No roles configured in preferences. Set roles first.")

    run = DiscoveryRun(
        id=uuid.uuid4().hex,
        started_at=_now_iso(),
        source="apify-linkedin",
    )
    _current_run["id"] = run.id
    _current_run["record"] = run

    async def _runner():
        try:
            updated = await _execute_discovery(run, prefs, max_results)
            _current_run["record"] = updated
            # Persist to runs history (cap last 20).
            with _store_lock:
                items = _load_list(DISCOVERY_RUNS_PATH)
                items.insert(0, updated.model_dump())
                _save_list(DISCOVERY_RUNS_PATH, items[:20])
        except Exception as e:
            run.finished_at = _now_iso()
            run.error = str(e)
            _current_run["record"] = run
            with _store_lock:
                items = _load_list(DISCOVERY_RUNS_PATH)
                items.insert(0, run.model_dump())
                _save_list(DISCOVERY_RUNS_PATH, items[:20])

    _current_run["task"] = asyncio.create_task(_runner())
    return run


async def _execute_discovery(
    run: DiscoveryRun, prefs: JobPreferences, max_results: int
) -> DiscoveryRun:
    """Run the actual pipeline: Apify → extractors → scorer → dedup → persist → auto-tailor."""
    loop = asyncio.get_running_loop()

    # 1) Run the configured backend (jobspy by default; apify if DISCOVERY_BACKEND=apify).
    backend = _discover_backend()
    run.source = os.getenv("DISCOVERY_BACKEND", "jobspy").lower()
    try:
        jobs = await loop.run_in_executor(None, lambda: backend(prefs, max_results=max_results))
    except ApifyAuthError as e:
        raise HTTPException(401, str(e))
    run.raw_count = len(jobs)

    # 2) Master CV for scoring + autogen.
    master = MasterCV.model_validate_json(MASTER_CV_PATH.read_text()) if MASTER_CV_PATH.exists() else None
    if master is None:
        run.error = "Master CV missing; cannot score discovered jobs."
        run.finished_at = _now_iso()
        return run

    # 3) Existing dedup keys.
    with _store_lock:
        existing = _load_list(DISCOVERED_PATH)
    existing_by_key = {it.get("dedup_key"): it for it in existing if it.get("dedup_key")}

    new_jobs: List[DiscoveredJob] = []
    dup_skipped = 0

    for j in jobs:
        if not j.dedup_key:
            continue
        if j.dedup_key in existing_by_key:
            # Bump last_seen_at but keep all user-state.
            existing_by_key[j.dedup_key]["last_seen_at"] = _now_iso()
            dup_skipped += 1
            continue
        # Annotate signals.
        j.sponsorship_status = extract_sponsorship(j.description).status if j.description else "unspecified"
        langs = extract_languages(j.description) if j.description else []
        j.languages_required = [c.language for c in langs if c.required]
        j.yoe_required = extract_min_yoe(j.description) if j.description else None
        # Hard filters.
        passes, reason = apply_hard_filters(j, prefs)
        if not passes:
            j.rejected = True
            j.rejection_reason = reason
            j.score = 0
        else:
            score, breakdown = score_job(j, prefs, master)
            j.score = score
            j.score_breakdown = breakdown
        new_jobs.append(j)

    run.added = len(new_jobs)
    run.dup_skipped = dup_skipped
    run.rejected = sum(1 for j in new_jobs if j.rejected)

    # 4) Persist new + bumped-seen entries.
    all_items = list(existing_by_key.values()) + [j.model_dump() for j in new_jobs]
    with _store_lock:
        _save_list(DISCOVERED_PATH, all_items)

    # 5) Auto-tailor top N qualifying jobs.
    qualifying = sorted(
        [j for j in new_jobs if not j.rejected and j.score >= prefs.autogen_min_score and j.description],
        key=lambda j: j.score, reverse=True,
    )[: prefs.autogen_top_n]

    sem = asyncio.Semaphore(2)

    async def _autogen(job: DiscoveredJob):
        async with sem:
            try:
                rec, _ = await _run_tailor_pipeline(
                    company=job.company or "Company",
                    jd=job.description,
                    source_url=job.application_link or None,
                )
                # Patch the discovered job's generation_id.
                with _store_lock:
                    raw = _load_list(DISCOVERED_PATH)
                    for i, it in enumerate(raw):
                        if it.get("id") == job.id:
                            it["generation_id"] = rec.id
                            raw[i] = it
                            _save_list(DISCOVERED_PATH, raw)
                            break
                return True
            except Exception:
                return False

    results = await asyncio.gather(*(_autogen(j) for j in qualifying))
    run.autogen_count = sum(1 for r in results if r)

    # 6) Auto-sync to Google Sheets if configured.
    if sheets_sync.is_configured():
        try:
            with _store_lock:
                raw_all = _load_list(DISCOVERED_PATH)
            all_jobs = [DiscoveredJob.model_validate(it) for it in raw_all]
            await loop.run_in_executor(None, sheets_sync.sync_jobs, all_jobs)
        except Exception as e:
            # Sheets sync failure shouldn't fail the whole run; record on run.error.
            run.error = (run.error + " | " if run.error else "") + f"Sheets sync failed: {e}"

    run.finished_at = _now_iso()
    return run


# ─── Google Sheets sync ──────────────────────────────────────────────────
@app.get("/api/sheets/status")
async def sheets_status():
    return sheets_sync.status_dict()


@app.post("/api/sheets/sync")
async def sheets_sync_now():
    if not sheets_sync.is_configured():
        raise HTTPException(400, "Sheets not configured. See README → Discovery → Sheets sync.")
    with _store_lock:
        raw = _load_list(DISCOVERED_PATH)
    try:
        jobs = [DiscoveredJob.model_validate(it) for it in raw]
    except Exception:
        jobs = []
    try:
        result = await asyncio.get_running_loop().run_in_executor(None, sheets_sync.sync_jobs, jobs)
    except Exception as e:
        raise HTTPException(502, f"Sheets sync failed: {e}")
    return result


if WEB_LOCAL_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_LOCAL_DIR), html=True), name="web_local")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("local_server:app", host="127.0.0.1", port=8001, reload=False)
