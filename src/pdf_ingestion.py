from __future__ import annotations

from pathlib import Path
from typing import Optional

import fitz
from openai import OpenAI

from .schemas import MasterCV

MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You convert raw resume text into a structured Master CV JSON \
'feature store' for downstream tailored-resume generation.

Rules:
1. Extract every distinct role, project, education entry, and skill found in the source.
2. For each role and project, populate `bullet_pool` with the bullets present in the source. \
Do NOT fabricate accomplishments. If the source provides 3 bullets, return 3. If it provides \
8, return 8. Preserve original metrics, tools, and outcomes verbatim where possible — \
light rephrasing only to fix grammar.
3. Tag each bullet with 2-5 short keyword tags drawn from its content (technologies, \
domains, skill categories, soft-skill signals). Tags drive selector matching later.
4. Preserve dates and locations exactly as written.
5. Skills section: group by the category headings in the source (e.g. 'GenAI Engineering', \
'Data Engineering & Cloud Infrastructure'). Split comma-separated items into individual entries.
6. If a field is absent from the source, omit it (do not invent placeholders)."""


def extract_pdf_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    try:
        return "\n\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()


def ingest_pdf(pdf_path: Path, *, client: Optional[OpenAI] = None) -> MasterCV:
    client = client or OpenAI()
    raw_text = extract_pdf_text(pdf_path)

    user_msg = (
        "Convert the following resume text into the MasterCV schema. "
        "Return ONLY the structured object — no commentary.\n\n"
        "---RESUME TEXT START---\n"
        f"{raw_text}\n"
        "---RESUME TEXT END---"
    )

    completion = client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format=MasterCV,
    )

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        refusal = completion.choices[0].message.refusal
        raise RuntimeError(
            f"LLM did not return valid MasterCV. refusal={refusal!r}, "
            f"finish_reason={completion.choices[0].finish_reason}"
        )
    return parsed
