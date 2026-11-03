import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from .config import schedule_slot
from .database import Database
from .normalize import matches_filters, normalize, utcnow
from .sources import FeedClient

LOG = logging.getLogger("jobharvest")


class AlreadyRunning(Exception):
    pass


def request_budget(db, url):
    if urlsplit(url).hostname != "remotive.com":
        return
    now = datetime.now(timezone.utc)
    previous = json.loads(db.state("remotive_http_requests") or "[]")
    recent = [t for t in previous if now - datetime.fromisoformat(t) < timedelta(hours=24)]
    if len(recent) >= 4:
        raise ValueError("Remotive's four-request rolling 24-hour budget has been reached, including retries")
    db.set_state("remotive_http_requests", json.dumps(recent + [now.isoformat()]))


@contextmanager
def collection_lock(path):
    # The OS releases this lock on process exit, including abrupt termination.
    handle = open(path + ".lock", "a+b")
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise AlreadyRunning("A collection is already running") from exc
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def collect(config, due=False, client=None, progress=None):
    db = Database(config["_database"])
    with collection_lock(db.path):
        slot = schedule_slot(config) if due else None
        if due and db.state("last_schedule_slot") == slot:
            return {"status": "not_due", "message": "Daily collection already attempted for this schedule slot"}
        enabled = [s for s in config["sources"] if s.get("enabled", True)]
        if not enabled:
            raise ValueError("No enabled sources")
        with db.connect() as conn:
            conn.execute("UPDATE ingestion_runs SET status='interrupted',finished_at=? WHERE status='running'", (utcnow(),))
            conn.execute("UPDATE source_runs SET status='interrupted',finished_at=? WHERE status='running'", (utcnow(),))
            run_id = conn.execute("INSERT INTO ingestion_runs(started_at,status,trigger,schedule_slot) VALUES (?,'running',?,?)", (utcnow(), "daily" if due else "manual", slot)).lastrowid
        client = client or FeedClient(config["collection"], before_request=lambda url: request_budget(db, url), progress=progress)
        summaries = []
        for source in enabled:
            name = source["name"]
            count = {key: 0 for key in ["fetched", "inserted", "updated", "unchanged", "filtered", "invalid", "closed"]}
            error = None
            now = utcnow()
            source_id = None
            try:
                previous = db.state("attempt:" + name)
                interval = timedelta(hours=source.get("min_interval_hours", 1))
                if previous and datetime.now(timezone.utc) - datetime.fromisoformat(previous) < interval:
                    status = "skipped"
                    error = "Source polling interval has not elapsed"
                    with db.connect() as conn:
                        source_id = conn.execute("INSERT INTO source_runs(run_id,source,started_at,status) VALUES (?,?,?,?)", (run_id, name, now, status)).lastrowid
                else:
                    with db.connect() as conn:
                        source_id = conn.execute("INSERT INTO source_runs(run_id,source,started_at,status) VALUES (?,?,?,'running')", (run_id, name, now)).lastrowid
                    db.set_state("attempt:" + name, now)
                    LOG.info("Collecting %s", name)
                    if progress:
                        progress("Collecting " + (source.get("company") or name) + "…")
                    fetch_source = dict(source)
                    if source["type"] in {"google", "meta"}:
                        fetch_source["_cursor"] = db.state("cursor:" + name) or ""
                    snapshot = client.fetch(fetch_source)
                    count["fetched"] = len(snapshot.jobs)
                    jobs, seen, invalid = [], set(), []
                    for record in snapshot.jobs:
                        try:
                            job = normalize(record, name)
                        except (ValueError, TypeError, OverflowError) as exc:
                            count["invalid"] += 1
                            invalid.append((str(exc), record))
                            continue
                        seen.add(job["external_id"])
                        if matches_filters(job, config.get("filters", {})):
                            jobs.append(job)
                        else:
                            count["filtered"] += 1
                    status = "success" if snapshot.complete and not count["invalid"] else "partial"
                    notes = [snapshot.note] if snapshot.note else []
                    if invalid:
                        notes.append(f"{len(invalid)} invalid jobs: {invalid[0][0]}")
                    filters = config.get("filters", {})
                    filtered_snapshot = bool(filters.get("roles") or filters.get("locations") or filters.get("remote_only"))
                    if filtered_snapshot:
                        notes.append("Collection filters enabled; closure detection disabled")
                    with db.connect() as conn:
                        observed = utcnow()
                        for reason, raw in invalid:
                            conn.execute("INSERT INTO rejected_jobs(source_run_id,source,reason,raw_json) VALUES (?,?,?,?)", (source_id, name, reason, json.dumps(raw, ensure_ascii=False)))
                        for job in jobs:
                            count[db.upsert(conn, job, observed)] += 1
                        if status == "success" and not filtered_snapshot:
                            count["closed"] = db.reconcile(conn, name, seen, config["collection"]["close_after_missing_runs"])
                        if snapshot.cursor is not None:
                            conn.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("cursor:" + name, snapshot.cursor))
                    error = "; ".join(notes) or None
            except Exception as exc:
                status = "failed"
                error = f"{type(exc).__name__}: {exc}"[:2000]
                LOG.exception("Source %s failed", name)
                # Transactional imports ensure a broken source never partially changes its jobs.
                count["inserted"] = count["updated"] = count["unchanged"] = count["closed"] = 0
            with db.connect() as conn:
                if source_id is None:
                    source_id = conn.execute("INSERT INTO source_runs(run_id,source,started_at,status) VALUES (?,?,?,?)", (run_id, name, now, status)).lastrowid
                conn.execute("UPDATE source_runs SET finished_at=?,status=?,fetched=?,inserted=?,updated=?,unchanged=?,filtered=?,invalid=?,closed=?,error=? WHERE id=?", (utcnow(), status, *count.values(), error, source_id))
            summary = {"source": name, "status": status, **count, "note": error}
            summaries.append(summary)
            LOG.info("%s", json.dumps(summary))
        states = [s["status"] for s in summaries]
        status = "failed" if all(s == "failed" for s in states) else ("partial" if any(s in {"failed", "partial"} for s in states) else ("skipped" if all(s == "skipped" for s in states) else "success"))
        result = {"run_id": run_id, "status": status, "sources": summaries}
        with db.connect() as conn:
            conn.execute("UPDATE ingestion_runs SET finished_at=?,status=?,summary_json=? WHERE id=?", (utcnow(), status, json.dumps(result), run_id))
            if due:
                # One scheduled attempt per day. Manual retries remain possible after cooldowns.
                conn.execute("INSERT INTO state VALUES ('last_schedule_slot',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (slot,))
        return result
