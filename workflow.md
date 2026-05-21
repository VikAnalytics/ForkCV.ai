# ForkCV — Workflow, Logic & Architecture

This document explains how ForkCV works end-to-end: every pipeline stage, every persistence file, every UI affordance, and the reasoning behind the design choices.

---

## 0. The 30-second pitch

You drop in your resume as a PDF once. From then on, you paste job links (LinkedIn or company career pages), and the tool produces one-page LaTeX-rendered tailored resumes — one per job — named `Vikrant_Indi_<Company>_Resume.pdf`. Bulk mode handles 20-30 jobs in one shot, an applied-jobs tracker persists locally, and a built-in editor lets you grow your master bullet pool over time.

Everything runs locally. The only network calls are:
1. **OpenAI** — JD analysis + bullet selection + skills enrichment (sends JD text + your master CV JSON).
2. **`httpx`** outbound to scrape job postings (LinkedIn `jobs-guest` endpoint + general career sites).
3. **`tectonic`** downloads LaTeX packages on first compile.

No backend service, no auth, no telemetry.

---

## 1. High-level architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          web_local/  (vanilla JS)                         │
│  - Tabbed editor + PDF preview                                            │
│  - Bulk-import bar + sidebar of jobs                                      │
│  - Master-CV drawer + Applied-jobs drawer                                 │
│  - Outreach panel (10 contact cards) per tab                              │
└──────────────────────────────────────────────────────────────────────────┘
                                  │  fetch /api/*
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         local_server.py  (FastAPI)                        │
│                                                                           │
│   /api/tailor  ──►  jd_analyzer  ──►  selector_agent  ──►  apply        │
│                                          │                    │           │
│                                          ▼                    ▼           │
│                            skills_enricher          latex_compiler        │
│                                                          (tectonic)       │
│                          ┌──────────────────────────────────┐             │
│                          │  Side-channel extractors          │             │
│                          │  - sponsorship_extractor (regex)  │             │
│                          │  - language_extractor    (regex)  │             │
│                          │  - report.build_report   (audit)  │             │
│                          └──────────────────────────────────┘             │
│                                                                           │
│   /api/bulk/parse  ──►  jd_scraper (LinkedIn + generic)                  │
│   /api/bulk/zip    ──►  latex_compiler (in-memory zipfile)               │
│   /api/master                                                             │
│   /api/generations  /api/applied   (file-backed CRUD)                    │
│                                                                           │
│   /api/outreach/discover  ──►  contact_provider (Hunter.io)              │
│                            ──►  contact_scorer  (4-signal ranking)       │
│                            ──►  outreach_generator (GPT, anti-AI-tone)   │
│   /api/outreach/reveal    ──►  contact_provider (email-finder)           │
└──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                              data/  (local disk)                          │
│  master_cv_bank.json    — your structured CV                              │
│  generations.json       — every tailored CV ever produced                 │
│  applied_jobs.json      — application tracker                             │
│  outreach.json          — contacts + drafts per generation                │
└──────────────────────────────────────────────────────────────────────────┘
```

Two languages, no framework:
- **Backend:** Python (FastAPI + Pydantic + OpenAI SDK + httpx + BeautifulSoup + trafilatura + Jinja2 + tectonic via subprocess).
- **Frontend:** Vanilla JS, no build step. Three files under `web_local/`.

---

## 2. Data model

All structures are Pydantic models in `src/schemas.py`.

> The diagram above shows the contact provider as "Hunter.io" because that's
> what's wired today. The codebase used to target Apollo.io; Apollo killed
> free-tier API access in 2024, so we switched. The boundary stays the same:
> a single `contact_provider.py` module behind two functions (`search_contacts`,
> `reveal_email`), so swapping to another provider in the future is a one-file
> change.


### MasterCV (the source of truth)

```
MasterCV
 ├── personal_info: { name, email, phone, linkedin, github }
 ├── professional_summary: str       (locked — never rewritten in local server)
 ├── skills: [ { category, items[] } ]
 ├── experience: [ Experience ]
 ├── projects: [ Project ]
 └── education: [ Education ]

Experience          Project
 ├── company         ├── name
 ├── role            ├── location / link
 ├── location        └── bullet_pool: [ Bullet ]
 ├── date
 └── bullet_pool: [ Bullet ]

Bullet  =  { text, tags[] }
```

The key idea: each role/project holds a **bullet pool** of 6-12 bullets, not 3. The selector picks the 3 most JD-relevant ones at generate time. A bullet that doesn't fit today's JD stays available for tomorrow's.

### JDAnalysis (the structured read of a JD)

```
JDAnalysis
 ├── role_title
 ├── primary_tech_stack[]    — concrete tools (SQL, dbt, Snowflake)
 ├── core_impact_areas[]     — outcomes (data modeling, stakeholder reporting)
 ├── must_have_keywords[]    — ATS-critical phrases to surface verbatim
 └── domain                  — industry context (fintech, insurance, ...)
```

Produced by `jd_analyzer.py`, consumed by `selector_agent.py` and `report.py`.

### TailoredSelection (what the selector returns)

```
TailoredSelection
 ├── jd_analysis: JDAnalysis
 ├── experience_selections: [ RoleSelection ]
 └── project_selections:    [ ProjectSelection ]

RoleSelection
 ├── company, role            (must match source verbatim)
 └── selected_bullets: [ SelectedBullet ]

SelectedBullet
 ├── source_index             — 0-based index into bullet_pool
 ├── compressed_text          — final 160-230 char bullet
 └── relevance_score          — 1-10
```

### PipelineReport (audit trail returned to the UI)

```
PipelineReport
 ├── analysis: JDAnalysis
 ├── placements: [ KeywordPlacement ]   — for each JD keyword, where it landed
 ├── skill_additions[]                  — "Cloud: dbt" style strings
 ├── sponsorship: SponsorshipInfo       — { status, evidence[] }
 └── languages:  [ LanguageConstraint ] — [ { language, level, required, evidence } ]
```

### GenerationRecord (history)

```
GenerationRecord
 ├── id (uuid hex)
 ├── company, jd, source_url
 ├── cv: MasterCV               (the tailored output, full blob)
 ├── report: PipelineReport
 ├── pdf_filename
 └── created_at (ISO-8601 UTC)
```

### AppliedRecord (tracker)

```
AppliedRecord
 ├── id (uuid hex)
 ├── company, job_title, job_link
 ├── applied_at (ISO date)
 ├── status   — applied | assessment | interview | offer | rejected | withdrew | ghosted
 ├── notes
 └── generation_id (FK into generations)
```

### Outreach records (per-generation)

```
ContactCandidate              ScoredContact
 ├── name                      ├── contact: ContactCandidate
 ├── title, headline           ├── score (0-100)
 ├── linkedin_url              ├── score_breakdown: { signal: points }
 ├── email + email_status      ├── tenure_months
 ├── organization_name         ├── category — recruiter | hiring_manager
 ├── departments[]             │              | team_ic | other
 ├── seniority                 └── shared_signals[] — readable bits for the
 ├── employment_history[]                              message generator
 └── education[]

OutreachDraft                 OutreachContact
 ├── linkedin_note (<= 280)    ├── scored: ScoredContact
 ├── email_subject (<= 60)     └── draft:  OutreachDraft
 └── email_body

OutreachRecord  (one per generation_id, persisted in data/outreach.json)
 ├── generation_id
 ├── company
 ├── role_title
 ├── created_at
 └── contacts: [ OutreachContact ]
```

---

## 3. Pipeline stages in detail

### 3.1 PDF ingestion (`src/pdf_ingestion.py`, run once via `main.py ingest`)

1. `pymupdf` extracts text from each page.
2. The full text is sent to GPT with the `MasterCV` schema as the response format (`client.beta.chat.completions.parse`).
3. The model returns a structured `MasterCV` JSON.
4. Saved to `data/master_cv_bank.json`.
5. The PDF itself is then orphaned — never read again. The structured JSON is the master.

**Why one-shot:** the PDF is messy (columns, OCR noise, dates as headers vs. footers). One LLM pass into a Pydantic schema is more robust than rule-based parsing, and it only runs once per career update.

### 3.2 JD analyzer (`src/jd_analyzer.py`)

- Single GPT call. Input: raw JD text. Output: `JDAnalysis` (structured, schema-validated).
- The model is prompted to bucket signals into `primary_tech_stack` (literal tools), `core_impact_areas` (outcomes), `must_have_keywords` (ATS critical), and `domain`. This bucketing matters downstream — the selector treats each bucket differently.

### 3.3 Selector agent (`src/selector_agent.py`) — the heart

The selector is the highest-leverage step. It takes the master bullet pool + the JD analysis and produces a `TailoredSelection`: for each role and project, which bullets to keep and how to compress/paraphrase them.

**Hard contracts** (enforced by validation + retry + post-hoc clamping):
- **Exactly 3 bullets per experience role** (or all of them if the pool has < 3).
- **Exactly 3 projects**, picked by JD relevance.
- Each project gets exactly **1 bullet**, length 220-320 chars, written like an engineer briefing a senior engineer.
- Each experience bullet is 160-230 chars and **must contain a concrete quantifier** preserved verbatim from source.
- `source_index` must be a valid index into the source pool (catches hallucination).
- `company` / `role` / project `name` must match source verbatim (catches name drift).

**Prompt rules** (`SYSTEM_PROMPT` in `selector_agent.py`):

1. **Length-and-substance density** — the budget exists to surface MORE substance (extra tool, extra metric), not to pad with fluff. The prompt enumerates a blocklist of "AI fluff" phrases ("ensuring reliability", "driving operational excellence", etc.) and a substance test: "what verifiable fact does this clause add?"
2. **Logical coherence** — the #1 priority rule. A keyword can be injected only if it's plausibly true of the *specific* work. Concrete anti-examples baked into the prompt: a Twitter bot tweeting sports stats cannot acquire "regulatory compliance"; a sentiment-analysis tool over public news cannot acquire "GDPR".
3. **Keyword injection — Semantic-equivalence paraphrase (REQUIRED)** — a JD keyword often describes the same concrete work as a source bullet using different words. In that case the selector MUST rewrite the lead verb or noun phrase to surface the JD's wording. Worked examples in the prompt:
   - `"Engineered an automated SQL migration pipeline using Gemini..."` + JD wants `data pipeline development` → `"Engineered data pipeline for automated SQL migrations using Gemini..."`
   - `"Built an ETL job that loads marketing-event data into Snowflake"` + JD wants `data ingestion` → `"Built data ingestion ETL loading marketing-event data into Snowflake"`
   - `"Refactored 12 stored procedures into modular dbt models"` + JD wants `data modeling` → `"Refactored 12 stored procedures into modular dbt data models"`

   Decision algorithm baked into the prompt:
   > Step 1: Ask "Does the source work, described to a generalist, fit under this JD keyword?" If yes → semantic match → proceed. If no → thematic stretch → omit.
   > Step 2: Ask "Can I swap one noun phrase or lead verb to use the JD's exact wording, without inventing any new fact?" If yes → REWRITE. If no → use the keyword in skills or a different bullet instead.

   What's forbidden during paraphrase: adding new tools, new metrics, new scope, or stacking multiple keywords into one bullet.
4. **Quantification** — every bullet must contain at least one concrete quantifier; source metrics preserved verbatim.
5. **Project mutual exclusions** — declared in `PROJECT_EXCLUSIONS`. Pairs of projects that overlap thematically (e.g. "Silent Degradation Detection" and "Unified Network Health Monitor" both cover telecom anomaly detection) — if both end up selected, the lower-scoring one is dropped and a different project fills the slot. Code path: `_enforce_project_constraints` in `selector_agent.py`.

**Defense in depth** — even if the LLM violates a contract, post-hoc clamping kicks in:
- If a role has > 3 bullets, truncate to 3.
- If the project list violates exclusions or has < 3, drop violators and pad from the remaining master pool.
- If a role is missing entirely, fall back to its master bullets unchanged.

### 3.4 Skills enricher (`src/skills_enricher.py`)

After bullet selection runs, a second GPT call takes the master skills sections and the JDAnalysis and decides which JD-relevant skills to append to existing categories. Two truth gates:
1. The candidate's bullet pool must show direct evidence of using the skill, **OR**
2. The skill is universally assumed for the role (e.g. Git for software engineers).

The model also prefers broader concepts ("data modeling", "dashboarding") over specific tools when evidence is weak, to avoid stacking unfamiliar buzzwords.

### 3.5 LaTeX render + compile (`src/latex_compiler.py`)

1. `render_tex(cv, templates_dir, template_name="resume.tex.j2")` — Jinja2 renders the tailored `MasterCV` into a `.tex` source. The Jinja env is configured with LaTeX-safe delimiters (`<% %>` and `<<>>`) so `{...}` in LaTeX doesn't conflict.
2. `tex_escape()` runs aggressive Unicode-to-LaTeX normalization: em/en dashes → `--`/`---`, curly quotes → straight, NBSP → `~`, ellipsis, etc. This was added after T1 fontenc + lmodern silently dropped em-dashes in earlier output.
3. `_LIGATURE_BREAKER` injects `{}` between `f` and `fi/ff/fl` ligatures where they hurt readability in the resume font.
4. `compile_pdf(tex_path, out_dir)` shells out to `tectonic --keep-logs --outdir <dir> <tex>`. Tectonic auto-downloads packages on first run, then caches them.

### 3.6 Sponsorship extractor (`src/sponsorship_extractor.py`)

Pure regex. **No LLM call** — runs in milliseconds on the JD text.

- Sentence split (on `.!?` + newlines, with bullet markers stripped).
- For each sentence: if it doesn't mention `visa | sponsor | H-1B | work authorization | immigration | green card | OPT | EAD`, skip.
- Otherwise classify:
  - **`not_available`** if a negative pattern matches (`no sponsorship`, `unable to sponsor`, `do not offer sponsorship`, `without (current or future) sponsorship`, `must be authorized to work in the US without sponsorship`, etc.).
  - **`available`** if a positive pattern matches (`offer/provide sponsorship`, `sponsorship available`, `willing/able to sponsor`, `we sponsor`, `H1B available`, etc.).
  - **`mentioned`** otherwise (e.g. "you must have work authorization in the US" — stance unclear, surfaced for user to read).
- **Negative wins on conflict** — "we sponsor visas. However, this role does not offer sponsorship." → `not_available`.
- Returns top ≤4 evidence sentences, deduplicated, priority order `not_available > available > mentioned`.

### 3.7 Language extractor (`src/language_extractor.py`)

Same shape as sponsorship: pure regex over JD sentences.

- 40+ languages recognized (English, Spanish, French, German, Mandarin, Japanese, Hindi, …).
- Levels: `native | fluent | professional | conversational | basic | bilingual`. CEFR (C1/C2/B1/B2/A1/A2), JLPT (N1-N5), HSK (5-6) codes all mapped.
- Per-sentence required-vs-preferred default: `preferred` cue beats `required` cue when both appear sentence-wide.
- **Per-language inline parens override** — `Languages: English (required), Mandarin (preferred), Spanish (nice to have)` parses each language separately. Inline `(fluent)` / `(conversational)` capture levels.
- **Closest-level resolution** — when a sentence mentions multiple languages, each language gets the level marker closest to it. `Native-level Japanese (JLPT N1); business-level English.` → Japanese=`native`, English=`professional` (no cross-leak).
- **Noise gate** — a sentence containing a language word with NO proficiency cue is rejected. `"We have an English-language Slack channel"` produces zero output.

Level regex ordering matters: longer alternatives first (`native[-\s]level` before bare `native`) so the regex engine doesn't match the shorter prefix and miss the full phrase.

### 3.8 JD scraper (`src/jd_scraper.py`)

`scrape(url, li_cookie=None) → ScrapedJob{url, company, jd, source, error}`.

**LinkedIn path:**
1. Extract the numeric `job_id` from the URL (handles `/jobs/view/<id>`, `currentJobId=` query param, and bare `/<id>/` fallback).
2. Try the public `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<id>` endpoint — unauthenticated, returns clean HTML. Parse `.topcard__org-name-link` for company and `.show-more-less-html__markup` for the JD HTML.
3. If guest fails (rare), fall back to authenticated `/jobs/view/<id>/` using the user-supplied `li_at` cookie. Parse JSON-LD `JobPosting` for `hiringOrganization.name` + `description`.
4. If both paths fail, return the error.

**Generic path:**
1. `httpx.GET` the URL with a desktop User-Agent.
2. Try JSON-LD `JobPosting` first — Greenhouse, Lever, Ashby, and Workable all embed it. Pull `hiringOrganization.name` + `description`.
3. If no JSON-LD, fall back to `trafilatura.extract(html)` for main-content extraction.
4. Company inference order: JSON-LD `hiringOrganization.name` → `og:site_name` meta → URL heuristics (`careers.stripe.com` → "Stripe", `jobs.lever.co/anthropic/...` → "Anthropic", known ATS host detection).

`_strip_html` normalizes the result: `<br>` → `\n`, `<li>` → `• <text>\n`, collapse multiple blank lines.

### 3.9 Report builder (`src/report.py`)

For each JD keyword (across all buckets), scan the *tailored* CV — summary, skills, experience bullets, project bullets — and record where (if anywhere) the keyword appears. Also diff master vs. tailored skills to surface "skill additions" that the enricher added.

The placements feed the **Keyword placements** UI block: every JD keyword gets a chip (colored by bucket) showing whether it landed in the CV and where. Unmatched keywords are toggled with a checkbox.

### 3.10 Contact provider (`src/contact_provider.py`)

Hunter.io-backed. Used to be Apollo; Apollo killed free-tier API access in
2024 so the module was rewritten while keeping the same two-function
abstraction (`search_contacts`, `reveal_email`).

**`search_contacts(company, role_title, role_keywords)`**:
1. Resolve company name → primary domain via **Clearbit autocomplete**
   (`https://autocomplete.clearbit.com/v1/companies/suggest?query=…`). This is
   a public, no-auth endpoint — free, no quota.
2. Map `role_title` → Hunter's fixed department taxonomy (engineering,
   design, sales, hr, finance, operations, executive, …). Always includes
   `hr` so recruiters surface alongside the role's own department.
3. `GET /v2/domain-search?domain=…&department=…&type=personal&limit=10`.
   Free tier caps limit at 10 results per call — we clamp defensively.
4. Each result has `first_name`, `last_name`, `position`, `linkedin`,
   `email`, `verification.status`, `department`, `seniority`. Map to
   `ContactCandidate`. One malformed entry doesn't sink the list (per-row
   try/except).

**`reveal_email(first_name, last_name, organization_name, ...)`**:
1. Resolve org name → domain via Clearbit if needed.
2. `GET /v2/email-finder?domain=…&first_name=…&last_name=…`.
3. Returns `{email, email_status}`. Counts against your Hunter monthly quota.

**Free-tier guardrails**:
- Hunter free: 25 searches/mo + 50 verifications/mo. Each `Find contacts`
  click ≈ 1 search. Each `Find email` per-card click ≈ 1 search.
- Clearbit autocomplete is unmetered.
- `ContactProviderAuthError` raised on missing/bad key; `ApolloAuthError`
  kept as an alias so older imports keep working.

### 3.11 Contact scorer (`src/contact_scorer.py`)

Pure Python, no LLM. 100-point ranking system over `ContactCandidate`s:

| Signal | Max points | How |
|---|---|---|
| Title relevance | 40 | recruiter=40, hiring_manager=35, team_ic=25, other=8 |
| Team / department match | 25 | JD `role_title + core_impact_areas + domain` tokens appearing in contact title/headline/dept |
| Shared school | 12 | Master CV `education[].institution` ≡ candidate `education[].school` (normalized) |
| Shared past employer | 8 | Master CV `experience[].company` ≡ candidate `employment_history[].organization_name` |
| Tenure sweet spot | 15 | Current-role start date 6mo-3yr ago = 15; 3-6mo or 3-5yr = 8; else 0 |

**Title categorization**: `_categorize()` looks for recruiter patterns first
(`recruiter | talent acquisition | sourcer | …`). Then hiring-manager =
title contains a role-token AND a leadership token (`manager | head of |
director | vp | chief | principal | staff`). Then team_ic = title contains
any role-token. Else `other`.

**Hunter caveat**: Hunter's API doesn't expose `employment_history` or
`education`, so the shared-school / shared-employer / tenure signals all
return 0 in the current setup. The scorer still ranks usefully on title
relevance + team match (max 65 of 100). When we add a richer provider in
the future, those signals light up automatically.

**Shared signals**: the scorer emits human-readable strings the outreach
generator weaves into messages. e.g. `["works on data", "shared past
employer: Bayer"]`.

### 3.12 Outreach generator (`src/outreach_generator.py`)

GPT call per contact (concurrency 4 server-side). Returns `OutreachDraft`
with `linkedin_note` (≤ 280 chars), `email_subject` (≤ 60 chars), and
`email_body`.

**Anti-AI-tone prompt** — the hardest piece. The system prompt enumerates
16 banned phrases ("I hope this finds you well", "I came across", "I'm
thrilled / excited / passionate", "Looking forward to hearing from you",
"Thank you for your time and consideration", etc.) plus banned punctuation
(em-dashes, semicolons) plus banned words ("synergy", "leverage" as a verb,
"passionate"). Every draft must include:

1. **One concrete recipient detail** — drawn from title, tenure, past
   company, school, or department. Not "your impressive background".
2. **One concrete sender detail** — drawn from the master CV's top bullets
   or the target role. Not "I'm passionate about data".

**Structural rules** baked into the prompt:
- LinkedIn note: 1-3 sentences, ≤ 280 chars total. No "Hi" salutation
  required, jump straight in.
- Email: subject ≤ 60 chars, body 4-6 short sentences, no bullet points
  (bullets read templated), sign-off is just the sender's first name.
- Mix sentence lengths. Use contractions. Short fragments OK.
- Closing ask is tiny and specific (15 min chat / referral / tech-stack
  advice). Not "any guidance you can share".

**Post-processing scrub** (`_scrub` in the same module): even if the model
slips, a regex strips banned phrases and replaces em-dashes with commas.
`_ensure_length` enforces the char caps without chopping mid-word.

**Fallback**: if the OpenAI structured-output call returns `None`, we emit
a hand-written minimal template so the contact card still has *some* draft
the user can edit. In `discover_outreach` the generation step is also
wrapped in try/except so one failing draft can't take down the whole batch.

### 3.13 Bulk parse + zip endpoints

- **`/api/bulk/parse`** — accepts `{ urls[], li_cookie? }`, runs `jd_scraper.scrape` per URL in an asyncio thread pool with a 5-wide semaphore. Returns per-URL outcomes so one bad link doesn't fail the batch.
- **`/api/bulk/zip`** — accepts `[{ cv, company }]` (the current edited state of each open job), renders each PDF in a 3-wide semaphore (pdflatex is CPU-heavy), and streams back a single ZIP. Filename collisions are de-duped by suffixing `_2`, `_3`.

---

## 4. End-to-end workflows

### 4.1 First-time setup

```
PDF resume
   │
   ▼  main.py ingest
pdf_ingestion (GPT one-shot)
   │
   ▼
data/master_cv_bank.json     ← from here on, the master is JSON
```

### 4.2 Single-job tailoring

```
1. User pastes company + JD into a tab, clicks Generate
                          │
                          ▼  POST /api/tailor
2. jd_analyzer (GPT) → JDAnalysis
3. selector_agent (GPT) → TailoredSelection
4. apply_selection → optimized MasterCV
5. skills_enricher (GPT) → enriched skills appended
6. latex_compiler → resume.tex → tectonic → PDF bytes
7. build_report → placements + skill_additions
8. sponsorship_extractor (regex) → SponsorshipInfo
9. language_extractor   (regex) → [ LanguageConstraint ]
10. Auto-append GenerationRecord to data/generations.json
                          │
                          ▼
Response: { cv, report, pdf_base64, generation_id }
                          │
                          ▼
UI hydrates editor + PDF preview + JD-signals panel
```

### 4.3 Bulk-import flow

```
1. User pastes 30 URLs into the bulk textarea, clicks "Parse links"
                          │
                          ▼  POST /api/bulk/parse
2. jd_scraper × 30 (concurrency 5)
                          │
                          ▼
Sidebar renders 30 rows (1st, 2nd, 3rd…), status dots, clickable URLs

3. User clicks "Generate all"
                          │
                          ▼  POST /api/tailor × 30 (frontend semaphore = 5)
4. Each call runs the single-job pipeline end-to-end + persists to history
                          │
                          ▼
Sidebar rows update to ✓ (green) as each completes

5. Click any row → opens as a tab with editor + preview
   Edit → "Apply changes" → re-render PDF → state syncs back to sidebar

6. "Download all (ZIP)"  → POST /api/bulk/zip
                          → streaming ZIP of all current PDFs
```

### 4.4 Master CV editor

```
1. Click "Edit master CV" in topbar
2. Drawer renders all roles, all bullets, all skills, all projects
3. Inline edits update a draft (deep-cloned from masterCV global)
4. Add bullet / add role / add project / delete role buttons
5. "Save"
                          │
                          ▼  POST /api/master
6. Pydantic validates, atomic write (tmpfile → rename) to master_cv_bank.json
7. masterCV global updated; next Generate uses new bullets immediately
```

### 4.5 Outreach flow

```
1. User clicks "Find contacts" inside the tab's Outreach panel
                          │
                          ▼  POST /api/outreach/discover {generation_id}
2. Load parent generation from generations.json (company + jd_analysis + master_cv)
3. Check outreach.json cache by generation_id — return cached if present (no LLM call)
4. contact_provider.search_contacts(company, role_title, role_keywords)
     - Clearbit autocomplete: company name → domain
     - Hunter /v2/domain-search filtered by departments inferred from role_title
     - Returns up to 10 ContactCandidates with name + position + LinkedIn + email
5. contact_scorer.rank_candidates(candidates, master, role_title, jd, top_k=10)
     - 100-point score per candidate (title, team, school, employer, tenure)
     - Sort desc; emit shared_signals[] for the generator
6. outreach_generator.generate_outreach × top-10 (asyncio.Semaphore(4))
     - Per-contact GPT call with anti-AI-tone system prompt
     - Post-LLM scrubber strips banned phrases / em-dashes
     - On per-contact failure: fall back to a stub draft, batch survives
7. Persist OutreachRecord to outreach.json keyed by generation_id
8. Return record → UI renders 10 cards
                          │
                          ▼
   Click "Find email" on a card that has no email
                          ▼  POST /api/outreach/reveal {generation_id, contact_index}
   Hunter /v2/email-finder for that specific person
   Update the cached outreach.json in place, return updated contact
                          ▼
   Card's email pill populates, mailto button activates
```

### 4.6 Applied-jobs tracker

```
After Generate:
  Tab shows "Mark applied" button next to Save PDF
                          │
                          ▼  POST /api/applied
  AppliedRecord persisted with generation_id back-pointer

Topbar "Applied jobs (N)" → drawer with all applications
  - Status dropdown (color-coded)
  - Editable date, notes (autosave 400ms debounce)
  - Delete button
  All edits hit PATCH /api/applied/{id}
```

### 4.7 Refresh resilience

On page load (`init()` in `app.js`):
1. `GET /api/master` → populate the `masterCV` global.
2. `GET /api/generations` → hydrate the sidebar with all past generations (lightweight summaries — no CV/report blob).
3. `GET /api/applied` → populate the applied-jobs cache + topbar counter.
4. When opening a tab from history, `GET /api/outreach/{id}` loads any cached outreach drafts for that generation (silent — no LLM calls, no Hunter quota burn).

Sidebar rows for past generations are **lazy-loaded**: clicking one fires `GET /api/generations/:id` for the full record + `POST /api/render` to get a fresh PDF (PDFs aren't stored — only CV JSON is, since re-render is sub-second).

---

## 5. Persistence model

Everything lives under `data/` as JSON. Four files:

| File | What | Written by | Read by |
|---|---|---|---|
| `master_cv_bank.json` | Your master CV bank | `main.py ingest`, `POST /api/master` | `GET /api/master`, `/api/tailor`, master editor |
| `generations.json` | Every tailored CV ever produced | `/api/tailor` (auto-append) | `/api/generations` (list + get) |
| `applied_jobs.json` | Application tracker | `/api/applied` (POST/PATCH/DELETE) | `/api/applied` (list), `/api/generations` (join) |
| `outreach.json` | Contacts + drafts per generation | `/api/outreach/discover`, `/api/outreach/reveal` | `/api/outreach/{id}`, drawer hydration |

**Atomic writes**: every file write goes through `_save_list` (or `save_master`) which writes to `<file>.tmp` then `Path.replace()` — guarantees the file is either old-and-valid or new-and-valid, never half-written.

**Concurrency**: a single `threading.Lock` (`_store_lock` in `local_server.py`) wraps every read-modify-write on the JSON files. Single-user local server makes contention near-zero, but the lock protects against the rare case of two browser tabs racing.

**No PDFs on disk**: generated PDFs only live in memory + the user's downloads folder. Regenerating from CV JSON via `/api/render` is fast (~1s), so caching the bytes isn't worth the disk cost.

---

## 6. API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness ping |
| GET | `/api/master` | Read master CV |
| POST | `/api/master` | Save master CV (atomic overwrite) |
| POST | `/api/tailor` | Generate tailored CV + PDF for one JD; auto-saves to history |
| POST | `/api/render` | Re-render an edited CV to PDF (no LLM calls) |
| POST | `/api/bulk/parse` | Scrape N URLs → company + JD per URL |
| POST | `/api/bulk/zip` | Render N CVs → streaming ZIP |
| GET | `/api/generations` | List past generation summaries |
| GET | `/api/generations/{id}` | Full record (cv, report, jd, source_url) |
| DELETE | `/api/generations/{id}` | Remove |
| GET | `/api/applied` | List applications |
| POST | `/api/applied` | Create application |
| PATCH | `/api/applied/{id}` | Update fields (status, date, notes, ...) |
| DELETE | `/api/applied/{id}` | Remove |
| POST | `/api/outreach/discover` | Hunter search + scoring + GPT draft generation for a generation |
| GET | `/api/outreach/{generation_id}` | Cached outreach record or `null` |
| POST | `/api/outreach/reveal` | Hunter email-finder for a single cached contact; updates `outreach.json` in place |
| DELETE | `/api/outreach/{generation_id}` | Remove cached outreach |

All bodies are JSON. All responses are Pydantic-validated against the schemas in `src/schemas.py`.

---

## 7. Frontend architecture

Three files under `web_local/`. No bundler, no framework, no transpilation.

**`index.html`** — declarative layout:
- Topbar (title, "Applied jobs (N)" button, "Edit master CV" button, status pill).
- Bulkbar (collapsible details/summary with the paste-links textarea + parse/generate/download buttons).
- Workspace = sidebar (`#bulkSidebar`) + workspace-main (tabbar + `#panels`).
- Master drawer + Applied drawer + backdrops (hidden by default).
- A `<template>` element holds the per-tab panel HTML, cloned on tab creation.

**`app.js`** — all logic. Key globals:
- `masterCV` — current master CV (re-fetched after master-drawer save).
- `tabs: Map<tabId, state>` — one entry per open tab. `state` holds `{company, jd, cv, report, pdfUrl, pdfFilename, bulkUrl, generationId, appliedId, status}`.
- `bulkJobs: Map<key, job>` — sidebar rows. Key is `url` if present else `gen:<id>`. Each `job` has `{url, genId, company, jd, cv, pdfBase64, pdfFilename, report, status, error, createdAt, applied}`.
- `appliedRecords: AppliedRecord[]` — populated by `refreshAppliedCount()`.
- `masterDraft` — deep-clone of `masterCV` while the master drawer is open.

Tabs ↔ bulk rows are linked by `state.bulkUrl` / `state.generationId`. When a user edits a bullet in a tab and clicks Apply, `syncTabToBulk()` propagates the latest `cv` + `pdfBase64` to the sidebar row so the ZIP download and re-open both see the latest version.

**`styles.css`** — hand-rolled CSS variables, no Tailwind. Color tokens at `:root`:
```
--bg, --panel, --border, --text, --muted, --accent, --success, --warn, --danger
```

---

## 8. Concurrency model

| Component | Pattern | Why |
|---|---|---|
| `/api/bulk/parse` | `asyncio.Semaphore(5)` over `loop.run_in_executor(None, scrape, ...)` | 5 concurrent HTTP fetches; respects external server rate limits |
| Frontend `generateAll()` | JS worker pool of size 5 | Caps simultaneous `/api/tailor` calls; respects OpenAI rate limits |
| `/api/bulk/zip` | `asyncio.Semaphore(3)` over `latex_compiler` | tectonic is CPU-heavy; 3 parallel compiles saturates a typical laptop without thrashing |
| `/api/outreach/discover` | `asyncio.Semaphore(4)` over GPT draft generation | 4 concurrent OpenAI calls for the 10 contact drafts |
| File writes | `threading.Lock` + tmpfile + rename | Atomicity for JSON store |

---

## 9. Configuration knobs

| Constant | File | Default | Meaning |
|---|---|---|---|
| `MODEL` | `selector_agent.py`, `jd_analyzer.py`, etc. | `gpt-4o-mini` | OpenAI model used for analysis + selection |
| `MAX_BULLETS_PER_ROLE` | `selector_agent.py` | `3` | Hard cap; clamped post-LLM |
| `MAX_PROJECTS` / `REQUIRED_PROJECTS` | `selector_agent.py` | `3` / `3` | Always exactly 3 projects |
| `MAX_BULLETS_PER_PROJECT` | `selector_agent.py` | `1` | Projects get one beefy bullet, not three thin ones |
| `TARGET_BULLET_CHARS_MIN/MAX` | `selector_agent.py` | `160 / 230` | Per-experience-bullet length budget |
| `TARGET_PROJECT_BULLET_CHARS_MIN/MAX` | `selector_agent.py` | `220 / 320` | Projects allowed to be longer |
| `PROJECT_EXCLUSIONS` | `selector_agent.py` | `[("silent degradation", "unified network")]` | Mutually exclusive project pairs |
| `BULK_CONCURRENCY` | `app.js` | `5` | Frontend tailor-call concurrency |
| Bulk-parse semaphore | `local_server.py` | `5` | Server-side scrape concurrency |
| Bulk-zip semaphore | `local_server.py` | `3` | Server-side latex-compile concurrency |
| `LI_COOKIE_KEY` | `app.js` | `'mrb.li_at'` | localStorage key for the LinkedIn cookie |
| `APPLIED_STATUSES` | `app.js` | `applied/assessment/interview/offer/rejected/withdrew/ghosted` | Status dropdown options |
| `per_page` (Hunter `domain-search` limit) | `contact_provider.py` → `search_contacts` | `10` | Hunter free tier caps at 10; clamped defensively |
| Outreach draft concurrency | `local_server.py` → `discover_outreach` | `4` | Parallel GPT calls during outreach discovery |
| `BANNED_PHRASES` | `outreach_generator.py` | 16 phrases | Banned in outreach drafts; scrubber strips slip-throughs |
| `MODEL` | `outreach_generator.py` | `gpt-4o-mini` | OpenAI model for outreach drafts |

---

## 10. Design decisions, in one place

- **Bullet pool, not bullets.** A resume bullet should not be locked to one JD's flavor. Keeping a 6-12 bullet pool per role and letting the selector choose dynamically is the entire reason the rest of this exists.
- **Selector paraphrases, never invents.** The hardest prompt-engineering rule: distinguish *semantic equivalence* (same work, different words — paraphrase aggressively) from *thematic stretch* (different work, vaguely related — omit). The decision algorithm + worked examples in the prompt encode this.
- **Regex for sponsorship + languages, not LLM.** These signals are surface-level and the JD wording is conventional. A regex is faster, cheaper, deterministic, and easier to audit than another GPT call.
- **One LLM call per pipeline stage.** Ingest (×1), JD analyze (×1), selector (×1, with one retry if validation fails), skills enricher (×1). The retry is gated on validation failure, not "low confidence". Cost per resume: a few cents.
- **No PDFs persisted.** PDF bytes are big, easy to regenerate, and never the source of truth. Storing only CV JSON keeps `data/` tiny and makes restore trivial.
- **Vanilla JS frontend.** No build step means clone-and-run. The app is small enough that a framework would be more code than it saves.
- **Local-only by default.** Productionization (Supabase auth, Stripe paywall, Fly.io deploy) is intentionally NOT shipped in the public repo. The local server has no users, no quota, no auth — just a tool.
- **Atomic file writes everywhere.** Every JSON store write goes through tmpfile + rename. Browser tabs can race, the user can Ctrl+C the server mid-write, the disk can fail — the file is always either old-and-valid or new-and-valid.
- **Contact provider behind a thin abstraction.** `contact_provider.py` exposes just two functions: `search_contacts` + `reveal_email`. Apollo died, we swapped to Hunter, nothing else in the system noticed. Next swap (if Hunter changes their free policy) is the same shape.
- **Anti-AI-tone is a defense in depth.** A strong system prompt + a deterministic post-LLM scrubber. The prompt bans the phrases; the scrubber strips them anyway if the model slips. Belt and suspenders.
- **Outreach drafts are advisory, not auto-sent.** ForkCV never sends a message. We open `mailto:` links and provide copy buttons. The human is always the final step. This keeps us compliant with CAN-SPAM, GDPR, and LinkedIn's automated-DM ToS.

---

## 11. Failure modes & how they're handled

| Failure | Handler |
|---|---|
| Selector returns < or > 3 bullets per role | Validation → retry with explicit nudge → post-hoc clamp |
| Selector returns project not in master | `_enforce_project_constraints` drops it + pads from master pool |
| Selector hallucinates `source_index` out of range | `apply_selection` checks bounds; out-of-range bullets are dropped silently (the role keeps however many valid ones came back) |
| LLM call fails / refuses | RuntimeError surfaced to the API caller; tab shows error log line |
| LinkedIn guest endpoint returns 404 / 451 | Falls back to authenticated `jobs/view` if `li_at` cookie supplied; else returns per-row error so other URLs in the batch still succeed |
| trafilatura returns empty (heavily-JS career page) | Per-row error returned; user can paste JD text manually instead |
| tectonic not installed | RuntimeError with `brew install tectonic` hint |
| Bullet contains an em-dash | `tex_escape` normalizes to `---` before render |
| Two concurrent saves to `master_cv_bank.json` | `_store_lock` serializes; tmpfile+rename guarantees atomicity |
| Browser refresh mid-bulk-generate | Completed generations are persisted to `generations.json` by then; sidebar restores them. In-flight ones are simply lost — restart the bulk run |
| `HUNTER_API_KEY` missing | `ContactProviderAuthError` → 401 from `/api/outreach/discover` with a message pointing to the signup URL |
| Hunter free tier exhausted | `429` from Hunter → wrapped to 502 with rate-limit hint; the outreach panel surfaces it inline |
| Hunter returns no contacts | Empty `OutreachRecord` persisted so the panel shows "No contacts returned" without re-querying |
| One outreach draft fails mid-batch | `gen_one` try/except substitutes a hand-written stub draft; the other 9 still go out |
| Clearbit autocomplete fails (offline) | `search_contacts` falls back to passing `company=…` to Hunter directly instead of `domain=…` |
| `generate_outreach()` keyword-only arg passed positionally | Lambda wrapper in `discover_outreach` calls `generate_outreach(sc, master, company, role_title, jd=jd_analysis)` |

---

## 12. Stage timing (typical, single job)

| Stage | Wall clock |
|---|---|
| JD analyzer | 2-4s |
| Selector | 8-15s (largest single cost) |
| Skills enricher | 2-4s |
| Sponsorship + language extractors | < 50ms combined |
| LaTeX render + tectonic compile | 0.8-1.5s |
| Report builder | < 100ms |
| **Total per job** | **~25-30s** |

For bulk-30 at concurrency 5, total wall clock is ~3-5 minutes plus scrape time.

### Outreach pipeline (per "Find contacts" click)

| Stage | Wall clock |
|---|---|
| Clearbit domain resolution | 0.3-0.8s |
| Hunter `/v2/domain-search` | 1-3s |
| Scoring (CPU only) | < 50ms |
| GPT outreach drafts × 10 (concurrency 4) | 8-15s |
| Persist `outreach.json` | < 50ms |
| **Total** | **~12-20s** |

`/api/outreach/reveal` is a single Hunter `email-finder` call: 1-3s.

