from __future__ import annotations

import json
from typing import List, Optional

from openai import OpenAI

from .schemas import (
    Bullet,
    JDAnalysis,
    MasterCV,
    ProjectSelection,
    SelectedBullet,
    TailoredSelection,
)

MODEL = "gpt-4o-mini"
MAX_BULLETS_PER_ROLE = 3
MAX_PROJECTS = 3
REQUIRED_PROJECTS = 3
MAX_BULLETS_PER_PROJECT = 1

# Mutually-exclusive project pairs — name substrings (case-insensitive).
# If both members of a pair end up in the selection, the lower-scoring one is
# dropped and the slot is filled by the next master_cv project that doesn't
# violate any exclusion.
PROJECT_EXCLUSIONS = [
    ("silent degradation", "unified network"),
]
TARGET_BULLET_CHARS_MIN = 160
TARGET_BULLET_CHARS_MAX = 230
TARGET_PROJECT_BULLET_CHARS_MIN = 220
TARGET_PROJECT_BULLET_CHARS_MAX = 320


def target_bullets_for_role(role_index: int, exp=None) -> int:
    """Returns the bullet count for a given role. Defaults to MAX_BULLETS_PER_ROLE
    unless the Experience explicitly sets `target_bullets_override`."""
    if exp is not None and getattr(exp, "target_bullets_override", None):
        try:
            return max(1, int(exp.target_bullets_override))
        except (TypeError, ValueError):
            pass
    return MAX_BULLETS_PER_ROLE

