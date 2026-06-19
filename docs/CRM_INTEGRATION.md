# CRM Integration Guide — Laravel + MariaDB

This scraper can write directly into a MySQL/MariaDB database so a Laravel CRM
can read scraped jobs and contacts without an intermediate import step.

## 1. Configure the connection

Add these to your `.env` (never commit real credentials):

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=your_crm_db
MYSQL_USER=scraper_user
MYSQL_PASSWORD=your_password
```

Install the driver (already in `requirements.txt`):

```bash
pip install -r requirements.txt   # pulls in pymysql
```

## 2. Run with the MySQL exporter

```bash
python main.py --ai --format mysql           # MySQL only
python main.py --ai --format json mysql      # JSON file + MySQL together
```

`exporters/mysql_export.py` creates the tables below on first run (`CREATE TABLE IF NOT EXISTS`)
and upserts on every later run (`INSERT ... ON DUPLICATE KEY UPDATE`), so re-running the
scraper updates existing rows instead of duplicating them.

## 3. Database schema

### `jobs`

| Column | Type | Notes |
|---|---|---|
| `job_id`, `source` | VARCHAR | Composite primary key |
| `title`, `company`, `company_url` | VARCHAR | |
| `location`, `location_city`, `location_postcode` | VARCHAR | |
| `salary_text`, `salary_min`, `salary_max`, `salary_period` | VARCHAR/DOUBLE | |
| `job_type`, `posted_at`, `expires_at`, `apply_url` | VARCHAR | |
| `description` | LONGTEXT | |
| `requirements`, `benefits`, `sources` | JSON | Lists |
| `field_sources` | JSON | Per-field provenance — see §4 |
| `job_hash` | VARCHAR | Dedup fingerprint (`_hash` in JSON export) |
| `scraped_at`, `run_id` | VARCHAR | |

### `contacts`

| Column | Type | Notes |
|---|---|---|
| `company` | VARCHAR | Primary key |
| `phone_numbers`, `emails` | JSON | Lists |
| `contact_person`, `address`, `website`, `company_number`, `company_type` | VARCHAR | |
| `confidence_score` | INT | 0–100 |
| `ai_used` | TINYINT(1) | |
| `enrichment_sources` | JSON | Which enrichers contributed, e.g. `["website", "gemini"]` |
| `field_sources` | JSON | Per-field provenance — see §4 |
| `enriched_at` | VARCHAR | |

### `runs`

One row per scraper run: `run_id`, `started_at`, `finished_at`, `jobs_scraped`,
`jobs_duplicate`, `companies_enriched`, `ai_calls_made`, `errors`.

## 4. Gemini provenance — knowing what came from AI

Every field the Gemini API fills in is tagged, so the CRM can distinguish
scraped-from-source data from AI-completed data:

- `jobs.field_sources[field] == "gemini_description"` — that job field (e.g.
  `requirements`, `benefits`) was extracted from the description by Gemini.
- `contacts.field_sources[field] == "gemini"` — that contact field (e.g.
  `contact_person`, `address`, `website`) was filled in by Gemini's
  company-lookup fallback.
- `contacts.enrichment_sources` contains `"gemini"` whenever Gemini contributed
  any part of that contact record; `contacts.ai_used = true` whenever any AI
  provider was used at all (Gemini, or a fallback provider if configured).

If a different AI provider answers instead of Gemini (e.g. Ollama as a
fallback), the same fields are tagged with that provider's name (e.g.
`"ollama"`, `"ollama_description"`) instead of `"gemini"`.

### Querying from Laravel / Eloquent

```php
// Contacts where Gemini contributed any data
Contact::whereJsonContains('enrichment_sources', 'gemini')->get();

// Jobs whose requirements were filled in by Gemini (not scraped directly)
Job::whereJsonContains('field_sources->requirements', 'gemini_description')->get();
```

`whereJsonContains` works on MySQL/MariaDB JSON columns directly — no extra
casting needed since the columns are declared `JSON`.

## 5. Sample Laravel migration

```php
<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('jobs', function (Blueprint $table) {
            $table->string('job_id');
            $table->string('source', 64);
            $table->string('title', 512)->nullable();
            $table->string('company', 512)->nullable();
            $table->string('company_url', 1024)->nullable();
            $table->string('location', 512)->nullable();
            $table->string('location_city')->nullable();
            $table->string('location_postcode', 32)->nullable();
            $table->string('salary_text')->nullable();
            $table->double('salary_min')->nullable();
            $table->double('salary_max')->nullable();
            $table->string('salary_period', 32)->nullable();
            $table->string('job_type', 128)->nullable();
            $table->string('posted_at', 64)->nullable();
            $table->string('expires_at', 64)->nullable();
            $table->string('apply_url', 1024)->nullable();
            $table->longText('description')->nullable();
            $table->json('requirements')->nullable();
            $table->json('benefits')->nullable();
            $table->json('sources')->nullable();
            $table->json('field_sources')->nullable();
            $table->string('job_hash', 64)->nullable();
            $table->string('scraped_at', 64)->nullable();
            $table->string('run_id', 64)->nullable();
            $table->primary(['job_id', 'source']);
        });

        Schema::create('contacts', function (Blueprint $table) {
            $table->string('company', 512)->primary();
            $table->json('phone_numbers')->nullable();
            $table->json('emails')->nullable();
            $table->string('contact_person', 512)->nullable();
            $table->string('address', 1024)->nullable();
            $table->string('website', 1024)->nullable();
            $table->string('company_number', 64)->nullable();
            $table->string('company_type', 64)->nullable();
            $table->integer('confidence_score')->nullable();
            $table->boolean('ai_used')->default(false);
            $table->json('enrichment_sources')->nullable();
            $table->json('field_sources')->nullable();
            $table->string('enriched_at', 64)->nullable();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('contacts');
        Schema::dropIfExists('jobs');
    }
};
```

The scraper itself creates the tables on first run (so the migration above is
only needed if you'd rather have Laravel own table creation — point
`MYSQL_DATABASE` at the same database either way and skip re-running
`init_db()` by removing the `CREATE TABLE` statements from
`exporters/mysql_export.py` if you do).

## 6. Scheduling recurring CRM syncs

Use the existing cron wrapper, just add `--format mysql` to the pipeline call
in `scheduler.py`'s `run_pipeline(...)`, or run directly via cron:

```bash
# Edit crontab
crontab -e

# Every Monday 6am: scrape, enrich, and push straight into the CRM database
0 6 * * 1 cd /home/ubuntu/UK-Data-Scrapper && python main.py --ai --format mysql --no-enrich=false
```

## Security notes

- Never commit real `MYSQL_PASSWORD` or `GEMINI_API_KEY` values — only put them
  in your local, gitignored `.env`.
- Use a dedicated MySQL user for the scraper with `INSERT`/`UPDATE`/`CREATE`
  privileges scoped to the CRM database, not a root/admin account.
