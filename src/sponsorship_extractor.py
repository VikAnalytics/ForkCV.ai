"""Extract visa/sponsorship stance from a job description.

Pure regex over raw JD text — no LLM call. The selector and renderer never use
this signal; it's surfaced to the user in the report panel so they can decide
whether to apply.

Status values:
- 'unspecified'   — JD never mentions visa/sponsor/work-auth terms.
- 'mentioned'     — terms appear but stance unclear (e.g. "must be authorized to work").
- 'available'     — JD explicitly says sponsorship IS offered.
- 'not_available' — JD explicitly says sponsorship is NOT offered.
"""
from __future__ import annotations

import re
from typing import List

from .schemas import SponsorshipInfo

# Terms that trigger interest in a sentence at all.
# Includes citizenship / clearance / ITAR / "US person" because those imply
# no-sponsorship for non-citizens.
TRIGGER_RE = re.compile(
    r"\b("
    r"sponsorship|sponsor(?:ing|s|ed)?|"
    r"visa|"
    r"h[-\s]?1[-\s]?b|"
    r"work\s+authoriz(?:ation|ed)|"
    r"work\s+permit|"
    r"immigration|"
    r"green\s*card|"
    r"opt\s+(?:status|holders?)|"
    r"ead|"
    r"employment\s+authoriz(?:ation|ed)|"
    r"u\.?\s*s\.?\s+citizen(?:ship)?s?|"
    r"united\s+states\s+citizen(?:ship)?s?|"
    r"u\.?\s*s\.?\s+person(?:s)?|"
    r"security\s+clearance|"
    r"(?:secret|top\s+secret|ts/?sci|public\s+trust)\s+clearance|"
    r"clearance|"
    r"itar|ear[-\s]controlled"
    r")\b",
    re.IGNORECASE,
)

# Strong "we do NOT sponsor" patterns.
NEG_PATTERNS = [
    r"\bno\s+sponsorship\b",
    r"\bnot\s+(?:able|abled|in\s+a\s+position)\s+to\s+sponsor",
    r"\bunable\s+to\s+(?:offer|provide|sponsor)",
    r"\bdo(?:es)?\s+not\s+(?:offer|provide|sponsor|support)",
    r"\bcannot\s+sponsor",
    r"\bwill\s+not\s+sponsor",
    r"\bwithout\s+(?:the\s+)?(?:need\s+for\s+)?(?:current\s+or\s+future\s+)?(?:visa\s+)?sponsorship",
    r"\bineligible\s+for\s+sponsorship",
    r"\bsponsorship\s+is\s+not\s+(?:available|offered|provided)",
    r"\bno\s+visa\s+sponsorship",
    r"\bnot\s+(?:eligible\s+for|considering)\s+(?:visa\s+)?sponsorship",
    r"\bmust\s+be\s+(?:legally\s+)?authorized\s+to\s+work\s+in\s+the\s+(?:u\.?s\.?|united\s+states|uk|united\s+kingdom|canada|eu)"
    r"(?:[^.\n]*?)(?:without\s+(?:current\s+or\s+future\s+)?sponsorship|without\s+(?:the\s+)?need\s+for\s+sponsorship)",
    r"\bsponsorship\s+is\s+not\s+offered",
    r"\bunable\s+to\s+sponsor\s+(?:visas?|candidates?)",
    # Citizenship requirements — implies no visa sponsorship.
    r"\b(?:u\.?\s*s\.?|united\s+states)\s+citizen(?:ship)?\s+(?:is\s+)?(?:required|necessary|mandatory|a\s+must)",
    r"\bmust\s+be\s+(?:a\s+)?(?:u\.?\s*s\.?|united\s+states)\s+citizen",
    r"\b(?:u\.?\s*s\.?|united\s+states)\s+citizens?\s+only",
    r"\brestricted\s+to\s+(?:u\.?\s*s\.?|united\s+states)\s+citizens?",
    r"\bopen\s+(?:only\s+)?to\s+(?:u\.?\s*s\.?|united\s+states)\s+citizens?",
    r"\bu\.?\s*s\.?\s+persons?\s+only\b",
    # Security clearance requirements — citizenship-gated in practice.
    # Loose: any of {active|current|maintained|valid} + ... + "clearance" within
    # the same sentence ⇒ this is a US-government role, no sponsorship.
    r"\bsecurity\s+clearance\s+(?:is\s+)?(?:required|mandatory|necessary)",
    r"\b(?:active|current|maintained|valid)[^.\n]{0,80}clearance\b",
    r"\b\"?(?:secret|top\s+secret|ts/?sci|public\s+trust)\"?[^.\n]{0,60}clearance\b",
    r"\bclearance[^.\n]{0,60}(?:required|mandatory)",
    r"\bmust\s+(?:be\s+able\s+to\s+)?(?:obtain|maintain|hold|possess)\s+(?:a\s+|an\s+)?(?:secret|top\s+secret|ts/?sci|public\s+trust|security)?\s*clearance",
    r"\bitar(?:[-\s]controlled)?\b",
    r"\bear[-\s]controlled\b",
]
NEG_RE = re.compile("|".join(NEG_PATTERNS), re.IGNORECASE)