SYSTEM_PROMPT = f"""You are an elite Technical Recruiter performing the highest-leverage step \
of a resume-tailoring pipeline. Your output is rendered into a one-page LaTeX resume that needs \
to FILL the page with substance — short, sparse bullets leave blank space and signal weak experience.

You receive:
1. A `MasterCV` JSON containing a pool of bullets per experience and project.
2. A `JDAnalysis` summarizing the target role with `primary_tech_stack`, `core_impact_areas`, \
and `must_have_keywords`.

For EACH experience role and project, pick the most relevant bullets and REWRITE them to \
maximize JD-alignment density.

REWRITING RULES (read every one — most failures here come from skipping a rule):

1. LENGTH & SUBSTANCE DENSITY: target {TARGET_BULLET_CHARS_MIN}-{TARGET_BULLET_CHARS_MAX} \
characters per bullet. Do NOT compress below {TARGET_BULLET_CHARS_MIN}.

The length budget exists to surface MORE SUBSTANCE — additional tools used in the work, \
secondary outcomes, scale figures, technique details — pulled from the source bullet or \
its obvious context. The budget is NOT for padding with vague phrases. A 220-char bullet \
that adds a real tool and a real outcome is better than a 180-char bullet that adds two \
adjectives.

**Anti-AI-fluff** (forbidden as tail-padding): these phrases tend to show up at the END of \
a bullet as filler when the model runs out of substance:
- "ensuring reliability for critical data pipelines"
- "enabling faster responses to operational challenges"
- "essential for stakeholder trust"
- "ensuring higher reliability in the software development lifecycle"
- "enhancing market monitoring capabilities"
- "maximizing engagement during critical high-traffic periods"
- "supporting analytical processes", "driving operational excellence"
- "in support of strategic initiatives", "in alignment with business goals"
- "delivering measurable business value", "strengthening data trust"

ALSO forbidden: leading verbs followed by a vague abstract object — "facilitating X", \
"ensuring X", "supporting X", "enabling X", "driving X", "enhancing X" — when X is an abstract \
noun ("excellence", "alignment", "outcomes", "capabilities", "reliability", "trust"). These \
are acceptable ONLY when X is concrete: "ensuring 100% data accuracy", "enabling stakeholders \
to query data marts directly", "driving a 33% uptime improvement".

**SUBSTANCE TEST**: for every clause you write, ask "what specific, verifiable fact does this \
add?" If the answer is "nothing — it just sounds professional", DELETE the clause. End the \
bullet at the last substantive fact. A 165-char bullet with three real facts is BETTER than a \
225-char bullet with three real facts plus a trailing fluff clause.

**HOW TO USE THE LENGTH BUDGET CORRECTLY**: when expanding a short bullet toward the target \
length, add:
- A second tool that was used: "...using Python and PostgreSQL" → "...using Python, PostgreSQL, \
  and Airflow for orchestration"
- A specific number or scale: "...processing user data" → "...processing 10M+ daily events"
- A specific downstream consumer: "...for the finance team" → "...for the FP&A team's monthly \
  close process"
- A specific method: "...optimizing the pipeline" → "...optimizing via columnar partitioning \
  and predicate pushdown"
DO NOT add abstract closers, tone modifiers, or restatements of impact.

2. **LOGICAL COHERENCE — THIS IS THE MOST IMPORTANT RULE**. Every rewritten bullet must read as \
TRUE and natural to someone who actually knows the project's domain. A keyword cannot be \
injected just because the JD wants it — it must be plausibly true of the *specific* work being \
described. Run this self-check before finalizing each bullet:
   - "If the candidate's former teammate read this rewritten bullet, would they nod or laugh?"
   - "Does this keyword pertain to THIS project's actual subject matter, or am I forcing a \
     thematic match that doesn't exist?"

   **Concrete examples of ILLEGAL injections** (these have happened before and must not recur):
   - Twitter bot publishing sports statistics → MUST NOT mention "regulatory compliance", \
     "compliance monitoring", "financial reporting", "audit trails". A bot tweeting football \
     scores has no compliance dimension.
   - Sentiment-analysis tool aggregating public news/HN posts about consumer tech → MUST NOT \
     mention "regulatory compliance", "audit", "GDPR", "PHI/PII handling". Public-data sentiment \
     analysis is not a regulated activity.
   - Hobby/portfolio project for general consumers → MUST NOT mention "stakeholder reporting", \
     "enterprise governance", "executive dashboards" unless the source explicitly says so.
   - Backend pipeline at e-commerce/ad-tech employer → MUST NOT mention "insurance underwriting" \
     or "claims processing" unless the source mentions it.

   When the JD's domain (e.g. "insurance", "fintech", "healthcare") does NOT match the project's \
   actual domain, DO NOT inject the domain keyword. The candidate's career-narrative can \
   surface domain interest in the SUMMARY (handled elsewhere), not in falsified bullets.

3. KEYWORD INJECTION (within the logical-coherence constraint): weave `primary_tech_stack` and \
`must_have_keywords` from the JD into bullets where the work TRULY involved that capability. \
Examples of LEGITIMATE injection:
   - Source: "Built Looker dashboards for finance team" + JD wants `dashboarding tools` \
     -> Add the generic phrase "dashboarding" alongside "Looker". Both are true.
   - Source: "Optimized BigQuery indexing" + JD wants `data marts` \
     -> If the optimization served downstream consumers, recast as "enabling data marts for \
        downstream analytics". Same underlying fact.
   - Source: "Engineered SQL migration pipeline" + JD wants `SQL` \
     -> Surface "SQL" verbatim in the rewrite. Already true.

   **SEMANTIC-EQUIVALENCE PARAPHRASE — REQUIRED, NOT OPTIONAL**. A JD keyword often describes \
   the SAME concrete work the source bullet describes using different vocabulary. In that case \
   you MUST rewrite the lead verb or noun phrase to surface the JD's exact wording — the \
   underlying fact is unchanged so it is fully truthful. Failing to paraphrase when the match \
   is semantically tight is the #1 cause of ATS misses on otherwise-strong bullets.

   **DECISION ALGORITHM** (apply per bullet, per JD keyword):
   Step 1: Ask "Does the source bullet's work, if described to a generalist, fit under this \
   JD keyword?" If yes → semantic match → proceed. If no → thematic stretch → omit.
   Step 2: Ask "Can I swap one noun phrase or lead verb in the source bullet to use the JD's \
   exact wording, without inventing any new fact?" If yes → REWRITE. If no → use the keyword \
   in skills or a different bullet instead.

   Concrete pattern: an "X-flavored pipeline / ETL / data flow / transformation" IS \
   "data pipeline development" / "data ingestion" / "ETL" / "batch processing" / \
   "data engineering" — as long as the source involves moving or transforming data. \
   Always paraphrase in this case.
   - Source: "Engineered an automated SQL migration pipeline using Gemini..." + JD wants \
     `data pipeline development` \
     -> Rewrite as: "Engineered data pipeline development for automated SQL migrations using \
        Gemini..." A SQL-migration pipeline IS a data pipeline; the work is identical, only \
        the noun phrase changes.
   - Source: "Built an ETL job that loads marketing-event data into Snowflake" + JD wants \
     `data ingestion` \
     -> "Built data ingestion ETL loading marketing-event data into Snowflake". ETL IS \
     ingestion; same work.
   - Source: "Wrote a Python script that fetches and joins vendor reports nightly" + JD wants \
     `batch processing` \
     -> "Engineered nightly batch processing in Python to fetch and join vendor reports". A \
     nightly script IS batch processing; same work.
   - Source: "Refactored 12 stored procedures into modular dbt models" + JD wants \
     `data modeling` \
     -> "Refactored 12 stored procedures into modular dbt data models". The new noun \
     ("data models") is what dbt models ARE.

   What you may NOT do during paraphrase:
   - Add NEW tools or technologies not in the source (no inventing "Airflow" or "Kafka" because \
     the JD wants it).
   - Add NEW metrics, scale figures, or outcome numbers — preserve source numbers verbatim.
   - Change the underlying SYSTEM, DOMAIN, or PURPOSE of the work (a logging pipeline cannot \
     become "real-time fraud detection" via paraphrase, no matter how the JD frames it).
   - Stack multiple JD keywords into one bullet if the work only legitimately supports one — \
     forcing 3 keywords into one sentence reads as keyword-stuffing.

   When in doubt about whether an injection is truthful, OMIT IT. The audit layer surfaces \
   every injection to a human reviewer; injections that fail review damage the candidate's \
   credibility.

4. QUANTIFICATION: EVERY bullet must contain at least one concrete quantifier — a percentage, \
$ amount, count (e.g. '200+ queries', '150+ DAGs'), time saved (e.g. '30 mins'), or scale figure. \
If the source bullet has a metric, preserve it verbatim. If it doesn't (rare), keep the source \
phrasing unchanged rather than invent one.

5. NEVER CHANGE THE FUNDAMENTAL ACTION OR SYSTEM. A bullet about "Airflow DAG stability" cannot \
become a bullet about "dbt model deployment" — pick a different source bullet if the JD asks for \
dbt and no bullet fits.

6. SELECTION COUNT — EXPERIENCE: per-role, exact:
   - The user message contains a BULLET COUNTS block listing the exact number of \
     bullets required for each role. Pick by JD relevance, weighted toward \
     `core_impact_areas`, until you've hit that count.
   - If a role's count exceeds its pool size, select all available bullets.
   - DO NOT default to 3 — flagship roles may require 5+ bullets and others may require only 2.

7. ORDERING: order selected bullets within each role by DESCENDING relevance score.

8. INDEXING: `source_index` is ZERO-BASED into the source `bullet_pool`. Must be in bounds.

9. EXACT KEY MATCH: `company`, `role`, `name` fields MUST match the source verbatim so the merge \
step succeeds. Copy them character-for-character.

10. NO EXPERIENCE OMISSIONS: include EVERY experience role from the source, even low-relevance \
ones. The pipeline expects 1:1 experience coverage.

11. PROJECTS — DIFFERENT RULES (resume is one page; projects are the relief valve):
   - Select EXACTLY {REQUIRED_PROJECTS} projects (no more, no fewer) — the ones whose \
     underlying work BEST matches the JD's `primary_tech_stack`, `core_impact_areas`, or domain. \
     If the master pool has fewer than {REQUIRED_PROJECTS}, select all available.
   - **Mutually-exclusive pairs** (do NOT pick both members of any pair — pick one and choose a \
     different third project from the remaining pool):
       * "Silent Degradation Detection" and "Unified Network Health Monitor" — they cover similar \
         telecom-domain anomaly-detection territory and look repetitive side-by-side. Pick the \
         one closer to the JD and let a different project (e.g. FPL Intel, IPL ETL, Omni-Tracker, \
         CommuteFlow) fill the third slot.
   - For EACH selected project: produce EXACTLY {MAX_BULLETS_PER_PROJECT} summarized bullet \
     ({TARGET_PROJECT_BULLET_CHARS_MIN}-{TARGET_PROJECT_BULLET_CHARS_MAX} chars). `source_index` \
     should point to the SINGLE most representative source bullet you drew from.

   **TECHNICAL SHOWCASE — projects are where engineering depth is visible, NOT a marketing blurb.**
   The bullet should read like an engineer briefing a senior engineer. Pack it with:
   - Named tools, libraries, services, or model IDs from the source ("OpenAI gpt-4o-mini", \
     "pgvector cosine", "Isolation Forest", "Markowitz max-Sharpe", "DuckDB", "spaCy en_core_web_sm", \
     "768-dim sentence-transformers", "Z-score against HoD/DoW baseline").
   - Specific numeric thresholds, dimensions, or scale figures ("0.82 similarity threshold", \
     "6-factor model", "2.73M hourly KPI rows", "5-min OHLCV bars", "$1,000 virtual portfolio").
   - Architectural decisions when they're load-bearing ("bronze/silver/gold layering", \
     "embedded DuckDB — no DB server", "EDGAR submissions API, no auth required").
   - Concrete pipelines or flows ("RSS → spaCy filter → OpenAI extract → Supabase").

   **AVOID** generic phrases ("enabling", "enhancing", "providing actionable insights", \
   "improving decision-making", "advanced analytics") — these waste characters that should be \
   carrying technical content. End at the last specific fact, not on a vague impact clause.

   - Apply the same logical-coherence rigor as experience: no fabrication, no keyword injection \
     where the underlying work doesn't support it. Names of tools you cite MUST appear in the \
     project's source bullets.
   - Do NOT include projects you are dropping in `project_selections`. Only include the kept ones."""


