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
- **Job discovery (optional)**: scrape LinkedIn job listings against your preferences (roles, locations, salary, visa needs, languages, max YoE, keywords), dedupe across runs, hard-filter + score each job, and auto-tailor resumes for the top N matches. Persists locally + optionally upserts to a Google Sheet. Apify + Sheets keys go in `.env.local`. See "Discovery flow" below.
- **Auto-apply agent (experimental, NOT production-ready)**: Playwright-driven headed browser that opens the application URL, follows LinkedIn redirects, and tries to fill the form. Greenhouse + Phenom adapters reach the "form filled" stage; submit is intentionally gated off. ATS DOMs vary by employer (especially Phenom tenants) so each company often needs custom selector tuning. **In practice you'll still review + submit manually.** The auto-tailored resume + outreach drafts are where this tool produces real value today.

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

### Discovery flow (optional)

Twice-daily (or on-demand) job discovery that scrapes LinkedIn, scores each job against your preferences, and auto-tailors resumes for the best matches.

**Setup:**

1. **Apify** — sign up at <https://console.apify.com>, get a token at Settings → Integrations → API. Add to `.env.local`:
   ```
   APIFY_API_TOKEN=apify_api_xxx
   ```
   Free tier covers ~5,000 jobs/mo; the default actor (`bebity~linkedin-jobs-scraper`) costs ~$0.50-1 per 1,000 jobs. 200 jobs/day × 2 runs ≈ $6-12/mo extra.

2. **(Optional) Google Sheets** — for a synced online tracker.
   - Create a Google Cloud project at <https://console.cloud.google.com>
   - Enable the Sheets API (APIs & Services → Library → "Google Sheets API" → Enable)
   - Create a service account (IAM & Admin → Service Accounts → Create)
   - Generate a JSON key (Keys → Add Key → JSON), save to e.g. `data/sheets-sa.json`
   - Create a Google Sheet, share it with the service-account email (Editor access)
   - Add to `.env.local`:
     ```
     GOOGLE_SERVICE_ACCOUNT_JSON=data/sheets-sa.json
     SHEETS_SPREADSHEET_ID=<the long id in your sheet's URL>
     SHEETS_RANGE=Sheet1!A1
     ```

3. **Configure preferences** in the web UI: click **Preferences** in the topbar, fill the form:
   - Target roles (one per line)
   - Locations + remote toggle
   - Min salary, visa needs, languages, max YoE
   - Keywords include / exclude, companies include / exclude
   - Auto-generate top N + min score threshold

4. **Run** — open the **Discovered jobs** drawer and click **Run discovery now**. ~3-10 minutes for the Apify run + auto-tailoring. The drawer polls every 5s and refreshes when the run completes.

**What you see per job:** company, title, location, posted-at, salary (when scraped), visa-sponsorship pill (green/red/amber/grey), YoE pill, language pill, 0-100 fit score, **Open posting ↗**, **Generate resume** / **Open resume**, **Mark applied**, delete. Filter by company/title, hide rejected, hide applied.

**Auto-tailoring**: any job that passes all hard filters AND scores at or above your `Min score threshold` (default 70) is automatically resume-tailored, up to `Top N` per run (default 5). Caps OpenAI cost at ~$0.25/run.

**Scheduling twice daily** (your machine must be reachable):
- **Option A: cron-job.org** (free, public URL needed). Use **ngrok** or **Cloudflare Tunnel** to expose `http://127.0.0.1:8001` to the internet. Then create a cron-job.org job that POSTs to `https://<your-tunnel>/api/discovery/run` at 08:00 and 20:00.
- **Option B: macOS launchd** (local). Create a `.plist` that runs `curl -X POST http://127.0.0.1:8001/api/discovery/run` twice daily. Server must be running at trigger time.
- **Option C: manual** — just click **Run discovery now** when you remember.

