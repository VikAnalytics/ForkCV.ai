"""Extract language proficiency constraints from a JD.

Pure regex, no LLM. Surfaces required vs preferred language requirements with
proficiency level and evidence sentence. Skips bare mentions of a language with
no proficiency context (e.g. an English-language JD that just talks about
"English literature").
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .schemas import LanguageConstraint

LANGUAGES = {
    "English", "Spanish", "French", "German", "Italian", "Portuguese", "Dutch",
    "Mandarin", "Cantonese", "Chinese", "Japanese", "Korean", "Vietnamese", "Thai",
    "Indonesian", "Malay", "Tagalog", "Filipino", "Hindi", "Urdu", "Bengali",
    "Arabic", "Hebrew", "Turkish", "Persian", "Farsi",
    "Russian", "Polish", "Czech", "Ukrainian", "Romanian", "Hungarian", "Greek",
    "Swedish", "Norwegian", "Danish", "Finnish", "Catalan",
}
LANGUAGE_RE = re.compile(r"\b(" + "|".join(sorted(LANGUAGES, key=len, reverse=True)) + r")\b")

LEVEL_PATTERNS: List[Tuple[str, str]] = [
    # Order matters: alternation matches left-to-right, so list longer variants first.
    ("native",        r"\b(?:native[-\s]speaker|native[-\s]level|mother\s+tongue|first\s+language|native)\b"),
    ("fluent",        r"\b(?:jlpt\s*n1|hsk\s*[56]|fluently|fluency|fluent|c2)\b"),
    ("professional",  r"\b(?:professional\s+working\s+proficiency|professional\s+proficiency|business\s+proficiency|working\s+proficiency|business[-\s]level|jlpt\s*n2|c1)\b"),
    ("conversational", r"\b(?:conversational|intermediate|jlpt\s*n3|b2|b1)\b"),
    ("basic",         r"\b(?:elementary|beginner|basic|jlpt\s*n[45]|a1|a2)\b"),
    ("bilingual",     r"\b(?:multilingual|trilingual|bilingual)\b"),
]

# Words that indicate the language is part of a *requirement* context (not random prose).
PROFICIENCY_CUE_RE = re.compile(
    r"\b(?:"
    r"fluent|fluency|fluently|"
    r"native|mother\s+tongue|first\s+language|"
    r"proficien(?:t|cy)|proficiency|"
    r"speak|spoken|speaker|written|"
    r"command\s+of|knowledge\s+of|"
    r"bilingual|trilingual|multilingual|"
    r"conversational|business[-\s]level|"
    r"(?:c|b|a)[12]|jlpt|hsk|"
    r"language\s+skills?|languages?\s+required|languages?\s+preferred"
    r")\b",
    re.IGNORECASE,
)

REQUIRED_CUE_RE = re.compile(
    r"\b(?:must|required|mandatory|essential|necessary|need(?:ed)?\s+to|should\s+have|requirement)\b",
    re.IGNORECASE,
)
PREFERRED_CUE_RE = re.compile(
    r"\b(?:preferred|nice[-\s]to[-\s]have|plus|bonus|advantage|ideally|"
    r"a\s+plus|would\s+be\s+a\s+plus|appreciated|desirable|beneficial)\b",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> List[str]:
    cleaned = re.sub(r"[•●◦▪–—]", " ", text)
    out: List[str] = []
    for line in cleaned.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", line)
        out.extend(p.strip() for p in parts if p.strip())
    return out


def _level_spans(sent: str) -> List[Tuple[int, int, str]]:
    """All level matches in the sentence: (start, end, level)."""
    out: List[Tuple[int, int, str]] = []
    for level, pat in LEVEL_PATTERNS:
        for m in re.finditer(pat, sent, re.IGNORECASE):
            out.append((m.start(), m.end(), level))
    return out


def _closest_level(sent: str, lang_start: int, lang_end: int, max_dist: int = 40) -> str:
    best_level = ""
    best_dist = max_dist + 1
    for s, e, lvl in _level_spans(sent):
        if e <= lang_start:
            dist = lang_start - e
        elif s >= lang_end:
            dist = s - lang_end
        else:
            dist = 0
        if dist <= max_dist and dist < best_dist:
            best_dist = dist
            best_level = lvl
    return best_level


_INLINE_PAREN_RE = re.compile(r"^\s*[\(\[]\s*([^\)\]\n]{1,40})\s*[\)\]]")


def _inline_qualifier(sent: str, lang_end: int) -> str | None:
    """Return 'required' / 'preferred' / None for an inline parenthetical right after the language token."""
    m = _INLINE_PAREN_RE.match(sent[lang_end:])
    if not m:
        return None
    inner = m.group(1)
    if PREFERRED_CUE_RE.search(inner):
        return "preferred"
    if REQUIRED_CUE_RE.search(inner):
        return "required"
    # Levels alone don't decide required/preferred, but a bare level qualifier still useful — we
    # return None so caller falls back to sentence-level inference.
    return None


_LEVEL_RANK = {"native": 5, "fluent": 4, "professional": 3, "bilingual": 3, "conversational": 2, "basic": 1, "": 0}


def _merge(existing: LanguageConstraint, new: LanguageConstraint) -> LanguageConstraint:
    """Combine two constraints for the same language; keep the strongest signal."""
    # Required wins over preferred.
    required = existing.required or new.required
    # Higher level wins.
    if _LEVEL_RANK.get(new.level, 0) > _LEVEL_RANK.get(existing.level, 0):
        level = new.level
        evidence = new.evidence
    else:
        level = existing.level
        evidence = existing.evidence
    # Prefer a required-tagged evidence sentence when stance flips to required.
    if required and not existing.required and new.required:
        evidence = new.evidence or evidence
    return LanguageConstraint(language=existing.language, level=level, required=required, evidence=evidence)


def _shorten(s: str, max_len: int = 240) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def extract_languages(jd: str) -> List[LanguageConstraint]:
    if not jd or not jd.strip():
        return []

    by_lang: Dict[str, LanguageConstraint] = {}

    for sent in _split_sentences(jd):
        # Find language hits in this sentence.
        hits = list(LANGUAGE_RE.finditer(sent))
        if not hits:
            continue
        # Sentence must have proficiency context anywhere in it, OR list-style cue ("language:"/"languages required").
        if not PROFICIENCY_CUE_RE.search(sent) and not re.search(r"\blanguages?\s*[:\-—]\s*", sent, re.IGNORECASE):
            continue

        # Sentence-level default for required/preferred.
        sent_required = REQUIRED_CUE_RE.search(sent) is not None
        sent_preferred = PREFERRED_CUE_RE.search(sent) is not None
        if sent_preferred and not sent_required:
            sent_default_required = False
        else:
            # If both cues present, treat as required by default — per-language inline
            # parens (below) override on a case-by-case basis.
            sent_default_required = True

        for m in hits:
            lang = m.group(1)
            level = _closest_level(sent, m.start(), m.end())
            inline = _inline_qualifier(sent, m.end())
            if inline == "required":
                required = True
            elif inline == "preferred":
                required = False
            else:
                required = sent_default_required
            ev = _shorten(sent)
            constraint = LanguageConstraint(language=lang, level=level, required=required, evidence=ev)
            prev = by_lang.get(lang)
            by_lang[lang] = _merge(prev, constraint) if prev else constraint

    # Order: required first, then by level rank desc, then alpha.
    return sorted(
        by_lang.values(),
        key=lambda c: (not c.required, -_LEVEL_RANK.get(c.level, 0), c.language),
    )
