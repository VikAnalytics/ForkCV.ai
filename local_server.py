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
from src.latex_compiler import compile_pdf, render_tex
from src.report import build_report
from src.schemas import (
    AppliedRecord,
    GenerationRecord,
    GenerationSummary,
    MasterCV,
    PipelineReport,
)
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
    with tempfile.TemporaryDirectory(prefix="rmach_local_") as td:
        td_path = Path(td)
        tex = render_tex(cv, templates_dir=TEMPLATES_DIR, template_name="resume.tex.j2")
        tex_path = td_path / f"{stem}.tex"
        tex_path.write_text(tex)
        pdf_path = compile_pdf(tex_path, out_dir=td_path)
        return pdf_path.read_bytes()


class TailorRequest(BaseModel):
    company: str
    jd: str
    source_url: Optional[str] = None
    # Summary is permanently locked to the text in master_cv_bank.json — the
    # rewriter is never invoked in the local server even if the client asks.
    rewrite_summary: bool = False
    enrich_skills: bool = True


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


@app.post("/api/tailor", response_model=TailorResponse)
async def tailor(req: TailorRequest):
    if not MASTER_CV_PATH.exists():
        raise HTTPException(500, "Master CV bank not found; run `ingest` first.")
    master = MasterCV.model_validate_json(MASTER_CV_PATH.read_text())

    loop = asyncio.get_running_loop()
    analysis = await loop.run_in_executor(None, analyze_jd, req.jd)
    selection = await loop.run_in_executor(None, select, master, analysis)
    optimized = apply_selection(master, selection)

    # Summary is permanently locked — pass through master's `professional_summary`
    # verbatim regardless of `req.rewrite_summary`. To re-enable rewriting later,
    # restore the rewriter call here and flip the default in TailorRequest.

    if req.enrich_skills and optimized.skills:
        enriched = await loop.run_in_executor(None, enrich_skills, master, analysis)
        optimized = optimized.model_copy(update={"skills": enriched})

    stem = f"{_candidate_last_first(master)}_{_safe_stem(req.company)}_Resume"
    pdf_bytes = await loop.run_in_executor(None, _render_and_compile, optimized, stem)
    report = build_report(analysis, master, optimized)
    report.sponsorship = extract_sponsorship(req.jd)
    report.languages = extract_languages(req.jd)

    download_filename = f"{stem}.pdf"
    record = GenerationRecord(
        id=uuid.uuid4().hex,
        company=req.company,
        jd=req.jd,
        source_url=req.source_url,
        cv=optimized,
        report=report,
        pdf_filename=download_filename,
        created_at=_now_iso(),
    )
    with _store_lock:
        items = _load_list(GENERATIONS_PATH)
        items.insert(0, record.model_dump())
        _save_list(GENERATIONS_PATH, items)

    return TailorResponse(
        cv=optimized,
        pdf_base64=base64.b64encode(pdf_bytes).decode("ascii"),
        download_filename=download_filename,
        report=report,
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


if WEB_LOCAL_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_LOCAL_DIR), html=True), name="web_local")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("local_server:app", host="127.0.0.1", port=8001, reload=False)
