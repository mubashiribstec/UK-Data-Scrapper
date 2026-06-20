# UK Data Scrapper — Browser Capture extension

Captures Indeed UK and Reed.co.uk job listings from your **real, logged-in
browser session** — no CAPTCHA, full job descriptions — and feeds them into
the UK-Data-Scrapper Python pipeline (dedup, contact mining, enrichment,
export), exactly like a normal scrape.

## Install (Chrome / Edge / Brave — Manifest V3)

1. Go to `chrome://extensions`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** and select this `extension/` folder
4. Pin the extension so you can see its badge (captured-job count)

## Use

1. Browse `uk.indeed.com` or `www.reed.co.uk` and search for jobs normally —
   scroll the results, open a few job pages. The extension captures jobs
   automatically in the background; the toolbar badge shows the running
   count.
2. Click the extension icon to open the popup:
   - **Download JSON** — saves a file you can run directly:
     ```bash
     python main.py --import-json ~/Downloads/extension_inbox_*.json --ai
     ```
     Works fully offline — no server needed.
   - **Auto-send to receiver as I browse** — toggle on, with the receiver
     running (see below), to stream jobs there live instead of downloading.
   - **Send all to receiver now** / **Run pipeline now** — manual triggers
     against the receiver.
   - **Clear captured jobs** — empties the local store.

## Local receiver (for live capture)

From the repo root:

```bash
python receiver.py --port 8765 --ai     # --ai enables AI contact fallback on /api/run
```

It listens on `http://127.0.0.1:8765` and accepts:
- `POST /api/jobs` — merge captured jobs into `output/extension_inbox.json`
- `POST /api/run` — run the full pipeline over the current inbox
- `GET /api/status` — inbox count + last run summary

Point the popup's receiver URL at this address (default already matches) and
enable **Auto-send**.

## Notes

- Jobs are tagged `source: "indeed"` / `"reed"` — they dedup and merge with
  jobs from the existing Reed-API/Playwright scrapers via the normal
  cross-source content-hash dedup, not treated as a separate source.
- Indeed extraction reads the same embedded `window.mosaic.providerData`
  structure the Python scraper parses server-side, plus a DOM fallback and
  full job-page description capture.
- Reed extraction reads the page's `JobPosting` JSON-LD block (schema.org),
  plus a DOM fallback for search-result cards.
- All capture happens client-side in your browser; nothing is sent anywhere
  except to the receiver you point it at (default: your own machine).