# Strong "we DO sponsor" patterns.
POS_PATTERNS = [
    r"\b(?:offer|provide|provides|offering)\s+(?:visa\s+)?sponsorship",
    r"\bsponsorship\s+(?:is\s+)?available",
    r"\bsponsorship\s+(?:is\s+)?(?:offered|provided)",
    r"\bwilling\s+to\s+sponsor",
    r"\bable\s+to\s+sponsor",
    r"\bopen\s+to\s+(?:visa\s+)?sponsor(?:ship|ing)",
    r"\bwe\s+(?:can\s+|will\s+)?sponsor",
    r"\bcan\s+sponsor",
    r"\bsponsor\s+(?:visas?|candidates?|qualified\s+candidates)",
    r"\bvisa\s+sponsorship\s+(?:is\s+)?available",
    r"\bh[-\s]?1[-\s]?b\s+(?:sponsorship\s+)?(?:available|offered|supported)",
    r"\bgreen\s*card\s+sponsorship",
]
POS_RE = re.compile("|".join(POS_PATTERNS), re.IGNORECASE)


def _split_sentences(text: str) -> List[str]:
    # Split on sentence boundaries and line breaks. Keep bullet lines intact.
    # Replace bullet markers so they don't fool the splitter, then chunk.
    cleaned = re.sub(r"[•●◦▪–—]", " ", text)
    # Split on newlines first, then sentence punctuation.
    out: List[str] = []
    for line in cleaned.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", line)
        out.extend(p.strip() for p in parts if p.strip())
    return out


def _shorten(s: str, max_len: int = 240) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def extract_sponsorship(jd: str) -> SponsorshipInfo:
    if not jd or not jd.strip():
        return SponsorshipInfo()

    matches: List[tuple[str, str]] = []  # (sentence, verdict)
    for sent in _split_sentences(jd):
        if not TRIGGER_RE.search(sent):
            continue
        if NEG_RE.search(sent):
            matches.append((sent, "not_available"))
        elif POS_RE.search(sent):
            matches.append((sent, "available"))
        else:
            matches.append((sent, "mentioned"))

    if not matches:
        return SponsorshipInfo(status="unspecified", evidence=[])

    verdicts = {v for _, v in matches}
    # Negative wins if present anywhere — a JD saying "we don't sponsor" overrides
    # an upstream "must have work authorization" hedge.
    if "not_available" in verdicts:
        status = "not_available"
    elif "available" in verdicts:
        status = "available"
    else:
        status = "mentioned"

    # Dedup evidence preserving order, prefer the lines with a definitive verdict.
    seen: set[str] = set()
    evidence: List[str] = []
    priority = {"not_available": 0, "available": 1, "mentioned": 2}
    for sent, _ in sorted(matches, key=lambda m: priority[m[1]]):
        key = sent.lower()
        if key in seen:
            continue
        seen.add(key)
        evidence.append(_shorten(sent))
        if len(evidence) >= 4:
            break

    return SponsorshipInfo(status=status, evidence=evidence)
