"""Generate LinkedIn note + email draft per contact.

The goal is messages that DON'T sound AI-generated. Conventional LLM tells:
em-dashes, "I hope this email finds you well", "I came across", "I'm
excited/thrilled/passionate to", three-paragraph cold-email scaffold,
unnecessary closing pleasantries.

Defense: an aggressively-worded system prompt with explicit bans + one
required concrete recipient-fact + one required concrete sender-fact +
length caps + tone calibration. We also post-process to strip em-dashes
and the worst boilerplate openers if the model slips.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

from openai import OpenAI

from .schemas import (
    JDAnalysis,
    MasterCV,
    OutreachDraft,
    ScoredContact,
)

MODEL = "gpt-4o-mini"


# Phrases we never want to see in the output. Lowercase substring check.
BANNED_PHRASES = [
    "i hope this email finds you well",
    "i hope this finds you well",
    "i hope this message finds you well",
    "i came across your profile",
    "i came across your linkedin",
    "i wanted to reach out",
    "i am writing to express",
    "i am reaching out to express",
    "i'm reaching out to express",
    "i would like to introduce myself",
    "as a passionate",
    "as an enthusiastic",
    "i'm passionate about",
    "i am passionate about",
    "i'm thrilled",
    "i am thrilled",
    "i'm excited to",
    "i am excited to",
    "looking forward to hearing from you",
    "i would love to connect",
    "would love to connect",
    "thank you for your time and consideration",
    "thank you for considering",
    "please let me know if",
    "i hope you are doing well",
]


SYSTEM_PROMPT = """You write SHORT cold outreach for job seekers — a LinkedIn note and a follow-up email. Your job is to sound like a real, slightly-tired human who actually did 60 seconds of research on the recipient, NOT like ChatGPT. Most candidates' outreach is AI-generated slop; yours has to read so differently that the recipient pauses.

HARD BANS — never produce text containing any of these phrases or their close paraphrases:
- "I hope this email/message finds you well"
- "I came across your profile / LinkedIn"
- "I wanted to reach out"
- "I am writing / I'm reaching out to express"
- "I'm thrilled / excited / passionate / delighted"
- "Looking forward to hearing from you"
- "Thank you for your time and consideration"
- "I would love to connect" (use "would be great to chat" or just skip it)
- ANY em-dash ("—") — use commas or periods or parentheses
- ANY semicolons in cold outreach (too formal)
- The word "synergy", "ecosystem", "leverage" (as a verb), "passionate", "thrilled"
- Three-paragraph cold-email scaffold (intro / pitch / ask)