def _user_message(master_cv: MasterCV, jd_analysis: JDAnalysis) -> str:
    cv_payload = {
        "experience": [
            {
                "company": e.company,
                "role": e.role,
                "bullet_pool": [b.text for b in e.bullet_pool],
            }
            for e in master_cv.experience
        ],
        "projects": [
            {"name": p.name, "bullet_pool": [b.text for b in p.bullet_pool]}
            for p in master_cv.projects
        ],
    }
    # Build a per-role bullet-count manifest (honoring target_bullets_override).
    per_role_counts = []
    for i, e in enumerate(master_cv.experience):
        target = min(target_bullets_for_role(i, e), len(e.bullet_pool))
        per_role_counts.append(
            f"  - Index {i}: {e.role} @ {e.company} → EXACTLY {target} bullets "
            f"(pool has {len(e.bullet_pool)} candidates)"
        )
    counts_block = "\n".join(per_role_counts)

    return (
        "MASTER_CV (bullet pools):\n"
        f"{json.dumps(cv_payload, indent=2)}\n\n"
        "JD_ANALYSIS:\n"
        f"{jd_analysis.model_dump_json(indent=2)}\n\n"
        "BULLET COUNTS — these OVERRIDE any default rule-of-3 in the system prompt. "
        "Pick exactly this many bullets for each role, no more, no fewer:\n"
        f"{counts_block}\n\n"
        f"Also return EXACTLY {REQUIRED_PROJECTS} projects with {MAX_BULLETS_PER_PROJECT} bullet each."
    )


