from __future__ import annotations

import json
from typing import List, Optional

from openai import OpenAI

from .schemas import EnrichedSkills, JDAnalysis, MasterCV, SkillsSection

MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You enrich a candidate's Technical Skills section with JD-relevant additions, \
applying strict evidence-based rules to avoid fabrication.

You receive:
1. Current `skills` sections (categories + items, in candidate's stated order).
2. The full bullet pool from experience + projects (your evidence base).
3. `JDAnalysis` with `primary_tech_stack`, `must_have_keywords`, `core_impact_areas`, `domain`.

Rules:
1. PRESERVE: every existing category and item must appear in the output, in original order, \
verbatim. Do NOT remove, reorder, or rephrase existing items.
2. ADD a JD skill ONLY if one of these is true:
   (a) The candidate's bullet pool shows direct evidence of using it (e.g. JD wants 'SQL' and \
       bullets mention SQL pipelines/queries → safe add).
   (b) It is universally assumed for someone with the candidate's role title and recent work \
       (e.g. 'Git' for any modern software/data engineer is safe; 'Linux' for a backend dev).
   (c) It is a GENERIC CONCEPT (not a specific product) that the candidate's work demonstrably \
       performs under another name (e.g. JD wants 'data modeling' and bullets show schema \
       design/ETL work → safe to add 'Data Modeling' as a concept).
3. DO NOT ADD a specific product/tool the candidate has not demonstrably used. Examples of \
unsafe additions: 'dbt', 'Databricks', 'QuickSight', 'Snowflake' unless bullets mention them. \
When unsure, skip the specific tool — but you may add a related broader concept.
4. PLACEMENT: append each addition to the END of the single most topically appropriate existing \
category. Do NOT create new categories. Do NOT split categories.
5. LIMIT: at most 2-3 total additions across all categories. Quality > quantity.
6. If no addition meets the bar, return skills unchanged (this is correct behavior, not a failure)."""


def enrich_skills(
    master_cv: MasterCV,
    jd_analysis: JDAnalysis,
    *,
    client: Optional[OpenAI] = None,
) -> List[SkillsSection]:
    client = client or OpenAI()

    evidence_bullets = [b.text for e in master_cv.experience for b in e.bullet_pool] + [
        b.text for p in master_cv.projects for b in p.bullet_pool
    ]
    user_msg = (
        "CURRENT_SKILLS:\n"
        + json.dumps([s.model_dump() for s in master_cv.skills], indent=2)
        + "\n\nBULLET_POOL_EVIDENCE:\n"
        + "\n".join(f"- {b}" for b in evidence_bullets)
        + "\n\nJD_ANALYSIS:\n"
        + jd_analysis.model_dump_json(indent=2)
        + "\n\nReturn EnrichedSkills with conservative additions."
    )

    completion = client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format=EnrichedSkills,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(
            f"Skills enricher returned no structured output. refusal={completion.choices[0].message.refusal!r}"
        )
    return parsed.skills
