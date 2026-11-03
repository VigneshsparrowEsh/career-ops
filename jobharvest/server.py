"""Loopback-only dashboard and JSON API."""
import csv
import io
import json
import logging
import secrets
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .collector import AlreadyRunning, collect
from .config import load, next_schedule
from .database import Database
from .company_sites import add_company_sites, parse_sites, starter_sites
from .profile import get_profile, save_profile, root_for
from .matching import matches, score_job
from .career_ops import connection, queue_job

WEB = Path(__file__).resolve().parent / "web"
CSV_FIELDS = ["id", "role", "domain", "domain_inferred", "description", "company", "location", "country", "experience_level", "experience_inferred", "experience_min_years", "experience_max_years", "experience_evidence", "skills", "workplace_type", "employment_type", "salary", "published_at", "first_seen_at", "last_seen_at", "active", "source_urls"]


def csv_text(db, params=None):
    jobs = db.search(params or {"active": "all"}, limit=-1, detail=True)["jobs"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for job in jobs:
        job["skills"] = "; ".join(s["name"] + (" (inferred)" if s["inferred"] else "") for s in job["skills"])
        job["source_urls"] = "; ".join(s["source"] + ": " + s["url"] for s in job["sources"])
        for key, value in job.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                job[key] = "'" + value
        writer.writerow(job)
    return output.getvalue()


def serve(config, port=8766, scheduler=False):
    db = Database(config["_database"])
    root = root_for(config)
    match_cache = OrderedDict()
    match_lock = threading.Lock()

    def ranked_jobs(params, limit, offset):
        profile = get_profile(root)
        filters = {k: v for k, v in params.items() if k not in {"limit", "offset"}}
        files = [Path(db.path), Path(db.path + "-wal")]
        versions = [(p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in files]
        key = json.dumps([filters, profile, versions], sort_keys=True)
        with match_lock:
            cached = match_cache.get(key)
            if not cached or time.monotonic() - cached[0] > 60:
                result = matches(db, profile, filters, limit=1000000)
                match_cache[key] = (time.monotonic(), result)
                match_cache.move_to_end(key)
                while len(match_cache) > 8:
                    match_cache.popitem(last=False)
            else:
                result = cached[1]
            return dict(result, jobs=result["jobs"][offset:offset+limit], limit=limit, offset=offset)
    token = secrets.token_urlsafe(32)
    task_lock = threading.Lock()
    task = {"running": False, "result": None, "progress": ""}
    stopping = threading.Event()

    def start_collection(due=False, sites=None):
        with task_lock:
            if task["running"]:
                return False
            task["running"] = True
            task["result"] = None
            task["progress"] = "Finding company boards…" if sites else "Collecting jobs…"

        def work():
            discovery = None
            def progress(message):
                with task_lock:
                    task["progress"] = message
            try:
                if sites:
                    discovery = add_company_sites(config["_path"], sites, progress=progress)
                    progress("Collecting jobs from company boards…")
                if sites and not discovery["added"]:
                    result = {"status": "skipped", "message": "No new company boards were added."}
                else:
                    result = collect(load(config["_path"]), due=due, progress=progress)
            except AlreadyRunning as exc:
                result = {"status": "busy", "message": str(exc)}
            except Exception as exc:
                logging.exception("Collection failed")
                result = {"status": "failed", "message": str(exc)}
            if discovery is not None:
                result["discovery"] = discovery
                problems = sum(s["status"] != "found" for s in discovery["sites"])
                result["message"] = (f"Added {len(discovery['added'])} company boards; {len(discovery['existing'])} already enabled; "
                                     f"{len(discovery['disabled'])} already configured but disabled; {problems} sites could not be added. "
                                     f"Collection: {result['status']}. Open Company career sites for details.")
            with task_lock:
                task["running"] = False
                task["result"] = result
                task["progress"] = ""
        threading.Thread(target=work, daemon=True).start()
        return True

    class Handler(BaseHTTPRequestHandler):
        def valid_host(self):
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
            if not isinstance(body, (bytes, str)):
                body = json.dumps(body, ensure_ascii=False)
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # A navigation may cancel an in-flight local response.

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {"error": "Local access only"})
            parsed = urlsplit(self.path)
            path = parsed.path
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
                if path in files:
                    name, content_type = files[path]
                    return self.send(200, (WEB / name).read_bytes(), content_type)
                if path == "/api/health":
                    return self.send(200, {"status": "ok"})
                if path == "/api/profile":
                    return self.send(200, {"profile": get_profile(root), "connection": connection(root)})
                if path == "/api/stats":
                    current = load(config["_path"])
                    result = db.stats()
                    with task_lock:
                        result["collector"] = dict(task)
                    result.update({"csrf_token": token, "schedule": current["schedule"], "next_collection": next_schedule(current), "scheduler_in_dashboard": scheduler, "configured_sources": [{key: s[key] for key in ["name", "type", "company", "career_url", "board", "enabled"] if key in s} | {"enabled": s.get("enabled", True)} for s in current["sources"]]})
                    return self.send(200, result)
                if path == "/api/jobs":
                    limit = min(100, max(1, int(params.get("limit", 30))))
                    offset = max(0, int(params.get("offset", 0)))
                    return self.send(200, db.search(params, limit, offset))
                if path == "/api/matches":
                    limit = min(100, max(1, int(params.get("limit", 25))))
                    offset = max(0, int(params.get("offset", 0)))
                    minimum = int(params.get("min_score", 0))
                    if not 0 <= minimum <= 100:
                        raise ValueError("Minimum fit must be from 0 to 100")
                    return self.send(200, ranked_jobs(params, limit, offset))
                if path.startswith("/api/jobs/"):
                    job = db.job(int(path.rsplit("/", 1)[1]))
                    profile = get_profile(root)
                    if job and profile["ready"]:
                        job["match"] = score_job(job, profile)
                    return self.send(200 if job else 404, job or {"error": "Job not found"})
                if path == "/api/runs":
                    return self.send(200, {"runs": db.runs()})
                if path == "/api/export.csv":
                    return self.send(200, "\ufeff" + csv_text(db, params), "text/csv; charset=utf-8", {"Content-Disposition": 'attachment; filename="jobs.csv"'})
                return self.send(404, {"error": "Not found"})
            except (ValueError, TypeError) as exc:
                return self.send(400, {"error": str(exc)})
            except Exception:
                logging.exception("Request failed")
                return self.send(500, {"error": "Request failed; check the application log"})

        def do_POST(self):
            if not self.valid_host():
                return self.send(403, {"error": "Local access only"})
            if self.headers.get("X-JobHarvest-Token") != token:
                return self.send(403, {"error": "Missing or invalid collection token"})
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
                return self.send(403, {"error": "Invalid origin"})
            if self.path not in {"/api/collect", "/api/company-sites", "/api/profile", "/api/preferences", "/api/career-ops/queue"}:
                return self.send(404, {"error": "Not found"})
            sites = None
            if self.path in {"/api/profile", "/api/preferences", "/api/career-ops/queue"}:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 120000:
                        raise ValueError("Request must contain at most 120,000 bytes")
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(body, dict):
                        raise ValueError("Expected a JSON object")
                    if self.path == "/api/profile":
                        return self.send(200, {"profile": save_profile(root, body)})
                    job_id = body.get("job_id")
                    if not isinstance(job_id, int) or isinstance(job_id, bool):
                        raise ValueError("Choose a valid job")
                    if self.path == "/api/preferences":
                        return self.send(200, db.set_preference(job_id, body.get("status"), body.get("notes", "")))
                    job = db.job(job_id)
                    if not job:
                        return self.send(404, {"error": "Job not found"})
                    return self.send(200, queue_job(root, job))
                except (ValueError, TypeError, UnicodeError) as exc:
                    return self.send(400, {"error": str(exc)})
                except Exception:
                    logging.exception("Personalization request failed")
                    return self.send(500, {"error": "Could not save your changes; check the application log"})
            if self.path == "/api/company-sites":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 32000:
                        return self.send(400, {"error": "Request must contain at most 32,000 bytes"})
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(body, dict):
                        raise ValueError("Expected a JSON object")
                    sites = starter_sites() if body.get("starter") is True else parse_sites(body.get("sites"))
                except (ValueError, TypeError, UnicodeError) as exc:
                    return self.send(400, {"error": str(exc)})
            if not start_collection(sites=sites):
                return self.send(409, {"error": "A collection is already running"})
            return self.send(202, {"status": "started"})

        def log_message(self, format, *args):
            logging.getLogger("jobharvest.http").debug(format, *args)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True

    def tick():
        while not stopping.is_set():
            try:
                current = load(config["_path"])
                from .config import schedule_slot
                if db.state("last_schedule_slot") != schedule_slot(current):
                    start_collection(due=True)
            except Exception:
                logging.exception("Scheduler tick failed")
            stopping.wait(30)
    if scheduler:
        threading.Thread(target=tick, daemon=True).start()
    print(f"JobHarvest dashboard: http://127.0.0.1:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set()
        httpd.server_close()
