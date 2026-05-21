from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

import re

from src.jd_analyzer import analyze_jd
from src.latex_compiler import compile_pdf, render_tex
from src.pdf_ingestion import ingest_pdf
from src.schemas import MasterCV
from src.selector_agent import apply_selection, select
from src.skills_enricher import enrich_skills
from src.summary_rewriter import rewrite_summary

_METRIC_RE = re.compile(r"\b\d[\d,.]*\s*(%|x|k|m|b|hrs?|mins?|hours?|days?|weeks?|months?|years?|\+|seconds?|s)?\b", re.IGNORECASE)


def _has_metric(text: str) -> bool:
    return bool(_METRIC_RE.search(text))

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"
OUTPUTS_DIR = ROOT / "outputs"


def cmd_ingest(args: argparse.Namespace) -> int:
    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    out_path = Path(args.out).resolve() if args.out else DATA_DIR / "master_cv_bank.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Ingesting: {pdf_path}")
    master_cv = ingest_pdf(pdf_path)

    payload = master_cv.model_dump(exclude_none=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    n_roles = len(master_cv.experience)
    n_projects = len(master_cv.projects)
    n_bullets = sum(len(e.bullet_pool) for e in master_cv.experience) + sum(
        len(p.bullet_pool) for p in master_cv.projects
    )
    print(f"Wrote {out_path}")
    print(f"  experience: {n_roles} roles, projects: {n_projects}, total bullets: {n_bullets}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    cv_path = Path(args.cv).resolve()
    if not cv_path.exists():
        print(f"CV JSON not found: {cv_path}", file=sys.stderr)
        return 1

    cv = MasterCV.model_validate_json(cv_path.read_text())

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = args.name or "resume"
    tex_path = OUTPUTS_DIR / f"{stem}.tex"

    tex = render_tex(cv, templates_dir=TEMPLATES_DIR, template_name=args.template)
    tex_path.write_text(tex)
    print(f"Wrote {tex_path}")

    if args.tex_only:
        return 0

    pdf_path = compile_pdf(tex_path, out_dir=OUTPUTS_DIR)
    print(f"Wrote {pdf_path}")
    return 0


def cmd_tailor(args: argparse.Namespace) -> int:
    cv_path = Path(args.cv).resolve()
    jd_path = Path(args.jd).resolve()
    if not cv_path.exists():
        print(f"CV JSON not found: {cv_path}", file=sys.stderr)
        return 1
    if not jd_path.exists():
        print(f"JD file not found: {jd_path}", file=sys.stderr)
        return 1

    master_cv = MasterCV.model_validate_json(cv_path.read_text())
    jd_text = jd_path.read_text()

    print(f"Analyzing JD: {jd_path.name}")
    analysis = analyze_jd(jd_text)
    print(f"  role: {analysis.role_title}")
    print(f"  stack: {', '.join(analysis.primary_tech_stack[:8])}")
    print(f"  impact: {', '.join(analysis.core_impact_areas[:5])}")

    print("Selecting + compressing bullets...")
    selection = select(master_cv, analysis)
    optimized = apply_selection(master_cv, selection)

    if args.rewrite_summary and optimized.professional_summary:
        print("Rewriting professional summary...")
        new_summary = rewrite_summary(master_cv, analysis)
        optimized = optimized.model_copy(update={"professional_summary": new_summary})
        print(f"  summary ({len(new_summary)} chars): {new_summary[:120]}...")

    if args.enrich_skills and optimized.skills:
        print("Enriching skills with JD-relevant additions...")
        before = sum(len(s.items) for s in optimized.skills)
        enriched = enrich_skills(master_cv, analysis)
        after = sum(len(s.items) for s in enriched)
        optimized = optimized.model_copy(update={"skills": enriched})
        added = after - before
        print(f"  added {added} item(s) across {len(enriched)} categories")
        if added > 0:
            for s in enriched:
                print(f"  {s.category}: {', '.join(s.items)}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sel_path = DATA_DIR / "tailored_selection.json"
    opt_path = DATA_DIR / "optimized_1_page.json"
    sel_path.write_text(selection.model_dump_json(indent=2))
    opt_path.write_text(optimized.model_dump_json(indent=2, exclude_none=True))
    print(f"Wrote {sel_path}")
    print(f"Wrote {opt_path}")

    n_bullets = sum(len(e.bullet_pool) for e in optimized.experience) + sum(
        len(p.bullet_pool) for p in optimized.projects
    )
    all_bullets = [b for e in optimized.experience for b in e.bullet_pool] + [
        b for p in optimized.projects for b in p.bullet_pool
    ]
    too_short = sum(1 for b in all_bullets if len(b.text) < 140)
    too_long = sum(1 for b in all_bullets if len(b.text) > 220)
    no_metric = sum(1 for b in all_bullets if not _has_metric(b.text))
    print(
        f"  bullets: {n_bullets} | <140 chars: {too_short} | >220 chars: {too_long} | "
        f"no quantifier: {no_metric}"
    )

    if args.render:
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        stem = args.name or "resume_tailored"
        tex_path = OUTPUTS_DIR / f"{stem}.tex"
        tex_path.write_text(
            render_tex(optimized, templates_dir=TEMPLATES_DIR, template_name=args.template)
        )
        print(f"Wrote {tex_path}")
        pdf_path = compile_pdf(tex_path, out_dir=OUTPUTS_DIR)
        print(f"Wrote {pdf_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="resume-machine")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="PDF -> master_cv_bank.json")
    ingest.add_argument("pdf", help="Path to source resume PDF")
    ingest.add_argument("--out", help="Output JSON path (default: data/master_cv_bank.json)")
    ingest.set_defaults(func=cmd_ingest)

    render = sub.add_parser("render", help="MasterCV JSON -> .tex (+ PDF)")
    render.add_argument("cv", help="Path to MasterCV JSON")
    render.add_argument("--template", default="resume.tex.j2", help="Jinja2 template filename")
    render.add_argument("--name", default="resume", help="Output stem (default: resume)")
    render.add_argument("--tex-only", action="store_true", help="Skip PDF compile")
    render.set_defaults(func=cmd_render)

    tailor = sub.add_parser("tailor", help="JD + MasterCV -> optimized_1_page.json (+ optional PDF)")
    tailor.add_argument("cv", help="Path to MasterCV JSON")
    tailor.add_argument("jd", help="Path to JD .txt file")
    tailor.add_argument("--render", action="store_true", help="Also render+compile PDF")
    tailor.add_argument(
        "--rewrite-summary",
        action="store_true",
        help="Rewrite professional_summary to align with JD (no fabrication)",
    )
    tailor.add_argument(
        "--enrich-skills",
        action="store_true",
        help="Append JD-relevant skills to existing categories where bullets show evidence",
    )
    tailor.add_argument("--template", default="resume.tex.j2", help="Jinja2 template filename")
    tailor.add_argument("--name", default="resume_tailored", help="PDF stem (default: resume_tailored)")
    tailor.set_defaults(func=cmd_tailor)

    return parser


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
