# UK Nurse Jobs Scraper

Scrapes nurse job listings from **Indeed UK** and **Reed.co.uk** (official API only — no other job sites are scraped), then enriches every job with company contact details (phone, email, address) using a search engine and, when details are still missing, the **Google Gemini API**. Results saved as clean JSON (optionally also MySQL/MariaDB for CRM ingestion) with a built-in data-quality report that shows exactly which source — including Gemini — supplied each field.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt
playwright install chromium   # needed for Indeed

# 2. Copy env template and add your keys (all optional)
cp .env.example .env

# 3. Run
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
2. **Sources** — pick by number or name (`1,2` or `reed,indeed`), or Enter for both (Reed is skipped automatically if `REED_API_KEY` isn't set)
3. **AI fallback** — `y` to use the Gemini API (with Ollama/Anthropic failover) to fill in missing requirements/benefits/phone/email/website/contact person; `N` (default) to use only free regex-based extraction

It then runs the full pipeline and points you at the [data provenance report](#data-provenance--source-report).

---

## Recommended one-time setup

A few things make the scraper dramatically more reliable:

1. **Reed API key** (free) — register at [reed.co.uk/developers](https://www.reed.co.uk/developers), put the key in `.env` as `REED_API_KEY=...`. **Required to use Reed at all** — without it Reed is skipped entirely (no HTML-scraping fallback).
2. **Gemini API key** (free tier) — get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey), set `GEMINI_API_KEY=...` and `AI_FALLBACK_ENABLED=true`. This is the primary way missing job/contact details (company website, contact person, email, phone, requirements, benefits) get filled in — every field it fills is tagged `"gemini"` / `"gemini_description"` in `field_sources` so you always know what came from Gemini. Live Google **search-grounding** is enabled automatically for company contact lookups once this key is set (no extra key/flag).
3. **Companies House API key** (free) — register at [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/), set `COMPANIES_HOUSE_API_KEY=...`. Enables company address/director lookups during contact enrichment. Without it that enricher is skipped.
4. **SerpAPI key** (paid, optional) — register at [serpapi.com](https://serpapi.com/), set `SERPAPI_KEY=...`. A reliable Google-search API used as a fallback when the free DuckDuckGo enricher fails or is blocked (common on datacenter/VPS IPs). Without it that fallback is skipped.

---

## Common Commands

| What you want | Command |
|---|---|
| Interactive setup (choose keywords/sources/AI) | `python interactive.py` |
| Full run, all sources | `python main.py` |
| Fast test (Indeed only, 10 jobs, no contact lookup) | `python main.py --sources indeed --max-results 10 --no-enrich` |
| London nurses only | `python main.py --location London` |
| Specific job titles | `python main.py --keywords "registered nurse" "RMN" "RNLD"` |
| Jobs posted this week | `python main.py --since 7` |
| Preview results without saving | `python main.py --dry-run` |
| Save as JSON + Excel + CSV | `python main.py --format json excel csv` |
| Export straight into the CRM database | `python main.py --format mysql` |
| Skip contact lookup (much faster) | `python main.py --no-enrich` |
| Resume — skip already-seen jobs | `python main.py --resume` |
| Full AI pipeline (Gemini fills in missing fields) | `python main.py --ai` |
| Use a proxy list | `python main.py --proxies proxies.txt` |

---

## Job Sources

Only two job sources are used — no other job board is scraped:

| Source | Method | Notes |
|---|---|---|
| `indeed` | Browser (Playwright), structured **mosaic JSON** extraction with CSS fallback | No login needed; always runs |
| `reed` | **Official API only** | Requires `REED_API_KEY` — without it, Reed is skipped entirely (no HTML-scraping fallback) |

Search-engine results (DuckDuckGo, with SerpAPI as a paid fallback) are used only for **contact/company enrichment** (finding phone/email/address for a company already found via Indeed/Reed) — see [How Contact Enrichment Works](#how-contact-enrichment-works) below, not as a job-discovery source.

Select sources with `--sources reed indeed` (default: both).

---

## AI Pipeline (`--ai`)

The primary AI provider is the **Google Gemini API** — set `GEMINI_API_KEY` in `.env` and it completes missing job/contact fields (company website, contact person, email, phone, requirements, benefits) whenever Indeed/Reed and the search-engine enrichment don't find them. Every field Gemini fills in is tagged `"gemini"` (contacts) or `"gemini_description"` (job description mining) in `field_sources` / `contact.field_sources`, so the output always shows which data came from Gemini specifically.

Providers are tried in a chain with automatic failover — **API only, no browser automation**:

**Gemini API → Ollama → Anthropic**

### Gemini search-grounding

Company contact lookups call Gemini with its built-in **`google_search` tool** enabled, so Gemini runs a live Google search before answering instead of relying only on its training data. This is automatic once `GEMINI_API_KEY` is set — no extra key or flag. Google bills grounded requests with a small per-request grounding fee, so the `AI_CALL_LIMIT` budget still applies.

### Providers

Configure in `.env` (all optional):

```env
GEMINI_API_KEY=your_key            # free tier, primary provider
GEMINI_MODEL=gemini-flash-latest
OLLAMA_BASE_URL=http://localhost:11434
AI_MODEL=llama3.2:3b               # exact tag from `ollama list` / `/api/tags`
ANTHROPIC_API_KEY=                 # paid, last in chain
```

A provider that fails twice in a row (quota, network) is skipped for the rest of the run. Force a single provider with `--ai-provider gemini|ollama|anthropic`.

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
  --sources SOURCE [...]      reed indeed (default: both; reed skipped without REED_API_KEY)
  --since DAYS                Only jobs posted in the last N days

Output:
  --format FORMAT [...]       json  csv  excel  sqlite  mysql  (default: json)
  --output-dir PATH           Where to save files (default: ./output)
  --dry-run                   Print a JSON preview, don't save anything

Enrichment & AI:
  --no-enrich                 Skip contact lookup (phone/email) — faster
  --ai                        Enable the AI pipeline (Gemini fills in missing fields)
  --ai-provider PROVIDER      Force: gemini | ollama | anthropic
  --fresh                     Ignore the contact cache — re-fetch every company
  --no-cache                  Don't read or write the contact cache this run

Network:
  --proxies PATH              Proxy list file for requests-based scrapers (Reed)
  --browser-proxy URL         Residential proxy for the Indeed browser (avoids bot blocks)

Other:
  --resume                    Skip jobs already saved in a previous run
  --headful                   Show the browser (debug Indeed)
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
    "per_source_raw": {"reed": 95, "indeed": 60},
    "per_source_unique": {"reed": 40, "indeed": 22},
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
    "ai_used": true,
    "enrichment_sources": ["job_description", "website", "companies_house", "gemini"],
    "field_sources": {
      "phone_numbers": ["job_description"],
      "emails": ["website"],
      "address": "companies_house",
      "company_number": "companies_house",
      "contact_person": "gemini"
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
| `field_sources` | Per-field provenance: which scraper supplied each job field. `"derived"` means it was parsed from another field (e.g. `salary_min` parsed from `salary_text`); `"gemini_description"` means the Gemini API extracted it from the job description |
| `contact.confidence_score` | 0–100: how reliable the contact data is |
| `contact.ai_used` | `true` if AI (Gemini, or a configured fallback provider) was used to find this contact |
| `contact.enrichment_sources` | Which enrichers found data: `job_description`, `website`, `companies_house`, `cqc`, `charities`, `duckduckgo` (search engine), `serpapi` (paid search fallback), `gemini` (or another AI provider name if configured) |
| `contact.field_sources` | Per-field provenance: which enricher supplied each contact field |
| `_hash` | Deduplication fingerprint (title + company + location) |

---

## Data Provenance / Source Report

Every run writes a plain-text **source report** alongside the other output files:
`output/source_report_YYYY-MM-DD_HH-MM.txt`. It answers "where did this piece of
data come from?" — for example:

```
Job data — which source supplied each field (count of jobs):
  title            reed: 80, indeed: 22
  salary_text      reed: 70, indeed: 30
  salary_min       derived: 95, reed: 5
  description      indeed: 60, reed: 40
  requirements     gemini_description: 12

Contact data — which source supplied each field (count of companies):
  phone_numbers    job_description: 20, companies_house: 15, website: 10
  emails           website: 25, duckduckgo: 5
  contact_person   gemini: 8
  address          companies_house: 30
```

- `derived` = parsed/cleaned from another field (e.g. `salary_min` extracted from `salary_text`)
- `gemini_description` = filled in by the Gemini API from the job description (or `<provider>_description` if a different AI provider answered)
- `gemini` = filled in by the Gemini API contact-lookup fallback (or `<provider>` for another configured AI provider)
- The same breakdown is in every JSON export under `quality_report.source_attribution`, and per-record under each job's `field_sources` / `contact.field_sources`.

---

## How Contact Enrichment Works

For each unique company, the scraper tries these sources in order, stopping when it has a phone **and** email:

0. **The job ad itself** — phone/email printed in the description (free, highest confidence)
1. **Company website** — scrapes `/contact`, `/about`, `/team` pages
2. **Companies House** — UK registered address + directors (free, no key needed)
3. **Charity Commission** — phone/email for hospices and care charities
4. **CQC open data** — Care Quality Commission registry (care homes, nursing homes)
5. **DuckDuckGo (search engine)** — searches `"Company Name" contact phone email UK`
6. **SerpAPI (paid Google search)** — fallback when DuckDuckGo fails/is blocked, only if `SERPAPI_KEY` is set
7. **AI fallback** — Gemini API (with Ollama/Anthropic failover, only if `--ai` flag is set); Gemini uses live Google search-grounding for the lookup, and any field it fills is tagged with the actual provider name (e.g. `"gemini"`) in `field_sources`

### Cross-run contact cache (new/old change tracking)

Once a company's contact data has been fetched, it's stored in the SQLite DB (`output/scraper.db`) and **reused on later runs instead of being re-fetched** — so a second run over the same companies costs almost no network calls. Behaviour:

- **Auto-reuse**: enabled by default (`CACHE_CONTACTS=true`). A company enriched within the last `CONTACT_CACHE_DAYS` (default **30**) days is served straight from the cache, tagged with a `"cache"` entry in `enrichment_sources`.
- **Self-healing**: a cached company older than the window is re-fetched from scratch (fresh data overwrites the cache).
- **Change tracking**: if this run's job ad lists a phone/email that differs from the cached value, **both are kept** — the union appears in `phone_numbers`/`emails`, and a `changes` block records what differed:
  ```json
  "changes": { "phone_numbers": { "old": ["+44 20 1234 5678"], "new": ["+44 20 9999 0000"] } }
  ```
- **Force a refresh**: `python main.py --fresh` ignores the cache and re-fetches everything; `--no-cache` skips reading and writing the cache for that run. Tune the window with `CONTACT_CACHE_DAYS` in `.env`.

The cache is written after every run regardless of `--format`, so it works even with the default JSON-only output.

---

## Proxies (optional)

There are two separate proxy paths, because the two scrapers use different HTTP stacks:

**1. Requests-based scrapers (Reed API) — `--proxies` / `PROXIES_FILE`**

Create a text file with one proxy per line:

```
http://user:pass@host:port
http://other-host:port
```

Run with `python main.py --proxies proxies.txt` (or set `PROXIES_FILE=` in `.env`). These rotate automatically after a 403.

**2. Browser scraper (Indeed) — `--browser-proxy` / `PLAYWRIGHT_PROXY`**

Indeed is browser-based and is the source most likely to hit bot blocks. Route it through a **residential proxy** so each request looks like an ordinary home/mobile IP:

```bash
python main.py --sources indeed --browser-proxy "http://user:pass@gb.provider.com:7777"
```

Or set `PLAYWRIGHT_PROXY=` in your local `.env`. Use a single auto-rotating gateway URL — the provider rotates the exit IP server-side per request, so it doesn't conflict with the saved Indeed login profile. Credentials are masked in logs (only `host:port` is shown).

> **Note:** Reliable residential proxies are a **paid** service (billed per GB) — Bright Data, Oxylabs, Smartproxy/Decodo, IPRoyal, etc. "Free proxy lists" are datacenter IPs that Indeed blocks on sight. Keep the gateway URL in your local, gitignored `.env` — never commit it.

---

## Environment Variables (`.env`)

Copy `.env.example` to `.env` and edit as needed. All are optional.

```env
# Sources
REED_API_KEY=                        # required to use Reed at all — reed.co.uk/developers
COMPANIES_HOUSE_API_KEY=             # free, developer.company-information.service.gov.uk

# AI chain (gemini → ollama → anthropic) — Gemini is the primary fill-in provider
GEMINI_API_KEY=
GEMINI_MODEL=gemini-flash-latest
OLLAMA_BASE_URL=http://localhost:11434
AI_MODEL=llama3.2:3b               # exact tag from `ollama list` / `/api/tags`
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

# CRM export — MySQL / MariaDB (optional, see docs/CRM_INTEGRATION.md)
MYSQL_HOST=
MYSQL_PORT=3306
MYSQL_DATABASE=
MYSQL_USER=
MYSQL_PASSWORD=

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
│   ├── reed.py            ← Reed official API only (no key = skipped)
│   ├── indeed.py          ← Indeed UK (Playwright, mosaic JSON)
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
│   ├── serpapi.py        ← paid Google-search fallback
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
│   ├── sqlite_export.py  ← persistent store
│   └── mysql_export.py   ← MySQL/MariaDB CRM export
│
├── docs/
│   └── CRM_INTEGRATION.md ← Laravel + MariaDB integration guide
│
├── utils/
│   ├── ai_client.py   ← AI failover chain (Gemini → Ollama → Anthropic)
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

**Reed returns 0 jobs / "no REED_API_KEY configured"**
Get a free API key from [reed.co.uk/developers](https://www.reed.co.uk/developers) and set `REED_API_KEY` in `.env`. Reed is used exclusively through the official API — there is no HTML-scraping fallback, so without a key Reed is skipped entirely and only Indeed runs.

**Reed API key rejected (401/403)**
Double-check the key value in `.env`. A rejected key causes Reed to be skipped for the rest of that run (it does not fall back to HTML scraping).

**Companies House enricher: 401 Unauthorized on every company**
The Companies House API requires a free API key. Register at
[developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/)
and set `COMPANIES_HOUSE_API_KEY=...` in `.env`. Without a key the enricher is
skipped automatically (no failed requests).

**AI: "all providers in the chain failed"**
- Gemini API: check `GEMINI_API_KEY` is valid (get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)) and has quota left
- Ollama: confirm `OLLAMA_BASE_URL` points at the host actually running Ollama (defaults to `http://localhost:11434`), that the server is reachable (`curl http://localhost:11434/api/tags`), and the model is pulled (`ollama pull llama3.2:3b`). Set `AI_MODEL` to the exact tag from `/api/tags` — the scraper auto-matches tag suffixes (so `llama3.2` resolves to a pulled `llama3.2:3b`), but it can't invent a model that isn't pulled
- A provider that fails twice is skipped for the rest of the run — restart to retry it

**MySQL export fails: "MySQL export requires MYSQL_HOST and MYSQL_DATABASE"**
Set `MYSQL_HOST`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD` in `.env` before running with `--format mysql`. See [docs/CRM_INTEGRATION.md](docs/CRM_INTEGRATION.md).

**"No jobs collected"**
Use `--verbose` to see per-request details. The scraper logs the HTTP status and response preview for every failed request, which will identify the cause.

**Phone numbers look wrong**
The cleaner rejects numbers that look like SVG coordinates or years. If valid numbers are being filtered, check the raw `description` field in the JSON and report the pattern.
