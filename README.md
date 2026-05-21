# ForkCV

Local AI-powered resume tailoring. Drop in your master CV (PDF), paste a job link or JD, get a one-page LaTeX-rendered PDF tailored to that posting. Bulk mode generates N versions for N postings at once.

## What it does

- **Ingests** your existing resume PDF into a structured "bullet pool" per role.
- **Analyzes** a job description for primary tech stack, must-have keywords, domain, sponsorship stance, and language constraints.
- **Selects + lightly rewrites** 3 bullets per role (and 3 projects) that maximize JD alignment. The selector is allowed to paraphrase the lead verb or noun phrase to surface JD keywords when the underlying fact is identical (e.g. "SQL migration pipeline" → "data pipeline development"), but cannot invent new tools, metrics, or scope.
- **Renders** to a one-page LaTeX resume PDF via [tectonic](https://tectonic-typesetting.github.io/).
- **Bulk mode**: paste 20-30 job links (LinkedIn or company URLs), generates one tailored PDF per posting, named `Vikrant_Indi_<Company>_Resume.pdf`. Download individually or as a ZIP.
- **Applied-jobs tracker**: mark applications as you submit, edit status (applied / assessment / interview / offer / rejected / withdrew / ghosted), notes, date. Persists across refreshes.
- **Master-CV editor**: edit bullets, skills, roles, and projects from the browser. Saves back to `data/master_cv_bank.json`.
- **Outreach (optional)**: for each generated resume, find 10 ranked contacts at the company (recruiters / hiring managers / team ICs) via the Hunter.io API, with per-contact LinkedIn note + email draft. Hunter free tier covers ~25 searches/mo. Set `HUNTER_API_KEY` in `.env.local` to enable.

All state (master CV, generation history, applied jobs) lives on your local disk under `data/`. Nothing leaves your machine except the JD + master CV sent to the OpenAI API for analysis and selection.

## Prerequisites

- **Python 3.10+**
- **tectonic** (LaTeX engine, downloads packages on demand — no full TeX install needed)
  - macOS: `brew install tectonic`
  - Linux: see [tectonic install docs](https://tectonic-typesetting.github.io/en-US/install.html)
- An **OpenAI API key** ([platform.openai.com](https://platform.openai.com/api-keys))
- Your **resume as a PDF** (any format, will be parsed by GPT into structured bullets)

## Setup

```bash
git clone https://github.com/<your-handle>/ForkCV.ai.git
cd ForkCV.ai

python -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env.local
# edit .env.local and put your OPENAI_API_KEY in
```

## Ingest your master resume

Drop your PDF into the repo and ingest it once. The PDF is parsed into a structured bullet pool and discarded — only the JSON is kept.

```bash
mkdir -p data
cp /path/to/your_resume.pdf data/raw_master.pdf
.venv/bin/python main.py ingest --pdf data/raw_master.pdf --out data/master_cv_bank.json
```

You can edit the resulting `data/master_cv_bank.json` directly, or from the web UI (described below).

## Run the local server

```bash
.venv/bin/python -m uvicorn local_server:app --host 127.0.0.1 --port 8001 --reload
```

Open <http://127.0.0.1:8001>.

## Using the web UI

### Single-job flow
1. Type a company name and paste the JD into a tab.
2. Click **Generate**. After ~30-60s you get:
   - Left panel: JD signals (tech stack, must-haves, domain, **sponsorship status**, **language constraints**), keyword placements, editable bullet pool.
   - Right panel: PDF preview.
3. Edit any bullet, skill, or summary and click **Apply changes** to re-render.
4. **Save PDF** downloads the file.
5. **Mark applied** records the application to the tracker.

### Bulk-import flow
1. Expand **Bulk import jobs** at the top.
2. Paste 1-30 job links, one per line (LinkedIn or company URLs).
3. Optional: paste your LinkedIn `li_at` cookie if any links are restricted public-LI pages (stored only in your browser).
4. **Parse links** scrapes company + JD for each.
5. **Generate all** runs the tailor pipeline at concurrency 5.
6. Click any sidebar row to open it as a full tab and edit. **Download all (ZIP)** bundles every generated PDF.

### Master CV editor
Click **Edit master CV** in the top bar. Edit/add/delete bullets per role, skills per category, whole roles or projects. Save persists to `data/master_cv_bank.json` atomically.

### Applied-jobs tracker
Click **Applied jobs (N)** in the top bar. Each card has an editable status dropdown, date picker, and notes. Auto-saves on change.

### Outreach (optional)
Requires `HUNTER_API_KEY` in `.env.local` (sign up at <https://hunter.io>, key at <https://hunter.io/api-keys>). After generating a resume, expand the **Outreach** panel in the left column and click **Find contacts**. Approx. 30s later you get 10 ranked contact cards per job:

- Name + title + organization
- Category badge: `recruiter` / `hiring_manager` / `team_ic` / `other`
- Score 0-100 (title relevance + team match + shared school/employer + tenure sweet spot)
- LinkedIn URL + email (Hunter returns most emails inline; missing ones can be fetched with the per-card **Find email** button which calls Hunter's email-finder)
- Short LinkedIn note (≤300 chars) + email subject + body, all editable
- One-click `mailto:` button that opens your default mail client pre-filled
- Copy buttons for the note and email body

The message generator is heavily prompt-engineered to avoid AI-tone tells (no "I hope this finds you well", no "thrilled", no em-dashes, no three-paragraph cold-email scaffold). Every draft must contain one concrete detail about the recipient and one concrete detail about your own work, both drawn from real data.

Drafts are cached per generation in `data/outreach.json` so re-opening a past job restores them without re-querying Hunter.

## Architecture

```
ForkCV.ai/
├── local_server.py          # FastAPI app — all /api/* routes
├── main.py                  # CLI: ingest / tailor / render
├── src/
│   ├── pdf_ingestion.py     # PDF → MasterCV JSON (one-shot)
│   ├── schemas.py           # Pydantic models (MasterCV, JDAnalysis, ...)
│   ├── jd_analyzer.py       # JD → tech stack + must-haves + domain
│   ├── jd_scraper.py        # URL → company + JD text (LinkedIn + generic)
│   ├── selector_agent.py    # Master bullets + JD → tailored selection
│   ├── skills_enricher.py   # JD-aware skills section enrichment
│   ├── summary_rewriter.py  # (Disabled by default — summary stays locked.)
│   ├── sponsorship_extractor.py  # Regex: visa/sponsorship stance
│   ├── language_extractor.py     # Regex: required/preferred languages
│   ├── report.py            # Keyword placements + audit report
│   └── latex_compiler.py    # Jinja2 → .tex → tectonic → .pdf
├── templates/
│   └── resume.tex.j2        # The one-page LaTeX template
├── web_local/
│   ├── index.html
│   ├── app.js               # All UI logic (no framework)
│   └── styles.css
├── data/                    # Your CV bank + history (gitignored)
└── requirements.txt
```

## API surface

| Endpoint | Purpose |
|---|---|
| `GET /api/master` | Read master CV |
| `POST /api/master` | Save master CV (atomic) |
| `POST /api/tailor` | Generate a tailored CV + PDF for one JD; auto-appends to history |
| `POST /api/render` | Re-render a CV (after edits) to PDF |
| `POST /api/bulk/parse` | Scrape N job URLs → company + JD per URL |
| `POST /api/bulk/zip` | Render N CVs → ZIP of PDFs |
| `GET /api/generations` | List past generations (summary) |
| `GET /api/generations/{id}` | Fetch one full generation |
| `DELETE /api/generations/{id}` | Remove |
| `GET /api/applied` | List applications |
| `POST /api/applied` | Create application |
| `PATCH /api/applied/{id}` | Update status / notes / date |
| `DELETE /api/applied/{id}` | Remove |
| `POST /api/outreach/discover` | Hunter search + scoring + draft generation for a generation_id |
| `POST /api/outreach/reveal` | Hunter email-finder for one cached contact; updates `outreach.json` in place |
| `GET /api/outreach/{generation_id}` | Fetch cached outreach record (or `null`) |
| `DELETE /api/outreach/{generation_id}` | Remove cached outreach |

## Privacy

- Your master CV and JD text are sent to **OpenAI** for analysis and bullet selection (model: `gpt-4o-mini`). The PDF itself is never uploaded — only the parsed JSON.
- Everything else (master CV, generated CVs, applied-jobs history) stays on your local disk in `data/`.
- LinkedIn `li_at` cookie, if used, lives only in your browser's `localStorage`.

## License

MIT — see LICENSE.
