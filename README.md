# UK Nurse Jobs Scraper

Scrapes nurse job listings from **NHS Jobs**, **Reed.co.uk**, **Indeed UK**, **TotalJobs**, and **CV-Library**, then enriches every job with company contact details (phone, email, address). Results saved as clean JSON with a built-in data-quality report.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt
playwright install chromium   # needed for Indeed / TotalJobs / CV-Library

# 2. Copy env template and add your keys (all optional)
cp .env.example .env

# 3. (Optional, once) log in to ChatGPT/Gemini in a browser — the session
#    is saved and reused by every later --ai run, no API key needed
python main.py --login-ai

# 4. Run
python main.py
```

Output lands in `./output/jobs_YYYY-MM-DD_HH-MM.json`.

---

## Interactive Mode

Don't want to remember CLI flags? Run the wizard:

```bash
python interactive.py
```

It asks three questions, each with a sensible default if you just press Enter:

1. **Keywords** — comma-separated, or Enter to use the default 7 nursing job titles (`nurse, registered nurse, staff nurse, community nurse, RGN, RMN, RNLD`)
2. **Sources** — pick by number or name (`1,3` or `nhs,reed`), or Enter for all five sources
3. **AI fallback** — `y` to use the Gemini → Ollama → Anthropic chain to fill in missing requirements/benefits/phone/email; `N` (default) to use only free regex-based extraction

It then runs the full pipeline and points you at the [data provenance report](#data-provenance--source-report).

---

## Recommended one-time setup

Four free things make the scraper dramatically more reliable:

1. **Reed API key** (free) — register at [reed.co.uk/developers](https://www.reed.co.uk/developers), put the key in `.env` as `REED_API_KEY=...`. The scraper then uses Reed's official JSON API (no bot detection, full data). Without it, HTML scraping is used as fallback.
2. **Companies House API key** (free) — register at [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/), set `COMPANIES_HOUSE_API_KEY=...`. Enables company address/director lookups during contact enrichment. Without it that enricher is skipped.
3. **ChatGPT/Gemini browser login** (free) — run `python main.py --login-ai` once. The AI pipeline then drives the chat websites with your saved session — no API key needed.
4. **Gemini API key** (free tier, failover) — get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey), set `GEMINI_API_KEY=...`. Used automatically when the browser providers are unavailable, with further failover to your Ollama server.

---

## Common Commands

| What you want | Command |
|---|---|
| Interactive setup (choose keywords/sources/AI) | `python interactive.py` |
| Full run, all sources | `python main.py` |
| One-time ChatGPT/Gemini browser login | `python main.py --login-ai` |
| Fast test (NHS only, 10 jobs, no contact lookup) | `python main.py --sources nhs --max-results 10 --no-enrich` |
| London nurses only | `python main.py --location London` |
| Specific job titles | `python main.py --keywords "registered nurse" "RMN" "RNLD"` |
| Jobs posted this week | `python main.py --since 7` |
| Preview results without saving | `python main.py --dry-run` |
| Save as JSON + Excel + CSV | `python main.py --format json excel csv` |
| Skip contact lookup (much faster) | `python main.py --no-enrich` |
| Resume — skip already-seen jobs | `python main.py --resume` |
| Full AI pipeline (Gemini → Ollama) | `python main.py --ai` |
| Use a proxy list | `python main.py --proxies proxies.txt` |

---

## Job Sources

| Source | Method | Notes |
|---|---|---|
| `nhs` | Official REST API | No key needed |
| `reed` | **Official API** when `REED_API_KEY` is set, HTML fallback otherwise | Free key strongly recommended |
| `indeed` | Browser (Playwright), structured **mosaic JSON** extraction with CSS fallback | No login needed |
| `totaljobs` | Browser (Playwright), JSON-LD extraction with CSS fallback | StepStone bot detection — may be partially blocked |
| `cvlibrary` | Browser (Playwright), JSON-LD extraction with CSS fallback | Cloudflare-protected — may be partially blocked |

Select sources with `--sources nhs reed indeed totaljobs cvlibrary` (default: all five).

---

## AI Pipeline (`--ai`)

Providers are tried in a chain with automatic failover:

**ChatGPT (browser) → Gemini (browser) → Gemini API → Ollama → Anthropic**

### Browser AI — one-time login, no API key needed

```bash
python main.py --login-ai
```

A visible browser opens ChatGPT, then Gemini — sign in to each (you can skip either one). Sessions are saved to `output/.browser/chatgpt/` and `output/.browser/gemini/` and **every later `--ai` run drives the chat websites headless using the saved login**. Repeat only if a site logs you out (the scraper will tell you: `session expired — run: python main.py --login-ai`).

Notes on browser AI:
- Prompts are answered through the normal chat web UI, so it's free but slower than an API (each answer takes ~15–60 s; calls run one at a time through a single browser)
- Chat sites change their page layout from time to time and may challenge automated headless browsers — when that happens the call fails and the chain falls over to the API providers below automatically
- **"Verify you are human" challenge:** if ChatGPT/Gemini shows a Cloudflare human-check, the scraper automatically re-opens that provider in a **visible** window so you can solve it — leave the window open, tick the box, and it continues (the cleared cookie is reused for the rest of the run). To always start visible, run with `--ai-headful` or set `BROWSER_AI_HEADFUL=true` in `.env`.

### API failover providers

Configure in `.env` (all optional):

```env
GEMINI_API_KEY=your_key            # free tier
GEMINI_MODEL=gemini-flash-latest
OLLAMA_BASE_URL=http://103.207.85.46:11434
AI_MODEL=llama3.2
ANTHROPIC_API_KEY=                 # paid, last in chain
```

A provider that fails twice in a row (quota, network, layout change) is skipped for the rest of the run. Force a single provider with `--ai-provider chatgpt|gemini-web|gemini|ollama|anthropic`.

With `--ai` enabled the AI does three jobs:
1. **Description parsing** — extracts `requirements` and `benefits` lists from job ads that don't provide them (budget: `AI_PARSE_LIMIT`, default 30/run)
2. **Contact mining** — finds phone/email printed inside the job ad text (regex runs first and is free; AI only fills the gaps, and only values literally present in the text are accepted)
3. **Contact lookup fallback** — last-resort company contact research (budget: `AI_CALL_LIMIT`, default 20/run)

Contact mining via regex (step 2) runs on **every** run, even without `--ai` — contacts printed in the ad itself are the highest-confidence source and cost nothing.

---

## All CLI Options

```
python main.py [OPTIONS]

