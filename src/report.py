from __future__ import annotations

import re
from typing import Iterable, List, Tuple

from .schemas import (
    JDAnalysis,
    KeywordPlacement,
    MasterCV,
    PipelineReport,
    PlacementLocation,
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _keyword_in(text: str, kw: str) -> bool:
    """Case-insensitive, word-boundary-ish match. Substring fallback for short keywords."""
    t = text.lower()
    k = kw.lower().strip()
    if not k:
        return False
    # Try word-boundary match first.
    pattern = r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])"
    if re.search(pattern, t):
        return True
    # Compact alphanumeric fallback (handles e.g. "data marts" vs "datamart").
    return _norm(k) in _norm(text)


def _short_snippet(text: str, kw: str, width: int = 70) -> str:
    t = text
    idx = t.lower().find(kw.lower())
    if idx < 0:
        idx = 0
    start = max(0, idx - width // 2)
    end = min(len(t), idx + len(kw) + width // 2)
    out = t[start:end].strip()
    if start > 0:
        out = "…" + out
    if end < len(t):
        out = out + "…"
    return out


def _master_skills_items(master: MasterCV) -> set[str]:
    out: set[str] = set()
    for s in master.skills:
        for item in s.items:
            out.add(item.strip().lower())
    return out


def _diff_skill_additions(master: MasterCV, optimized: MasterCV) -> List[Tuple[str, str]]:
    """Return list of (category, added_item) tuples."""
    master_by_cat = {s.category.strip().lower(): {i.strip().lower() for i in s.items} for s in master.skills}
    additions: List[Tuple[str, str]] = []
    for s in optimized.skills:
        before = master_by_cat.get(s.category.strip().lower(), set())
        for item in s.items:
            if item.strip().lower() not in before:
                additions.append((s.category, item))
    return additions


def _scan_locations(
    optimized: MasterCV,
    master: MasterCV,
    kw: str,
    *,
    skill_additions: List[Tuple[str, str]],
) -> List[PlacementLocation]:
    locs: List[PlacementLocation] = []

    # Summary
    if optimized.professional_summary and _keyword_in(optimized.professional_summary, kw):
        is_new = not (
            master.professional_summary and _keyword_in(master.professional_summary, kw)
        )
        locs.append(
            PlacementLocation(
                section="summary",
                label="Professional Summary",
                is_new=is_new,
                snippet=_short_snippet(optimized.professional_summary, kw),
            )
        )

    # Skills — call out added items first, then existing matches.
    added_lookup = {(c.strip().lower(), i.strip().lower()) for c, i in skill_additions}
    for s in optimized.skills:
        for item in s.items:
            if _keyword_in(item, kw):
                is_new = (s.category.strip().lower(), item.strip().lower()) in added_lookup
                locs.append(
                    PlacementLocation(
                        section="skills",
                        label=f"Skills › {s.category}",
                        is_new=is_new,
                        snippet=item,
                    )
                )

    # Experience bullets
    master_text_by_role = {
        (e.company.strip().lower(), e.role.strip().lower()): " ".join(b.text for b in e.bullet_pool)
        for e in master.experience
    }
    for e in optimized.experience:
        for i, b in enumerate(e.bullet_pool, start=1):
            if _keyword_in(b.text, kw):
                src_text = master_text_by_role.get((e.company.strip().lower(), e.role.strip().lower()), "")
                is_new = not _keyword_in(src_text, kw)
                locs.append(
                    PlacementLocation(
                        section="experience",
                        label=f"{e.role} @ {e.company}, bullet {i}",
                        is_new=is_new,
                        snippet=_short_snippet(b.text, kw),
                    )
                )

    # Project bullets
    master_text_by_proj = {
        p.name.strip().lower(): " ".join(b.text for b in p.bullet_pool) for p in master.projects
    }
    for p in optimized.projects:
        for i, b in enumerate(p.bullet_pool, start=1):
            if _keyword_in(b.text, kw):
                src_text = master_text_by_proj.get(p.name.strip().lower(), "")
                is_new = not _keyword_in(src_text, kw)
                locs.append(
                    PlacementLocation(
                        section="project",
                        label=f"{p.name}, bullet {i}",
                        is_new=is_new,
                        snippet=_short_snippet(b.text, kw),
                    )
                )

    return locs


def build_report(
    analysis: JDAnalysis, master: MasterCV, optimized: MasterCV
) -> PipelineReport:
    skill_additions = _diff_skill_additions(master, optimized)

    placements: List[KeywordPlacement] = []
    buckets: List[Tuple[str, Iterable[str]]] = [
        ("primary_tech", analysis.primary_tech_stack),
        ("core_impact", analysis.core_impact_areas),
        ("must_have", analysis.must_have_keywords),
    ]
    if analysis.domain:
        buckets.append(("domain", [analysis.domain]))

    seen: set[Tuple[str, str]] = set()
    for bucket, keywords in buckets:
        for kw in keywords:
            key = (bucket, kw.strip().lower())
            if not kw.strip() or key in seen:
                continue
            seen.add(key)
            locs = _scan_locations(optimized, master, kw, skill_additions=skill_additions)
            placements.append(KeywordPlacement(keyword=kw, bucket=bucket, locations=locs))

    skill_addition_strs = [f"{c}: {i}" for c, i in skill_additions]
    return PipelineReport(
        analysis=analysis, placements=placements, skill_additions=skill_addition_strs
    )
