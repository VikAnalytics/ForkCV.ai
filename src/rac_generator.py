"""Rewrite a resume bullet into Result-Action-Context order.

Most resume bullets are written Action-Context-Result (ACR): start with a
verb, describe what was done, end with the outcome. RAC inverts this —
lead with the outcome so a skimming recruiter sees the impact first.

Transformation is fact-preserving: the same tools, scale figures, and
named metrics must appear in both versions. No new tools, no inflated
numbers. If the source bullet has no quantifier, the RAC version stays
metric-light too — we don't invent one.
"""
from __future__ import annotations

from typing import List, Optional

from openai import OpenAI
from pydantic import BaseModel, Field

MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You rewrite resume bullets from Action-Context-Result order \
into Result-Action-Context order. Lead with the OUTCOME, then what was done, \
then the surrounding context.

HARD RULES:
- Preserve every concrete fact: tools/technologies named, scale figures, \
  percentages, dollar amounts, time savings. If the source says "Python", \
  the RAC version says "Python". No new tools, no inflated metrics.
- Same length budget as the source (±20%). Aim 140-220 chars.
- Same action verbs and domain language where possible — recruiter context \
  must remain identical.
- Lead with the quantifier when present: "Cut Snowflake spend 40% by …", \
  "Stabilized 150 production DAGs through …". If no quantifier exists, lead \
  with the most concrete outcome word.
- Do NOT prepend "Result:" / "Outcome:" / labels. Write a natural sentence.
- Do NOT invent context the source omits — if you don't know the team or \
  business unit, leave it out.

EXAMPLES:

Source: "Engineered an automated SQL migration pipeline using Gemini-assisted \
tools to convert 200+ Teradata queries to BigQuery while preserving business \
logic and achieving 100% accuracy."
RAC:    "Migrated 200+ Teradata queries to BigQuery at 100% accuracy by \
engineering a Gemini-assisted SQL migration pipeline that preserved business \
logic end-to-end."

Source: "Stabilized 150+ production DAGs in Airflow, improving system uptime \
33% through automated anomaly detection."
RAC:    "Lifted system uptime 33% by stabilizing 150+ production DAGs in \
Airflow with automated anomaly detection."

Source: "Built Looker dashboards for the FP&A team to monitor monthly close \
metrics."
RAC:    "Gave the FP&A team real-time visibility into monthly close metrics \
by building dedicated Looker dashboards."
"""


class RacRewrite(BaseModel):
    rac_text: str = Field(description="The same bullet rewritten in Result-Action-Context order.")


def rewrite_to_rac(source_text: str, *, client: Optional[OpenAI] = None) -> str:
    """Return the RAC-ordered phrasing for one bullet. Returns the original
    text on failure so callers don't need to defend."""
    if not source_text or not source_text.strip():
        return ""
    client = client or OpenAI()
    completion = client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Source bullet:\n{source_text.strip()}\n\nReturn RAC-ordered rewrite."},
        ],
        response_format=RacRewrite,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None or not parsed.rac_text:
        return source_text  # safe fallback
    return parsed.rac_text.strip()


def rewrite_bullets(texts: List[str], *, client: Optional[OpenAI] = None) -> List[str]:
    """Convenience: rewrite a list of source bullets to RAC in order.
    Failures fall back to the source text so the result list always has the
    same length and ordering as the input."""
    client = client or OpenAI()
    out: List[str] = []
    for t in texts:
        try:
            out.append(rewrite_to_rac(t, client=client))
        except Exception:
            out.append(t)
    return out