Search:
  --keywords KEYWORD [...]    Job titles to search (default: 7 nursing titles)
  --location LOCATION         Where to search (default: United Kingdom)
  --max-results N             Max jobs per keyword (default: 50)
  --sources SOURCE [...]      nhs reed indeed totaljobs cvlibrary (default: all)
  --since DAYS                Only jobs posted in the last N days

Output:
  --format FORMAT [...]       json  csv  excel  sqlite  (default: json)
  --output-dir PATH           Where to save files (default: ./output)
  --dry-run                   Print a JSON preview, don't save anything

Enrichment & AI:
  --no-enrich                 Skip contact lookup (phone/email) — faster
  --ai                        Enable the AI pipeline (description parsing + contact fallback)
  --ai-provider PROVIDER      Force: chatgpt | gemini-web | gemini | ollama | anthropic

Sessions & network:
  --login-ai                  One-time ChatGPT/Gemini browser login (saved + reused)
  --proxies PATH              Proxy list file for requests-based scrapers (NHS, Reed)

Other:
  --resume                    Skip jobs already saved in a previous run
  --headful                   Show the browser (debug Indeed/TotalJobs/CV-Library)
  -v, --verbose               Detailed per-request logging
```

---

## Output Format

Default output is a single JSON file: `output/jobs_YYYY-MM-DD_HH-MM.json`

### Top-level structure

```json
{
  "exported_at": "2026-06-09T18:00:00Z",
  "total_jobs": 142,
  "total_with_contact": 98,
  "total_with_phone": 71,
  "total_with_email": 85,
  "quality_report": {
    "per_source_raw": {"nhs_jobs": 120, "reed": 95, "indeed": 60},
    "per_source_unique": {"nhs_jobs": 80, "reed": 40, "indeed": 22},
    "field_coverage": {"description": 0.72, "salary": 0.61, "phone": 0.31, "email": 0.22},
    "dedup": {"raw_total": 275, "unique": 142, "removed": 133},
    "source_attribution": {
      "job_fields": {"description": {"reed": 60, "indeed": 22}, "salary_min": {"reed": 30, "derived": 11}},
      "contact_fields": {"phone_numbers": {"job_description": 20, "companies_house": 15}, "emails": {"website": 25}}
    },
    "ai_calls": 12,
    "errors": 0
  },
  "jobs": [ ... ]
}
```

### Single job object

```json
{
  "job_id": "f4bfbde8336762ed",
  "sources": ["reed", "indeed"],
  "title": "Staff Nurse",
  "company": "Compton Care",
  "company_url": "https://www.comptoncare.org.uk",
  "location": "Wolverhampton, WV3 9DH",
  "location_city": "Wolverhampton",
  "location_postcode": "WV3 9DH",
  "salary_text": "£30,110 a year",
  "salary_min": 30110.0,
  "salary_max": 30110.0,
  "salary_period": "annual",
  "job_type": "full-time",
  "description": "We are looking for...",
  "requirements": ["NMC registered", "2 years experience"],
  "benefits": ["NHS pension", "27 days annual leave"],
  "posted_at": "2026-06-05",
  "expires_at": "2026-06-30",
  "apply_url": "https://www.reed.co.uk/jobs/...",
  "contact": {
    "phone_numbers": ["+44 1902 774570"],
    "emails": ["hr@comptoncare.org.uk"],
    "contact_person": "HR Team",
    "address": "4 Compton Road West, Wolverhampton, WV3 9DH",
    "website": "https://www.comptoncare.org.uk",
    "company_number": "01607631",
    "company_type": "charity",
    "confidence_score": 90,
    "ai_used": false,
    "enrichment_sources": ["job_description", "website", "companies_house"],
    "field_sources": {
      "phone_numbers": ["job_description"],
      "emails": ["website"],
      "address": "companies_house",
      "company_number": "companies_house"
    }
  },
  "field_sources": {
    "title": "reed",
    "company": "reed",
    "salary_text": "indeed",
    "salary_min": "derived",
    "description": "indeed",
    "posted_at": "reed"
  },
  "_hash": "a3f9b2c1d4e5",
  "scraped_at": "2026-06-09T18:00:00Z"
}
```

### Field reference

| Field | Description |
|---|---|
| `job_id` | Source-native job identifier |
| `sources` | Which sites found this job (may appear on multiple) |
| `salary_text` | Human-readable salary string |
| `salary_min/max` | Parsed numeric values (same unit as `salary_period`) |
| `salary_period` | `"annual"` or `"hourly"` |
| `field_sources` | Per-field provenance: which scraper supplied each job field. `"derived"` means it was parsed from another field (e.g. `salary_min` parsed from `salary_text`); `"ai_description"` means AI extracted it from the job description |
| `contact.confidence_score` | 0–100: how reliable the contact data is |
| `contact.ai_used` | `true` if AI was used to find this contact |
| `contact.enrichment_sources` | Which enrichers found data: `job_description`, `website`, `companies_house`, `cqc`, `charities`, `duckduckgo`, `ai` |
| `contact.field_sources` | Per-field provenance: which enricher supplied each contact field |
| `_hash` | Deduplication fingerprint (title + company + location) |

---

## Data Provenance / Source Report

Every run writes a plain-text **source report** alongside the other output files:
`output/source_report_YYYY-MM-DD_HH-MM.txt`. It answers "where did this piece of
data come from?" — for example:

```
Job data — which source supplied each field (count of jobs):
  title            reed: 80, indeed: 22, totaljobs: 18
  salary_text      reed: 70, indeed: 30
  salary_min       derived: 95, reed: 5
  description      indeed: 60, reed: 40

