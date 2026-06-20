#!/usr/bin/env python3
"""Local receiver for the browser-extension scraper.

Runs a small HTTP server (stdlib only — no Flask/FastAPI) that the Chrome
extension POSTs real-browser-scraped jobs to. Jobs accumulate in an inbox
JSON file (deduped by source+job_id), then the full pipeline can be run over
them — dedup → clean → mine → enrich → export — exactly like a normal scrape,
but without tripping Indeed's bot detection.

Usage:
    python receiver.py                       # listen on 127.0.0.1:8765
    python receiver.py --port 8765 --ai      # enrich with the AI fallback on /api/run
    python receiver.py --no-enrich           # skip contact enrichment on /api/run

Endpoints (all return permissive CORS headers — localhost only):
    POST /api/jobs    body: {"jobs":[...]} or [...]  -> merge into the inbox
    POST /api/run                                    -> run the pipeline on the inbox
    GET  /api/status                                 -> inbox count + last run summary
"""

import argparse
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Refuse request bodies above this size — the inbox is meant for browsing
# sessions' worth of job cards, not arbitrary uploads.
MAX_BODY_BYTES = 25 * 1024 * 1024

# Origins allowed to call this server. It binds to localhost, but any web
# page in the browser can still issue a same-machine fetch() to it, so CORS
# must not be left wide open ("*") — restrict to the extension itself and
# the loopback origins the popup/receiver normally run from.
_ALLOWED_ORIGIN_PREFIXES = ("http://localhost:", "http://127.0.0.1:")

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from utils.logger import setup_logger

logger = logging.getLogger(__name__)

# Process-wide state, guarded by a lock since ThreadingHTTPServer handles
# each request on its own thread.
_state_lock = threading.Lock()
_run_lock = threading.Lock()
_last_run: dict = {}
_config: Config = None


def _inbox_path() -> Path:
    return Path(_config.extension_inbox)


def _load_inbox() -> list:
    path = _inbox_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("jobs", []) if isinstance(data, dict) else (data or [])
    except Exception as e:
        logger.warning(f"Could not read inbox {path}: {e}")
        return []


def _save_inbox(jobs: list) -> None:
    path = _inbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file and rename into place so a crash mid-write (or a
    # concurrent reader) never sees a truncated/corrupt inbox file.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"jobs": jobs}, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _merge_jobs(incoming: list) -> tuple[int, int]:
    """Merge incoming jobs into the inbox, deduped by (source, job_id).

    Returns (received, total_in_inbox). Later sends for the same key replace
    the earlier entry, so re-captures during auto-scroll just refresh the job.
    """
    with _state_lock:
        existing = _load_inbox()
        by_key = {(j.get("source"), j.get("job_id")): j for j in existing}
        received = 0
        for job in incoming:
            if not isinstance(job, dict):
                continue
            key = (job.get("source"), job.get("job_id"))
            if not key[1]:
                continue
            by_key[key] = job
            received += 1
        merged = list(by_key.values())
        _save_inbox(merged)
        return received, len(merged)


def _run_pipeline_on_inbox() -> dict:
    """Run the full pipeline over the current inbox. Serialised by _run_lock."""
    from pipeline import import_and_run

    with _run_lock:
        result = import_and_run(_config, str(_inbox_path()))
        summary = {
            "run_id": result.get("run_id"),
            "jobs": len(result.get("jobs", [])),
            "contacts": len(result.get("contacts", {})),
            "output_files": result.get("output_files", []),
            "errors": result.get("errors", 0),
        }
        with _state_lock:
            global _last_run
            _last_run = summary
        return summary


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.debug("HTTP " + fmt % args)

    def _allowed_origin(self):
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://") or origin.startswith(_ALLOWED_ORIGIN_PREFIXES):
            return origin
        return None

    def _cors(self):
        origin = self._allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return None
        if length > MAX_BODY_BYTES:
            raise ValueError(f"request body too large ({length} bytes, max {MAX_BODY_BYTES})")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.rstrip("/") == "/api/status":
            with _state_lock:
                inbox = _load_inbox()
                per_source = {}
                for j in inbox:
                    per_source[j.get("source")] = per_source.get(j.get("source"), 0) + 1
                self._send(200, {
                    "inbox_total": len(inbox),
                    "per_source": per_source,
                    "last_run": _last_run,
                })
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.rstrip("/")
        try:
            if path == "/api/jobs":
                data = self._read_json()
                incoming = data.get("jobs", []) if isinstance(data, dict) else (data or [])
                if not isinstance(incoming, list):
                    self._send(400, {"error": "expected a list of jobs or {\"jobs\":[...]}"})
                    return
                received, total = _merge_jobs(incoming)
                logger.info(f"/api/jobs: received {received}, inbox now {total}")
                self._send(200, {"received": received, "total_in_inbox": total})
                return

            if path == "/api/run":
                logger.info("/api/run: starting pipeline over inbox")
                summary = _run_pipeline_on_inbox()
                logger.info(f"/api/run: done — {summary}")
                self._send(200, summary)
                return

            self._send(404, {"error": "not found"})
        except json.JSONDecodeError as e:
            self._send(400, {"error": f"invalid JSON: {e}"})
        except ValueError as e:
            self._send(413, {"error": str(e)})
        except Exception as e:
            logger.error(f"Request failed: {e}", exc_info=True)
            self._send(500, {"error": str(e)})


def main():
    parser = argparse.ArgumentParser(description="Browser-extension job receiver")
    parser.add_argument("--host", default=None, help="Bind host (default from config)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default 8765)")
    parser.add_argument("--ai", action="store_true", help="Enable AI fallback on /api/run")
    parser.add_argument("--no-enrich", action="store_true", help="Skip contact enrichment on /api/run")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    global _config
    _config = Config()
    if args.ai:
        _config.ai_fallback_enabled = True
    if args.no_enrich:
        _config.enrich_contacts = False

    host = args.host or _config.receiver_host
    port = args.port or _config.receiver_port

    setup_logger(verbose=args.verbose, log_dir=str(Path(_config.output_dir) / "logs"))
    Path(_config.output_dir).mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"\n  Browser-extension receiver listening on {url}")
    print(f"  Inbox file: {_config.extension_inbox}")
    print(f"  Load the extension (chrome://extensions → Load unpacked → extension/),")
    print(f"  browse Indeed/Reed, then POST to {url}/api/jobs or click 'Run pipeline'.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down receiver.")
        server.shutdown()


if __name__ == "__main__":
    main()
