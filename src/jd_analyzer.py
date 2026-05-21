from __future__ import annotations

from typing import Optional

from openai import OpenAI

from .schemas import JDAnalysis

MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You are a recruiting strategist. Extract structured signals from a job description \
to drive downstream resume-tailoring. Be precise:

- `primary_tech_stack`: concrete, named tools/languages/platforms. Lowercase canonical names \
('dbt' not 'DBT', 'sql' lowercase). Skip vague terms like 'cloud' or 'databases'.
- `core_impact_areas`: 3-6 short noun phrases describing the outcomes the role drives.
- `must_have_keywords`: 5-12 keywords (tech + soft) most likely to be scored by an ATS or human \
screener. Should match phrasing in the JD where reasonable.
- `domain`: industry context if clearly stated (e.g. 'Insurance', 'Fintech', 'Healthcare'); null otherwise.

Do not invent signals not present in the JD."""


def analyze_jd(jd_text: str, *, client: Optional[OpenAI] = None) -> JDAnalysis:
    client = client or OpenAI()
    completion = client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"JOB DESCRIPTION:\n\n{jd_text}"},
        ],
        response_format=JDAnalysis,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(
            f"JD analyzer returned no structured output. refusal={completion.choices[0].message.refusal!r}"
        )
    return parsed