Contact data — which source supplied each field (count of companies):
  phone_numbers    job_description: 20, companies_house: 15, website: 10
  emails           website: 25, duckduckgo: 5
  address          companies_house: 30
```

- `derived` = parsed/cleaned from another field (e.g. `salary_min` extracted from `salary_text`)
- `ai_description` = filled in by the AI pipeline from the job description
- The same breakdown is in every JSON export under `quality_report.source_attribution`, and per-record under each job's `field_sources` / `contact.field_sources`.

---

## How Contact Enrichment Works

For each unique company, the scraper tries these sources in order, stopping when it has a phone **and** email:

0. **The job ad itself** — phone/email printed in the description (free, highest confidence)
1. **Company website** — scrapes `/contact`, `/about`, `/team` pages
2. **Companies House** — UK registered address + directors (free, no key needed)
3. **Charity Commission** — phone/email for hospices and care charities
4. **CQC open data** — Care Quality Commission registry (care homes, nursing homes)
5. **DuckDuckGo** — searches `"Company Name" contact phone email UK`
6. **AI fallback** — Gemini/Ollama/Anthropic chain (only if `--ai` flag is set)

---

## Proxies (optional)

Create a text file with one proxy per line:

```
http://user:pass@host:port
http://other-host:port
```

Run with `python main.py --proxies proxies.txt` (or set `PROXIES_FILE=` in `.env`). Proxies apply to the requests-based scrapers (NHS, Reed) and rotate automatically after a 403. The browser-based scrapers don't use proxies — rotating IPs would conflict with the saved Indeed login session.

---

## Environment Variables (`.env`)

Copy `.env.example` to `.env` and edit as needed. All are optional.

```env
# Sources
REED_API_KEY=                        # free, reed.co.uk/developers
COMPANIES_HOUSE_API_KEY=             # free, developer.company-information.service.gov.uk

