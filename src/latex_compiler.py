from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .schemas import MasterCV

_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_LATEX_PATTERN = re.compile("|".join(re.escape(k) for k in _LATEX_ESCAPES))

# Unicode → LaTeX canonical replacements (run BEFORE escape pattern so the
# replacement chars don't get re-escaped). Covers chars that T1 fontenc + lmodern
# silently drop or mis-render under tectonic.
_UNICODE_REPLACEMENTS = {
    "—": "---",   # em-dash
    "–": "--",    # en-dash
    "‘": "`",     # left single quote
    "’": "'",     # right single quote / apostrophe
    "“": "``",    # left double quote
    "”": "''",    # right double quote
    "…": r"\ldots{}",  # ellipsis
    " ": "~",     # non-breaking space
    "·": r"\textperiodcentered{}",  # middle dot
    "•": r"\textbullet{}",          # bullet
}


# ATS-friendly ligature breaker. lmodern (T1) composes fi/fl/ff/ffi/ffl into
# single ligature glyphs whose ToUnicode map emits U+FB01/U+FB02/U+FB00/etc.,
# causing keyword search in some ATS to miss "efficient" in "eﬃcient". We
# insert an empty TeX group `{}` between the trigger letters so the typesetter
# never reaches the ligature lookup; the rendered visual is unchanged because
# the kerning between separate `f` and `i` glyphs is the same with or without
# the ligature substitution at body-text sizes.
_LIGATURE_BREAKER = re.compile(r"f(?=[fil])")


def tex_escape(value: Any) -> str:
    """Escape LaTeX special characters, normalize Unicode punctuation to
    canonical LaTeX equivalents, and break common ligatures for ATS parsing.
    Returns '' for None.

    Order: LaTeX-special escape, then Unicode replacements (their LaTeX cmds
    contain `\\` and `{}` so they must NOT be re-escaped), then the ligature
    breaker (it inserts more `{}` which also must not be re-escaped)."""
    if value is None:
        return ""
    s = str(value)
    s = _LATEX_PATTERN.sub(lambda m: _LATEX_ESCAPES[m.group(0)], s)
    for u, repl in _UNICODE_REPLACEMENTS.items():
        s = s.replace(u, repl)
    s = _LIGATURE_BREAKER.sub("f{}", s)
    return s


# URL chars that need escaping inside \href{URL}{...}. Hyperref accepts most
# special chars in the URL arg unescaped, but %, #, and \ must be escaped.
_URL_ESCAPES = {"\\": r"\textbackslash{}", "%": r"\%", "#": r"\#"}
_URL_PATTERN = re.compile("|".join(re.escape(k) for k in _URL_ESCAPES))


def tex_url(value: Any) -> str:
    """Escape a URL for use as the first arg of `\\href`. Only escapes the chars
    hyperref objects to; leaves everything else (including `_`, `~`, `&`) raw."""
    if value is None:
        return ""
    s = str(value).strip()
    return _URL_PATTERN.sub(lambda m: _URL_ESCAPES[m.group(0)], s)


def short_url(value: Any) -> str:
    """Display form of a URL — strip scheme and leading `www.` so the visible
    text is compact (e.g. `github.com/foo/bar` rather than `https://...`)."""
    if value is None:
        return ""
    s = str(value).strip()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s.startswith("www."):
        s = s[4:]
    return s


def build_jinja_env(templates_dir: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        block_start_string="<%",
        block_end_string="%>",
        variable_start_string="<<",
        variable_end_string=">>",
        comment_start_string="<#",
        comment_end_string="#>",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        undefined=StrictUndefined,
    )
    env.filters["tex"] = tex_escape
    env.filters["url"] = tex_url
    env.filters["short_url"] = short_url
    return env


# Tightness presets — used by the page-fit retry loop. Each level reduces
# font size + margins. Tweak here if you want different gradations.
TIGHTNESS_PRESETS = [
    {"font_pt": "10",   "margin_lr": "0.8",  "margin_tb": "0.5"},   # normal
    {"font_pt": "9.5",  "margin_lr": "0.6",  "margin_tb": "0.4"},   # tight
    {"font_pt": "9",    "margin_lr": "0.5",  "margin_tb": "0.35"},  # very tight
    {"font_pt": "8.5",  "margin_lr": "0.4",  "margin_tb": "0.3"},   # last resort
]


def render_tex(
    cv: MasterCV,
    *,
    templates_dir: Path,
    template_name: str = "resume.tex.j2",
    tightness: int = 0,
) -> str:
    """Render the resume LaTeX from `cv`. `tightness` (0-3) picks a preset
    from TIGHTNESS_PRESETS — higher = smaller font + tighter margins."""
    env = build_jinja_env(templates_dir)
    template = env.get_template(template_name)
    preset = TIGHTNESS_PRESETS[max(0, min(tightness, len(TIGHTNESS_PRESETS) - 1))]
    return template.render(cv=cv, **preset)


def _count_pdf_pages(pdf_path: Path) -> int:
    """Return the page count of a PDF. Falls back to 1 on import error."""
    try:
        import fitz  # PyMuPDF
        with fitz.open(str(pdf_path)) as doc:
            return doc.page_count
    except Exception:
        return 1


def compile_pdf(tex_path: Path, *, out_dir: Path | None = None) -> Path:
    """Run tectonic on tex_path. Returns path to produced PDF."""
    if shutil.which("tectonic") is None:
        raise RuntimeError("tectonic not installed. `brew install tectonic`.")

    out_dir = out_dir or tex_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["tectonic", "--keep-logs", "--outdir", str(out_dir), str(tex_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"tectonic failed (exit {result.returncode}).\n"
            f"--- stderr ---\n{result.stderr}\n"
            f"--- stdout ---\n{result.stdout}"
        )

    pdf_path = out_dir / (tex_path.stem + ".pdf")
    if not pdf_path.exists():
        raise RuntimeError(f"tectonic returned 0 but no PDF at {pdf_path}")
    return pdf_path