STRUCTURAL RULES:
1. LinkedIn note: 1-3 sentences, MAX 280 characters total (LinkedIn's hard cap is 300). No formal salutation — start with their first name + comma OR jump straight in. End with a short, low-stakes ask.
2. Email: subject line MAX 60 characters, ideally 40. Body 4-6 short sentences, NO bullet points (bullets read templated). Sign-off is just the sender's first name, no "Best regards", no "Sincerely".
3. Mix sentence lengths. Use contractions ("I'm", "you're", "doesn't"). A short fragment is fine if it lands.

REQUIRED CONTENT — every message must contain BOTH of these grounded specifics:
A. ONE CONCRETE thing about the RECIPIENT, drawn from their title, tenure, past company, school, or department. Not "your impressive background", not "your great work". Real specifics: "after Databricks → Stripe in 2024", "the analytics infra team", "the UT-Austin connection".
B. ONE CONCRETE thing about the SENDER's relevant work, drawn from their master CV bullets or the job they're targeting. Not "I have 5 years of experience", not "I am passionate about data". Real: "shipped a real-time CDC pipeline that cut Snowflake spend 40%", "rewrote 200 Teradata queries into BigQuery for a client at Incedo".

TONE: Direct, slightly informal, confident but not cocky. Sound like a smart engineer writing at 11pm before applying. Not over-eager. Not desperate. No throat-clearing.

CLOSING: Ask for something tiny and specific — 15 minutes to chat, a referral, advice on the team's tech stack, whether they're open to a quick intro. Avoid "any guidance you can share" — too vague.

OUTPUT JSON ONLY, schema:
{
  "linkedin_note": "<= 280 chars",
  "email_subject": "<= 60 chars",
  "email_body": "4-6 short sentences"
}"""


def _user_message(
    contact: ScoredContact,
    master: MasterCV,
    company: str,
    role_title: str,
    jd: Optional[JDAnalysis] = None,
) -> str:
    c = contact.contact
    # Compact recipient profile.
    recipient = {
        "name": c.name,
        "first_name": c.name.split()[0] if c.name else "",
        "title": c.title,
        "company": c.organization_name or company,
        "headline": c.headline,
        "departments": c.departments,
        "category": contact.category,  # recruiter / hiring_manager / team_ic / other
        "tenure_months_at_company": contact.tenure_months,
        "shared_signals": contact.shared_signals,  # things we discovered
        "past_companies": [e.organization_name for e in c.employment_history[:4] if e.organization_name],
        "schools": [ed.school for ed in c.education[:2] if ed.school],
    }
    # Compact sender profile from master CV.
    bullets: List[str] = []
    for exp in (master.experience or [])[:3]:
        for b in (exp.bullet_pool or [])[:2]:
            bullets.append(f"[{exp.role} @ {exp.company}] {b.text}")
    sender = {
        "name": master.personal_info.name,
        "first_name": (master.personal_info.name or "").split()[0],
        "summary": master.professional_summary,
        "schools": [e.institution for e in (master.education or [])[:2]],
        "past_companies": [e.company for e in (master.experience or [])[:5]],
        "top_bullets": bullets[:5],
    }
    target = {
        "company": company,
        "role_title": role_title,
        "role_keywords": (jd.must_have_keywords[:6] if jd else []) + (jd.primary_tech_stack[:4] if jd else []),
    }
    return (
        "RECIPIENT (the person being messaged):\n"
        f"{json.dumps(recipient, indent=2)}\n\n"
        "SENDER (the candidate, writing the message):\n"
        f"{json.dumps(sender, indent=2)}\n\n"
        "TARGET ROLE:\n"
        f"{json.dumps(target, indent=2)}\n\n"
        "Write the LinkedIn note + email. Return JSON only."
    )


# ── Post-processing ──────────────────────────────────────────────────────
_EM_DASH_RE = re.compile(r"\s*[—–]\s*")
_BANNED_RE = re.compile(
    "|".join(re.escape(p) for p in BANNED_PHRASES),
    re.IGNORECASE,
)


def _scrub(text: str) -> str:
    if not text:
        return ""
    # Replace em-dashes / en-dashes with comma + space (most common natural sub).
    text = _EM_DASH_RE.sub(", ", text)
    # Remove banned phrases entirely; cleanup trailing punctuation.
    text = _BANNED_RE.sub("", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ensure_length(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    # Try not to chop mid-word.
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",;: ") + "…"


def generate_outreach(
    contact: ScoredContact,
    master: MasterCV,
    company: str,
    role_title: str,
    *,
    jd: Optional[JDAnalysis] = None,
    client: Optional[OpenAI] = None,
) -> OutreachDraft:
    client = client or OpenAI()
    completion = client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_message(contact, master, company, role_title, jd)},
        ],
        response_format=OutreachDraft,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        # Fallback to a minimal, hand-written template if structured output failed.
        first = (contact.contact.name or "there").split()[0]
        return OutreachDraft(
            linkedin_note=f"{first}, applying for {role_title} at {company}. Would 15 min to chat about the team be possible?",
            email_subject=f"{role_title} at {company} - quick intro",
            email_body=(
                f"Hi {first},\n\nI just applied for the {role_title} role at {company} and wanted to "
                f"introduce myself briefly. Happy to share a tailored resume and answer any questions. "
                f"15 minutes whenever works for you?\n\n"
                f"{(master.personal_info.name or '').split()[0]}"
            ),
        )

    # Scrub banned phrases / em-dashes.
    return OutreachDraft(
        linkedin_note=_ensure_length(_scrub(parsed.linkedin_note), 280),
        email_subject=_ensure_length(_scrub(parsed.email_subject), 70),
        email_body=_scrub(parsed.email_body),
    )