def _validate_tiered_bullets(master_cv: MasterCV, selection: TailoredSelection) -> List[str]:
    """Return warnings for roles where bullet count violates the tiered cap."""
    warnings: List[str] = []
    selections_by_key = {
        (s.company.strip().lower(), s.role.strip().lower()): s
        for s in selection.experience_selections
    }
    for i, exp in enumerate(master_cv.experience):
        pool_size = len(exp.bullet_pool)
        target = min(target_bullets_for_role(i, exp), pool_size)
        key = (exp.company.strip().lower(), exp.role.strip().lower())
        sel = selections_by_key.get(key)
        actual = len(sel.selected_bullets) if sel else 0
        if actual != target:
            warnings.append(
                f"index {i} — {exp.role} @ {exp.company}: expected {target} bullets "
                f"(tier cap, pool={pool_size}), got {actual}"
            )
    return warnings


def select(
    master_cv: MasterCV,
    jd_analysis: JDAnalysis,
    *,
    client: Optional[OpenAI] = None,
) -> TailoredSelection:
    client = client or OpenAI()
    completion = client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_message(master_cv, jd_analysis)},
        ],
        response_format=TailoredSelection,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(
            f"Selector returned no structured output. refusal={completion.choices[0].message.refusal!r}"
        )

    # Hard contract: each role must have min(target_for_index, pool_size) bullets per the
    # tiered cap (3/3/2/2/1...). Retry once with an explicit nudge if violated, then
    # truncate/log if the model still misbehaves.
    warnings = _validate_tiered_bullets(master_cv, parsed)
    if warnings:
        retry_msg = (
            _user_message(master_cv, jd_analysis)
            + "\n\nIMPORTANT: your previous response had the wrong bullet counts. "
            + "Re-read the BULLET COUNTS block above and produce EXACTLY the specified "
            + "number of bullets per role. Violations from last attempt:\n- "
            + "\n- ".join(warnings)
            + "\nReturn a corrected TailoredSelection."
        )
        completion = client.beta.chat.completions.parse(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": retry_msg},
            ],
            response_format=TailoredSelection,
        )
        retried = completion.choices[0].message.parsed
        if retried is not None:
            parsed = retried

    # Final defense: truncate per-role bullets to the tiered cap. Under-cap is fine
    # if the pool is genuinely small; over-cap gets clipped to the tier maximum.
    role_target_by_key = {
        (e.company.strip().lower(), e.role.strip().lower()): target_bullets_for_role(i, e)
        for i, e in enumerate(master_cv.experience)
    }
    for sel in parsed.experience_selections:
        key = (sel.company.strip().lower(), sel.role.strip().lower())
        cap = role_target_by_key.get(key, MAX_BULLETS_PER_ROLE)
        if len(sel.selected_bullets) > cap:
            sel.selected_bullets = sel.selected_bullets[:cap]

    # Projects: enforce exclusions + clamp bullets-per-project, then guarantee
    # exactly REQUIRED_PROJECTS entries by padding from the master pool if needed.
    parsed.project_selections = _enforce_project_constraints(master_cv, parsed.project_selections)
    for psel in parsed.project_selections:
        if len(psel.selected_bullets) > MAX_BULLETS_PER_PROJECT:
            psel.selected_bullets = psel.selected_bullets[:MAX_BULLETS_PER_PROJECT]
    return parsed


