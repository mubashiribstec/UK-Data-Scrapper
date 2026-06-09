# UK Nurse Jobs Scraper

Scrapes nurse job listings from **NHS Jobs**, **Reed.co.uk**, and **Indeed UK**, then enriches every job with company contact details (phone, email, address). Results saved as clean JSON.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt
playwright install chromium   # needed for Indeed only

# 2. Copy env template (all keys optional)
cp .env.example .env

# 3. Run
python main.py
```

Output lands in `./output/jobs_YYYY-MM-DD_HH-MM.json`.

---

## Common Commands

| What you want | Command |
|---|---|
| Full run, all sources | `python main.py` |
| Fast test (NHS only, 10 jobs, no contact lookup) | `python main.py --sources nhs --max-results 10 --no-enrich` |
| London nurses only | `python main.py --location London` |
| Specific job titles | `python main.py --keywords "registered nurse" "RMN" "RNLD"` |
| Jobs posted this week | `python main.py --since 7` |
| Preview results without saving | `python main.py --dry-run` |
| Save as JSON + Excel + CSV | `python main.py --format json excel csv` |
| Skip contact lookup (much faster) | `python main.py --no-enrich` |
| Resume — skip already-seen jobs | `python main.py --resume` |
| Use AI to fill missing contacts (Ollama) | `python main.py --ai` |

---

## Installation

### Requirements
- Python 3.10+
- pip

### Steps

```bash
# Clone and enter the repo
git clone <repo-url>
cd UK-Data-Scrapper

# Install Python packages
pip install -r requirements.txt

# Install Playwright's Chromium browser (for Indeed)
playwright install chromium

# Optional: copy and edit the env file
cp .env.example .env
```

If you **don't need Indeed** (just NHS + Reed), you can skip `playwright install`.

---

## All CLI Options

```
python main.py [OPTIONS]

Search:
  --keywords KEYWORD [...]    Job titles to search (default: 7 nursing titles)
  --location LOCATION         Where to search (default: United Kingdom)
  --max-results N             Max jobs per keyword (default: 50)
  --sources SOURCE [...]      nhs  reed  indeed  (default: all three)
  --since DAYS                Only jobs posted in the last N days

Output:
  --format FORMAT [...]       json  csv  excel  sqlite  (default: json)
  --output-dir PATH           Where to save files (default: ./output)
  --dry-run                   Print a JSON preview, don't save anything

Enrichment:
  --no-enrich                 Skip contact lookup (phone/email) — faster
  --ai                        Enable AI fallback for missing contacts
  --ai-provider PROVIDER      ollama (free, local)  or  anthropic (paid)

Other:
  --resume                    Skip jobs already saved in a previous run
  --headful                   Show the browser when scraping Indeed (debug)
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
    "enrichment_sources": ["website", "companies_house", "cqc"]
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
| `contact.confidence_score` | 0–100: how reliable the contact data is |
| `contact.ai_used` | `true` if AI was used to find this contact |
| `contact.enrichment_sources` | Which enrichers found data: `website`, `companies_house`, `cqc`, `charities`, `duckduckgo`, `ai` |
| `_hash` | Deduplication fingerprint (title + company + location) |

---

## How Contact Enrichment Works

For each unique company, the scraper tries these sources in order, stopping when it has a phone **and** email:

1. **Company website** — scrapes `/contact`, `/about`, `/team` pages
2. **Companies House** — UK registered address + directors (free, no key needed)
3. **Charity Commission** — phone/email for hospices and care charities
4. **CQC open data** — Care Quality Commission registry (care homes, nursing homes)
5. **DuckDuckGo** — searches `"Company Name" contact phone email UK`
6. **AI fallback** — Ollama or Anthropic (only if `--ai` flag is set)

---

## Saving Other Formats

```bash
# JSON only (default)
python main.py

# JSON + Excel (3-sheet workbook: Jobs, Contacts, Summary)
python main.py --format json excel

# All four formats
python main.py --format json csv excel sqlite

# SQLite only (persistent, tracks run history)
python main.py --format sqlite
```

---

## Environment Variables (`.env`)

Copy `.env.example` to `.env` and edit as needed. All are optional.

```env
# Override search defaults
MAX_RESULTS_PER_KEYWORD=50
REQUEST_DELAY_MIN=2.0

# AI contact lookup (only needed with --ai)
AI_FALLBACK_ENABLED=false
AI_PROVIDER=ollama                   # or: anthropic
AI_MODEL=llama3.2                    # Ollama model name
ANTHROPIC_API_KEY=                   # only for ai_provider=anthropic
OLLAMA_BASE_URL=http://localhost:11434

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
├── pipeline.py        ← orchestrates all stages
├── config.py          ← all settings
├── scheduler.py       ← cron wrapper
├── requirements.txt
├── .env.example       ← copy to .env
│
├── scrapers/
│   ├── nhs.py         ← NHS Jobs REST API
│   ├── reed.py        ← Reed.co.uk (JSON-LD)
│   └── indeed.py      ← Indeed UK (Playwright)
│
├── enrichers/
│   ├── orchestrator.py   ← runs enrichers in order
│   ├── website.py        ← scrapes company website
│   ├── companies_house.py
│   ├── charities.py
│   ├── cqc.py
│   ├── duckduckgo.py
│   └── ai_enricher.py    ← optional AI fallback
│
├── processing/
│   ├── dedup.py       ← 3-level deduplication
│   ├── cleaner.py     ← phone/email/salary normalisation
│   └── merger.py      ← merges enricher results
│
├── exporters/
│   ├── json_export.py
│   ├── csv_export.py
│   ├── excel_export.py   ← 3-sheet workbook
│   └── sqlite_export.py  ← persistent store
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

**Reed returns 403**
Reed uses bot detection. The scraper automatically warms up the session (visits the homepage first) and retries. If it persists, add a delay by setting `REQUEST_DELAY_MIN=5` in `.env`.

**NHS: "Expecting value: line 1 column 1" / empty response**
The scraper now logs the actual response content when this happens. Common causes:
- Missing `Accept: application/json` header (fixed in current code)
- The NHS API is temporarily down — check [api.jobs.nhs.uk](https://api.jobs.nhs.uk) status
- Your IP is on a blocklist — try running from a residential internet connection

**"No jobs collected"**
Use `--verbose` to see per-request details. The scraper logs the HTTP status and response preview for every failed request, which will identify the cause.

**Indeed CAPTCHA / blocked**
Run with `--headful` to see the browser and solve the CAPTCHA manually once. After that the session cookie may work for several hours.

**Phone numbers look wrong**
The cleaner rejects numbers that look like SVG coordinates or years. If valid numbers are being filtered, check the raw `description` field in the JSON and report the pattern.

**AI enrichment returning nulls**
Make sure Ollama is running: `ollama serve` and `ollama pull llama3.2`. Use `--verbose` to see the raw AI response.
