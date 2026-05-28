"""Pure-regex extraction of "minimum years of experience" from a JD.

Returns an integer (the minimum required years) or None if not found.
Range conventions: "3-5 years" → floor 3 (lower bound = what's required).
"5+ years" → 5. "At least 5 years" → 5. "5 years of experience" → 5.
Across multiple statements in the same JD, returns the max — the binding
constraint when the JD asks for different mins for different skills.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

_RANGE_PAT = re.compile(
    r"\b(\d{1,2})\s*(?:-|to|–|—)\s*(\d{1,2})\s*\+?\s*years?\b",
    re.IGNORECASE,
)

# Each entry's capture group 1 holds the floor.
_SINGLE_PATTERNS = [
    re.compile(r"\b(\d{1,2})\s*\+\s*years?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:minimum(?:\s+of)?|min(?:\.|imum)?|at\s+least)\s+(\d{1,2})\s*\+?\s*years?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,2})\s+years?\s+(?:of\s+)?(?:relevant\s+|professional\s+|industry\s+)?(?:work\s+)?experience\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:require[sd]?|requires?)\s+(\d{1,2})\s*\+?\s*years?\b", re.IGNORECASE),
]


def _in_span(pos: int, spans: List[Tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)


def extract_min_yoe(jd: str) -> Optional[int]:
    if not jd:
        return None
    floors: List[int] = []
    range_spans: List[Tuple[int, int]] = []

    # Pass 1: ranges first so we can suppress single-pattern matches inside them.
    for m in _RANGE_PAT.finditer(jd):
        try:
            v = int(m.group(1))
        except (ValueError, IndexError):
            continue
        if 0 <= v <= 20:
            floors.append(v)
            range_spans.append((m.start(), m.end()))

    # Pass 2: single-value patterns, skipping any inside a range span.
    for pat in _SINGLE_PATTERNS:
        for m in pat.finditer(jd):
            if _in_span(m.start(), range_spans):
                continue
            try:
                v = int(m.group(1))
            except (ValueError, IndexError):
                continue
            if 0 <= v <= 20:
                floors.append(v)

    if not floors:
        return None
    return max(floors)
