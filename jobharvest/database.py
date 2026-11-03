import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .normalize import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, content_hash TEXT NOT NULL,
 role TEXT NOT NULL, domain TEXT NOT NULL, domain_inferred INTEGER NOT NULL,
 description TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL, country TEXT,
 experience_level TEXT NOT NULL, experience_inferred INTEGER NOT NULL,
 experience_min_years INTEGER, experience_max_years INTEGER, experience_evidence TEXT,
 workplace_type TEXT NOT NULL, employment_type TEXT NOT NULL, salary TEXT, published_at TEXT,
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS jobs_active_recent ON jobs(active, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS jobs_location ON jobs(location);
CREATE INDEX IF NOT EXISTS jobs_domain ON jobs(domain);
CREATE INDEX IF NOT EXISTS jobs_stats ON jobs(active,workplace_type,company);
CREATE INDEX IF NOT EXISTS jobs_match_titles ON jobs(active,role,id);
CREATE INDEX IF NOT EXISTS jobs_remote_titles ON jobs(active,workplace_type,role);
CREATE TABLE IF NOT EXISTS listings (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 source TEXT NOT NULL, external_id TEXT NOT NULL, url TEXT NOT NULL, raw_json TEXT NOT NULL,
 content_hash TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, missing_runs INTEGER NOT NULL DEFAULT 0,
 UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS listings_job ON listings(job_id);
CREATE INDEX IF NOT EXISTS listings_url ON listings(url);
CREATE TABLE IF NOT EXISTS skills (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS job_skills (
 job_id INTEGER NOT NULL REFERENCES jobs(id), skill_id INTEGER NOT NULL REFERENCES skills(id),
 inferred INTEGER NOT NULL, PRIMARY KEY(job_id, skill_id)
);
CREATE TABLE IF NOT EXISTS job_versions (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
 changed_at TEXT NOT NULL, data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestion_runs (
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, trigger TEXT NOT NULL, schedule_slot TEXT, summary_json TEXT
);
CREATE TABLE IF NOT EXISTS source_runs (
 id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
 source TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, fetched INTEGER NOT NULL DEFAULT 0,
 inserted INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL DEFAULT 0,
 unchanged INTEGER NOT NULL DEFAULT 0, filtered INTEGER NOT NULL DEFAULT 0,
 invalid INTEGER NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE INDEX IF NOT EXISTS source_runs_recent ON source_runs(source, started_at DESC);
CREATE TABLE IF NOT EXISTS rejected_jobs (
 id INTEGER PRIMARY KEY, source_run_id INTEGER NOT NULL REFERENCES source_runs(id),
 source TEXT NOT NULL, reason TEXT NOT NULL, raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job_preferences (
 job_id INTEGER PRIMARY KEY REFERENCES jobs(id), status TEXT NOT NULL,
 notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
PRAGMA user_version = 1;
"""

JOB_FIELDS = ["fingerprint", "content_hash", "role", "domain", "domain_inferred", "description", "company", "location", "country", "experience_level", "experience_inferred", "experience_min_years", "experience_max_years", "experience_evidence", "workplace_type", "employment_type", "salary", "published_at"]


class Database:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def upsert(self, conn, job, now):
        listing = conn.execute("SELECT * FROM listings WHERE source=? AND external_id=?", (job["source"], job["external_id"])).fetchone()
        if listing:
            job_id = listing["job_id"]
        else:
            match = conn.execute("SELECT job_id FROM listings WHERE url=? LIMIT 1", (job["url"],)).fetchone()
            if not match:
                match = conn.execute("SELECT id AS job_id FROM jobs WHERE fingerprint=? LIMIT 1", (job["fingerprint"],)).fetchone()
            job_id = match["job_id"] if match else None
        outcome = "unchanged"
        if job_id is None:
            columns = JOB_FIELDS + ["first_seen_at", "last_seen_at", "updated_at"]
            job_id = conn.execute(f"INSERT INTO jobs ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [job[k] for k in JOB_FIELDS] + [now] * 3).lastrowid
            outcome = "inserted"
        else:
            old = conn.execute("SELECT content_hash FROM jobs WHERE id=?", (job_id,)).fetchone()
            if old["content_hash"] != job["content_hash"]:
                conn.execute(f"UPDATE jobs SET {','.join(k+'=?' for k in JOB_FIELDS)},updated_at=? WHERE id=?", [job[k] for k in JOB_FIELDS] + [now, job_id])
                outcome = "updated"
            conn.execute("UPDATE jobs SET last_seen_at=?,active=1 WHERE id=?", (now, job_id))
        if outcome != "unchanged":
            conn.execute("INSERT INTO job_versions(job_id,changed_at,data_json) VALUES (?,?,?)", (job_id, now, json.dumps({k: v for k, v in job.items() if k != "raw"}, ensure_ascii=False)))
            conn.execute("DELETE FROM job_skills WHERE job_id=?", (job_id,))
            for name in job["skills"]:
                conn.execute("INSERT OR IGNORE INTO skills(name) VALUES (?)", (name,))
                skill_id = conn.execute("SELECT id FROM skills WHERE name=?", (name,)).fetchone()[0]
                conn.execute("INSERT INTO job_skills VALUES (?,?,?)", (job_id, skill_id, int(name not in job["explicit_skills"])))
        conn.execute("""INSERT INTO listings(job_id,source,external_id,url,raw_json,content_hash,first_seen_at,last_seen_at)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(source,external_id) DO UPDATE SET
            url=excluded.url,raw_json=excluded.raw_json,content_hash=excluded.content_hash,
            last_seen_at=excluded.last_seen_at,active=1,missing_runs=0""", (job_id, job["source"], job["external_id"], job["url"], json.dumps(job["raw"], ensure_ascii=False), job["content_hash"], now, now))
        return outcome

    def reconcile(self, conn, source, seen_ids, threshold):
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS seen_ids (id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM seen_ids")
        conn.executemany("INSERT OR IGNORE INTO seen_ids VALUES (?)", ((i,) for i in seen_ids))
        conn.execute("UPDATE listings SET missing_runs=missing_runs+1 WHERE source=? AND active=1 AND external_id NOT IN (SELECT id FROM seen_ids)", (source,))
        count = conn.execute("UPDATE listings SET active=0 WHERE source=? AND active=1 AND missing_runs>=?", (source, threshold)).rowcount
        conn.execute("UPDATE jobs SET active=EXISTS(SELECT 1 FROM listings l WHERE l.job_id=jobs.id AND l.active=1)")
        return count

    def state(self, key):
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def set_state(self, key, value):
        with self.connect() as conn:
            conn.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    @staticmethod
    def where(params):
        clauses, args = [], []
        active = params.get("active", "true")
        if active in {"true", "false"}:
            clauses.append("j.active=?")
            args.append(int(active == "true"))
        if params.get("q"):
            clauses.append("(j.role LIKE ? ESCAPE '\\' OR j.company LIKE ? ESCAPE '\\' OR j.description LIKE ? ESCAPE '\\')")
            term = "%" + escape_like(params["q"]) + "%"
            args.extend([term] * 3)
        for key, column in [("location", "location"), ("company", "company"), ("domain", "domain"), ("level", "experience_level")]:
            if params.get(key):
                clauses.append(f"j.{column} LIKE ? ESCAPE '\\'")
                args.append("%" + escape_like(params[key]) + "%")
        if params.get("remote") == "true":
            clauses.append("j.workplace_type='remote'")
        if params.get("skill"):
            clauses.append("EXISTS(SELECT 1 FROM job_skills js JOIN skills s ON s.id=js.skill_id WHERE js.job_id=j.id AND s.name LIKE ? ESCAPE '\\')")
            args.append("%" + escape_like(params["skill"]) + "%")
        if params.get("source"):
            clauses.append("EXISTS(SELECT 1 FROM listings l WHERE l.job_id=j.id AND l.source=?)")
            args.append(params["source"])
        if params.get("saved") == "true":
            clauses.append("EXISTS(SELECT 1 FROM job_preferences p WHERE p.job_id=j.id AND p.status='saved')")
        if params.get("exclude_hidden") == "true":
            clauses.append("NOT EXISTS(SELECT 1 FROM job_preferences p WHERE p.job_id=j.id AND p.status='hidden')")
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), args

    def enrich(self, conn, row, detail=False):
        job = dict(row)
        preference = conn.execute("SELECT status,notes FROM job_preferences WHERE job_id=?", (job["id"],)).fetchone()
        job["preference"] = dict(preference) if preference else {"status": "none", "notes": ""}
        job["skills"] = [dict(s) for s in conn.execute("SELECT s.name,js.inferred FROM job_skills js JOIN skills s ON js.skill_id=s.id WHERE js.job_id=? ORDER BY s.name", (job["id"],))]
        job["sources"] = [dict(s) for s in conn.execute("SELECT source,url,external_id,active,last_seen_at,missing_runs FROM listings WHERE job_id=? ORDER BY active DESC,source", (job["id"],))]
        if detail:
            job["version_count"] = conn.execute("SELECT COUNT(*) FROM job_versions WHERE job_id=?", (job["id"],)).fetchone()[0]
        else:
            job.pop("description", None)
            job.pop("content_hash", None)
            job.pop("fingerprint", None)
        return job

    def search(self, params=None, limit=30, offset=0, detail=False):
        where, args = self.where(params or {})
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM jobs j" + where, args).fetchone()[0]
            rows = conn.execute("SELECT j.* FROM jobs j" + where + " ORDER BY j.first_seen_at DESC,j.id DESC LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
            return {"total": total, "limit": limit, "offset": offset, "jobs": [self.enrich(conn, row, detail) for row in rows]}

    def set_preference(self, job_id, status, notes=""):
        if status not in {"saved", "hidden", "none"}:
            raise ValueError("Choose saved, hidden or none")
        if not isinstance(notes, str) or len(notes) > 4000:
            raise ValueError("Notes must be at most 4,000 characters")
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
                raise ValueError("Job not found")
            conn.execute("INSERT INTO job_preferences VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,notes=excluded.notes,updated_at=excluded.updated_at", (job_id, status, notes, utcnow()))
        return {"status": status, "notes": notes}

    def matching_candidates(self, params, accept_title):
        """Filter titles first, then load relevant JDs and relationships in batches."""
        where, args = self.where(params)
        with self.connect() as conn:
            ids = [r["id"] for r in conn.execute("SELECT j.id,j.role FROM jobs j" + where, args) if accept_title(r["role"])]
            jobs = []
            for start in range(0, len(ids), 400):
                batch = ids[start:start+400]
                placeholders = ",".join("?" for _ in batch)
                records = {r["id"]: dict(r) for r in conn.execute(f"SELECT * FROM jobs WHERE id IN ({placeholders})", batch)}
                for job in records.values():
                    job.update(skills=[], sources=[], preference={"status": "none", "notes": ""})
                for r in conn.execute(f"SELECT js.job_id,s.name,js.inferred FROM job_skills js JOIN skills s ON js.skill_id=s.id WHERE js.job_id IN ({placeholders}) ORDER BY s.name", batch):
                    records[r["job_id"]]["skills"].append({"name": r["name"], "inferred": r["inferred"]})
                for r in conn.execute(f"SELECT job_id,source,url,external_id,active,last_seen_at,missing_runs FROM listings WHERE job_id IN ({placeholders}) ORDER BY active DESC,source", batch):
                    row = dict(r)
                    records[row.pop("job_id")]["sources"].append(row)
                for r in conn.execute(f"SELECT job_id,status,notes FROM job_preferences WHERE job_id IN ({placeholders})", batch):
                    records[r["job_id"]]["preference"] = {"status": r["status"], "notes": r["notes"]}
                jobs.extend(records.values())
            return jobs

    def job(self, job_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self.enrich(conn, row, True) if row else None

    def runs(self):
        with self.connect() as conn:
            runs = [dict(r) for r in conn.execute("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT 20")]
            for run in runs:
                run["sources"] = [dict(s) for s in conn.execute("SELECT * FROM source_runs WHERE run_id=? ORDER BY id", (run["id"],))]
                run["summary"] = json.loads(run.pop("summary_json") or "{}")
            return runs

    def stats(self):
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total,COALESCE(SUM(active),0) AS active,COALESCE(SUM(active AND workplace_type='remote'),0) AS remote,COUNT(DISTINCT company) AS companies FROM jobs").fetchone()
            result = dict(row)
            result["sources"] = [dict(s) for s in conn.execute("SELECT source,COUNT(*) AS total,SUM(active) AS active FROM listings GROUP BY source")]
            result["source_health"] = {r["source"]: dict(r) for r in conn.execute("SELECT sr.source,sr.status,sr.started_at,sr.finished_at,sr.fetched,sr.inserted,sr.error FROM source_runs sr JOIN (SELECT source,MAX(id) AS id FROM source_runs WHERE status!='skipped' GROUP BY source) latest ON latest.id=sr.id")}
            result["domains"] = [r[0] for r in conn.execute("SELECT DISTINCT domain FROM jobs ORDER BY domain")]
            result["levels"] = [r[0] for r in conn.execute("SELECT DISTINCT experience_level FROM jobs ORDER BY experience_level")]
            result["last_run"] = dict(r) if (r := conn.execute("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT 1").fetchone()) else None
            return result

    def backup(self, destination):
        target = Path(destination).resolve()
        if str(target) == self.path:
            raise ValueError("Backup must have a different path")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source:
            output = sqlite3.connect(target)
            try:
                source.backup(output)
            finally:
                output.close()


def escape_like(value):
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
