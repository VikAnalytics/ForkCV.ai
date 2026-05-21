from __future__ import annotations

from typing import Optional

from openai import OpenAI

from .schemas import JDAnalysis, MasterCV, RewrittenSummary

MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You rewrite a candidate's professional-summary blurb to align with a target \
job description, WITHOUT fabricating experience.

Inputs:
1. The original `professional_summary` (truthful baseline).
2. Recent role titles + companies (proves what's truthful).
3. JD analysis: target role, primary tech stack, core impact areas, domain.

Rules:
1. Keep the candidate's actual seniority and primary identity intact. If they identify as a \
'Data Engineer' and the JD is for an 'Analytics Engineer', frame as 'Data/Analytics Engineer' \
or '...with analytics engineering focus' — don't relabel them as something they aren't.

2. Weave JD signals into the prose, but with TWO different rules for specific tools vs generic \
concepts:

   **(a) SPECIFIC TOOLS** (e.g. "dbt", "Databricks", "Snowflake", "QuickSight", "Airflow", \
   "BigQuery", "Spark"): name the tool in the summary ONLY if it appears in the EVIDENCE LIST \
   the user provides (the candidate's master CV skills + bullets). If a JD tool is absent from \
   the evidence list, DO NOT name it in the summary — period. Claiming "experienced with dbt" \
   when no bullet shows dbt is fabrication.

   **(b) GENERIC CONCEPTS** (e.g. "data modeling", "stakeholder reporting", "dashboarding", \
   "data marts", "insight generation", "trend analysis"): these can be woven freely when the \
   bullets show conceptually-equivalent work (e.g. "BigQuery indexing for downstream consumers" \
   demonstrably backs "data modeling"; "Looker dashboards" demonstrably backs "dashboarding").

   Aim for 2-3 generic concepts and 1-2 truthful specific tools (from evidence). Quality > quantity.

3. **NEVER mention or imply a specific industry, sector, or domain that is not present in the \
candidate's experience.** This is the single most important rule and the most common failure \
mode. Banned constructions include — but are not limited to:
   - "...within the banking sector"
   - "...for healthcare analytics"
   - "...in the insurance domain"
   - "...for fintech reporting"
   - "...supporting regulatory reporting in {industry}"
   - "...eager to apply to the {industry} sector"
   Do NOT name the JD's industry at all. The candidate's work has its own industry context \
   (proven by their bullets); injecting a new industry — even as an aspiration — looks \
   fabricated to a reader who scans the experience and sees no match. Let the TECH keywords \
   (SQL, Python, etc.) and IMPACT AREAS (data modeling, stakeholder reporting) carry the \
   JD-alignment signal without naming an industry.

4. Preserve the underlying claim (cloud-native pipelines, AI/ML, business impact, ROI focus, \
etc.) — recast its emphasis, don't replace its substance.

5. Length: 260-380 characters total. Two sentences, ideally three short ones. No bullets.

6. **Anti-AI-fluff:** the summary must read like a human professional wrote it, not an LLM. \
Avoid these tells, which signal generated text:
   - Vague impact verbs strung together: "leveraging", "facilitating", "ensuring", \
     "supporting", "driving", "enabling" — used only when followed by a concrete object.
   - Stacked abstract nouns: "data trust", "data integrity capabilities", "operational \
     excellence", "synergistic outcomes", "strategic alignment".
   - Closing aspirational filler: "eager to leverage my experience", "passionate about \
     delivering value", "looking forward to contributing".
   Replace these with concrete tools, deliverables, and outcomes. Two real claims beat \
   four hand-wavy ones."""


def rewrite_summary(
    master_cv: MasterCV,
    jd_analysis: JDAnalysis,
    *,
    client: Optional[OpenAI] = None,
) -> str:
    if not master_cv.professional_summary:
        return ""

    client = client or OpenAI()
    history_lines = "\n".join(
        f"- {e.role} @ {e.company} ({e.date or 'n/a'})" for e in master_cv.experience[:5]
    )
    # Build evidence list — every named tool/tech in skills + bullets.
    # The summary rewriter may name specific tools ONLY from this list.
    evidence_tools = set()
    for s in master_cv.skills:
        for item in s.items:
            evidence_tools.add(item.strip())
    bullets_text = "\n".join(
        f"- {b.text}" for e in master_cv.experience for b in e.bullet_pool
    ) + "\n" + "\n".join(
        f"- {b.text}" for p in master_cv.projects for b in p.bullet_pool
    )
    evidence_block = (
        "EVIDENCE — specific tools the candidate has truthfully used (from skills + bullets):\n"
        + ", ".join(sorted(evidence_tools))
        + "\n\nFull bullet text for context:\n"
        + bullets_text
    )

    user_msg = (
        f"ORIGINAL_SUMMARY:\n{master_cv.professional_summary}\n\n"
        f"RECENT_ROLES:\n{history_lines}\n\n"
        f"{evidence_block}\n\n"
        f"JD_ANALYSIS:\n{jd_analysis.model_dump_json(indent=2)}\n\n"
        "Return the rewritten summary. Reminder: name a SPECIFIC tool from JD only if it "
        "appears in the EVIDENCE list above. Otherwise use generic concepts."
    )

    completion = client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format=RewrittenSummary,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(
            f"Summary rewriter returned no structured output. refusal={completion.choices[0].message.refusal!r}"
        )
    return parsed.summary