# AI chain (gemini → ollama → anthropic)
GEMINI_API_KEY=
GEMINI_MODEL=gemini-flash-latest
OLLAMA_BASE_URL=http://103.207.85.46:11434
AI_MODEL=llama3.2
ANTHROPIC_API_KEY=
AI_FALLBACK_ENABLED=false            # --ai flag does the same
AI_CALL_LIMIT=20                     # contact-lookup budget per run
AI_PARSE_LIMIT=30                    # description-parsing budget per run

# Scraping
MAX_RESULTS_PER_KEYWORD=50
REQUEST_DELAY_MIN=2.0
PROXIES_FILE=

# Output
OUTPUT_DIR=./output

# Email notifications after each scheduled run (optional)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your_app_password
NOTIFY_EMAIL=you@gmail.com
```

---

## Scheduled / Automated Runs (VPS)

Use `scheduler.py` as the cron target — it prevents overlapping runs, rotates log files, and emails a summary.

```bash
# Edit crontab
crontab -e

# Add: run every Monday at 6am
0 6 * * 1 cd /home/ubuntu/UK-Data-Scrapper && python scheduler.py
```

Log files are written to `output/logs/scraper_YYYY-MM-DD.log`.

---

## Project Layout

```
UK-Data-Scrapper/
├── main.py            ← run this
├── interactive.py     ← guided setup wizard
├── pipeline.py        ← orchestrates all stages
├── config.py          ← all settings
├── scheduler.py       ← cron wrapper
├── requirements.txt
├── .env.example       ← copy to .env
│
├── scrapers/
│   ├── nhs.py             ← NHS Jobs REST API
│   ├── reed.py            ← Reed official API + HTML fallback
│   ├── indeed.py          ← Indeed UK (Playwright, mosaic JSON)
│   ├── totaljobs.py       ← TotalJobs (Playwright, JSON-LD)
│   ├── cvlibrary.py       ← CV-Library (Playwright, JSON-LD)
│   ├── playwright_base.py ← shared browser boilerplate + anti-detection
│   └── jsonld.py          ← shared schema.org JobPosting parser
│
├── enrichers/
│   ├── orchestrator.py   ← runs enrichers in order
│   ├── website.py        ← scrapes company website
│   ├── companies_house.py
│   ├── charities.py
│   ├── cqc.py
│   ├── duckduckgo.py
│   └── ai_enricher.py    ← AI contact fallback
│
├── processing/
│   ├── dedup.py       ← 3-level deduplication
│   ├── cleaner.py     ← phone/email/salary normalisation
│   ├── ai_parser.py   ← mines descriptions (regex + AI)
│   ├── quality.py     ← run quality report
│   └── merger.py      ← merges enricher results
│
├── exporters/
│   ├── json_export.py
│   ├── csv_export.py
│   ├── excel_export.py   ← 3-sheet workbook
│   └── sqlite_export.py  ← persistent store
│
├── utils/
│   ├── ai_client.py   ← AI failover chain (browser + API providers)
│   ├── browser_ai.py  ← ChatGPT/Gemini web UI automation + --login-ai
│   ├── retry.py       ← exponential backoff
│   ├── proxy.py       ← optional proxy rotation
│   └── ...
│
└── output/            ← all output files land here (gitignored)
```

---

## Troubleshooting

**Indeed: "Executable doesn't exist … chrome-headless-shell"**
You need to download the Playwright browser binary. Run once:
```bash
playwright install chromium
```

**Indeed: "Playwright Sync API inside the asyncio loop" (Windows)**
This is a known Windows issue — automatically fixed in the scraper code. If you still see it, make sure you have the latest code and Python 3.10+.

**Indeed blocked / CAPTCHA**
Run with `--headful`, solve the CAPTCHA manually, and retry. Indeed blocks come and go — waiting a few minutes between runs and lowering `--max-results` also helps.

**Reed returns 403 / 0 jobs**
Get a free API key from [reed.co.uk/developers](https://www.reed.co.uk/developers) and set `REED_API_KEY` in `.env` — the official API has no bot detection. Without a key the HTML fallback warms up the session and retries, but can still be blocked.

**TotalJobs / CV-Library return 0 jobs**
Both sites use aggressive bot protection (StepStone / Cloudflare). Try `--headful` to see what the browser hits. If they stay blocked from your network, exclude them: `--sources nhs reed indeed`.

**NHS: "Expecting value: line 1 column 1" / 403 / empty response**
- The NHS API blocks many datacenter/VPS IPs — run from a residential connection
- Check [api.jobs.nhs.uk](https://api.jobs.nhs.uk) status
- Use `--verbose` to see the response preview the scraper logs

**NHS: "expected JSON but got text/html" with a garbled preview (starts with `\x1b`)**
The NHS API (and some other sites) respond Brotli-compressed (`Content-Encoding: br`).
Without the `brotli` package, `requests` cannot decompress them and you get raw
compressed bytes instead of JSON. Fix: re-install dependencies to pick up `brotli`:
```bash
pip install -r requirements.txt
```

**Companies House enricher: 401 Unauthorized on every company**
The Companies House API requires a free API key. Register at
[developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/)
and set `COMPANIES_HOUSE_API_KEY=...` in `.env`. Without a key the enricher is
skipped automatically (no failed requests).

**AI: "all providers in the chain failed"**
- ChatGPT/Gemini browser: run `python main.py --login-ai` again if a session expired; set `PLAYWRIGHT_HEADLESS=false` in `.env` to watch what the browser hits
- Gemini API: check `GEMINI_API_KEY` is valid and has quota left
- Ollama: confirm the server is reachable (`curl http://103.207.85.46:11434/api/tags`) and the model is pulled (`ollama pull llama3.2`)
- A provider that fails twice is skipped for the rest of the run — restart to retry it

**"No jobs collected"**
Use `--verbose` to see per-request details. The scraper logs the HTTP status and response preview for every failed request, which will identify the cause.

**Phone numbers look wrong**
The cleaner rejects numbers that look like SVG coordinates or years. If valid numbers are being filtered, check the raw `description` field in the JSON and report the pattern.