**Data layout:**
- `data/preferences.json` — your prefs (drawer-edited)
- `data/discovered_jobs.json` — all discovered jobs (dedup'd, with state)
- `data/discovery_runs.json` — last 20 run summaries
- Google Sheet (if configured) — 6 tabs (US, Europe, UAE, Australia & NZ, UK, Other), routed by location, refreshed each run + each `POST /api/sheets/sync`. Hard-rejected jobs (visa/lang/YoE/keyword/company filters) are excluded — they stay in the local drawer for transparency.

### PersonalProfile — your application source of truth

Single JSON file (`data/personal_profile.json`) the auto-apply field mapper consults to fill every form question. 117 fields across 18 categories: identity / location / work auth (US/UK/EU/Canada/UAE/Australia granular flags) / EEO (gender, race, hispanic_or_latino, veteran, disability, LGBTQ, religion) / online profiles (LinkedIn, GitHub, StackOverflow, Behance, Medium, …) / education / languages / career / compensation (current vs expected, USD + local currency) / relocation + travel / work preferences / background check + clearance / referral source / employment history / consent toggles / 9 narrative templates (why interested, biggest strength, 5-year goal, …) / and a `custom_answers: { "label substring": "answer" }` catch-all.

Edit by hand or via:
```bash
curl http://127.0.0.1:8001/api/personal-profile        # read
curl -X POST http://127.0.0.1:8001/api/personal-profile -d @profile.json  # write
```

When you add fields to the JSON, no schema migration needed — Pydantic forward-compat reads them on next request.

### Auto-apply (experimental — see honest caveat above)

> ⚠️ **Not production-ready.** Each ATS (Workday, Phenom, iCIMS, custom company portals) has its own DOM quirks that require per-employer selector tuning. The agent will navigate, screenshot, and fill known fields — but reliably submitting end-to-end across every employer is an open-ended cat-and-mouse with ATS DOM changes and bot detection. The pragmatic flow today is: let the agent open the form + pre-fill what it can + take screenshots, then YOU review and click submit in the open browser. This still saves real time per application but is not "zero-touch."

The agent opens a real Chromium browser (headed, stealth-patched) using a persistent profile under `data/browser-profile/`, navigates to the job's application URL, fills every field it can match from your `PersonalProfile`, uploads the **tailored** resume PDF for that job, and either stops at the Submit button (default) or clicks it (when `AUTO_APPLY_AUTO_SUBMIT=true`).

**Install Playwright browsers (one-time):**

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

**Supported portals (v1):** Greenhouse (production-ready), Lever + Ashby (skeletons reusing Greenhouse logic; submit-button selectors may need tuning per posting). Workday is intentionally NOT implemented yet — its per-tenant subdomain layout + reCAPTCHA requires a dedicated adapter (Phase 4).

**Blocked portals (by design):** LinkedIn Easy Apply, Indeed Apply, Glassdoor Apply. These auto-ban accounts and shadow-reject applications. The detector rejects them with a clear reason. If a job's `application_link` is a LinkedIn URL, click into the posting on LinkedIn and copy the **"Apply on company site"** URL into your discovered-jobs entry instead.

**Configuration in `.env.local`:**

| Variable | Default | Meaning |
|---|---|---|
| `AUTO_APPLY_AUTO_SUBMIT` | `false` | When `false`, agent fills + screenshots + STOPS at submit. Recommended for first 5-10 runs. |
| `AUTO_APPLY_DAILY_CAP` | `10` | Hard cap on submissions per UTC day. |
| `BROWSER_PROFILE_DIR` | `data/browser-profile` | Persistent browser data dir (cookies + sessions). |
| `AUTO_APPLY_USER_AGENT` | recent Chrome string | Override if needed. |

**Kill switches built in:** CAPTCHA presence on the page → halt + status `blocked`. 3 consecutive failures → halt for the day (in-process counter, resets on server restart). Manual reset via server restart.

**UI:**
- **Per-row "Auto-apply" button** on each Discovered-jobs card → confirms → runs the agent in the background → updates status in the Auto-apply drawer.
- **Topbar "Auto-apply (N)" button** → drawer with live attempt status, screenshots at each step (page load / filled / after submit), step log, error messages. Polls every 4 s when open.

**Audit trail:**
- `data/apply_attempts.json` — last 200 attempts with full step logs, screenshots paths, submit-mode, errors.
- `data/screenshots/<attempt_id>/*.png` — per-attempt screenshots. Viewable in the drawer; click to open full-size.

**Safety posture:** the agent never opens a tab without your explicit click. It honors a hard daily cap. It stops on CAPTCHA. It never submits when `AUTO_APPLY_AUTO_SUBMIT=false`. Use the screenshots to verify what got filled before flipping submit-on. Run a few in `draft` mode first.

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
| `GET /api/preferences` · `POST /api/preferences` | Read / write `data/preferences.json` |
| `POST /api/discovery/run` | Kick off a discovery run in the background; returns run record |
| `GET /api/discovery/status` | Latest run state (in-flight or finished) |
| `GET /api/discovery/jobs` | List all discovered jobs |
| `PATCH /api/discovery/jobs/{id}` | Update applied / rejected / generation_id |
| `DELETE /api/discovery/jobs/{id}` | Remove |
| `GET /api/sheets/status` | Sheets sync configuration check |
| `POST /api/sheets/sync` | Manually sync `discovered_jobs.json` to the configured Google Sheet |
| `POST /api/apply/{job_id}` | Kick off the auto-apply agent for a discovered job |
| `GET /api/apply/attempts` | List recent attempts (live + finished) |
| `GET /api/apply/attempts/{id}` | Full attempt record with step logs |
| `GET /api/apply/screenshot/{id}/{file}` | Serve a per-step screenshot PNG |

## Privacy

- Your master CV and JD text are sent to **OpenAI** for analysis and bullet selection (model: `gpt-4o-mini`). The PDF itself is never uploaded — only the parsed JSON.
- Everything else (master CV, generated CVs, applied-jobs history) stays on your local disk in `data/`.
- LinkedIn `li_at` cookie, if used, lives only in your browser's `localStorage`.

## License

MIT — see LICENSE.