def _violates_exclusion(name_a: str, name_b: str) -> bool:
    """True if two project names form a mutually-exclusive pair."""
    a, b = name_a.strip().lower(), name_b.strip().lower()
    for fragA, fragB in PROJECT_EXCLUSIONS:
        if (fragA in a and fragB in b) or (fragA in b and fragB in a):
            return True
    return False


def _enforce_project_constraints(
    master_cv: MasterCV,
    selections: List[ProjectSelection],
) -> List[ProjectSelection]:
    """Make the project list match our hard contract:
      1. Drop entries whose `name` doesn't match any master project (LLM hallucination).
      2. Drop entries that violate PROJECT_EXCLUSIONS pairwise — keep the higher
         relevance_score; tie-breaker is original order.
      3. Truncate to MAX_PROJECTS.
      4. Pad with master-pool projects (in master order) up to REQUIRED_PROJECTS,
         skipping anything already selected or any that would re-introduce an
         exclusion violation. Padded projects get a fallback bullet built from
         source_index=0.
    """
    master_by_key = {p.name.strip().lower(): p for p in master_cv.projects}

    # Step 1: filter unknown names
    valid: List[ProjectSelection] = [s for s in selections if s.name.strip().lower() in master_by_key]

    # Step 2: drop exclusion violators. Greedy pass — for each pair, if both
    # present, drop the one with lower relevance_score among its bullets
    # (tie-break: keep first in original list order).
    def _score(sel: ProjectSelection) -> int:
        if not sel.selected_bullets:
            return 0
        return max(b.relevance_score for b in sel.selected_bullets)

    i = 0
    while i < len(valid):
        a = valid[i]
        j = i + 1
        dropped_a = False
        while j < len(valid):
            b = valid[j]
            if _violates_exclusion(a.name, b.name):
                if _score(b) > _score(a):
                    valid.pop(i)
                    dropped_a = True
                    break  # restart outer at same i
                else:
                    valid.pop(j)
                    continue
            j += 1
        if not dropped_a:
            i += 1

    # Step 3: cap at MAX_PROJECTS
    if len(valid) > MAX_PROJECTS:
        valid = valid[:MAX_PROJECTS]

    # Step 4: pad to REQUIRED_PROJECTS from master pool, in master order,
    # skipping already-selected and exclusion-violators.
    if len(valid) < REQUIRED_PROJECTS:
        selected_keys = {s.name.strip().lower() for s in valid}
        for mp in master_cv.projects:
            if len(valid) >= REQUIRED_PROJECTS:
                break
            mk = mp.name.strip().lower()
            if mk in selected_keys:
                continue
            if any(_violates_exclusion(mp.name, s.name) for s in valid):
                continue
            if not mp.bullet_pool:
                continue
            valid.append(
                ProjectSelection(
                    name=mp.name,
                    selected_bullets=[
                        SelectedBullet(
                            source_index=0,
                            compressed_text=mp.bullet_pool[0].text,
                            relevance_score=5,
                        )
                    ],
                )
            )
            selected_keys.add(mk)

    return valid


def apply_selection(master_cv: MasterCV, selection: TailoredSelection) -> MasterCV:
    """Build a new MasterCV with bullet_pools replaced by selector picks. Personal info,
    summary, skills, education pass through unchanged."""

    exp_by_key = {
        (s.company.strip().lower(), s.role.strip().lower()): s
        for s in selection.experience_selections
    }
    proj_by_key = {p.name.strip().lower(): p for p in selection.project_selections}

    new_experience = []
    for e in master_cv.experience:
        key = (e.company.strip().lower(), e.role.strip().lower())
        sel = exp_by_key.get(key)
        if sel is None:
            # selector dropped this role — keep original pool as fallback
            new_experience.append(e)
            continue
        new_bullets = []
        for sb in sel.selected_bullets:
            if 0 <= sb.source_index < len(e.bullet_pool):
                src = e.bullet_pool[sb.source_index]
                new_bullets.append(Bullet(text=sb.compressed_text, tags=src.tags))
        new_experience.append(e.model_copy(update={"bullet_pool": new_bullets}))

    # Projects: only keep those the selector explicitly chose (rest dropped to save page space).
    # Preserve the selector's chosen order so the most JD-relevant projects appear first.
    new_projects = []
    master_by_key = {p.name.strip().lower(): p for p in master_cv.projects}
    for sel in selection.project_selections:
        p = master_by_key.get(sel.name.strip().lower())
        if p is None:
            continue
        new_bullets = []
        for sb in sel.selected_bullets:
            if 0 <= sb.source_index < len(p.bullet_pool):
                src = p.bullet_pool[sb.source_index]
                new_bullets.append(Bullet(text=sb.compressed_text, tags=src.tags))
        new_projects.append(p.model_copy(update={"bullet_pool": new_bullets}))

    return master_cv.model_copy(
        update={"experience": new_experience, "projects": new_projects}
    )
