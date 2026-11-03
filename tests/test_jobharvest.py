import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from jobharvest.collector import AlreadyRunning, collect, collection_lock, request_budget
from jobharvest.config import ROOT, load, next_schedule, schedule_slot
from jobharvest.database import Database
from jobharvest.normalize import normalize, plain
from jobharvest.server import csv_text
from jobharvest.sources import FeedClient, Snapshot


def record(id="one", **changes):
    job = {"external_id": id, "title": "Senior Python Engineer", "company": "Acme", "location": "India", "description": "<p>Python, SQL, Docker and 3-5 years experience.</p>", "url": "https://example.com/jobs/" + id, "remote": True}
    job.update(changes)
    return job


class FakeClient:
    def __init__(self, snapshot=None, fail=False):
        self.snapshot = snapshot or Snapshot([record()])
        self.fail = fail
        self.calls = 0

    def fetch(self, source):
        self.calls += 1
        if self.fail:
            raise RuntimeError("upstream unavailable")
        return self.snapshot


class NormalizationTests(unittest.TestCase):
    def test_html_and_inference(self):
        job = normalize(record(), "test")
        self.assertEqual(job["description"], "Python, SQL, Docker and 3-5 years experience.")
        self.assertEqual(job["skills"], ["Docker", "Python", "SQL"])
        self.assertEqual(job["experience_level"], "Senior")
        self.assertEqual((job["experience_min_years"], job["experience_max_years"]), (3, 5))
        self.assertTrue(job["experience_inferred"])
        self.assertEqual(plain("&lt;p&gt;hello&lt;/p&gt;<script>evil()</script>"), "hello")

    def test_skill_boundaries_and_missing_values(self):
        job = normalize(record(title="Assistant", description="Enjoy teamwork in JavaScript. Ongoing work."), "test")
        self.assertEqual(job["skills"], ["JavaScript"])
        self.assertEqual(job["experience_level"], "Unknown")
        self.assertIsNone(job["experience_min_years"])
        self.assertIsNone(job["salary"])
        self.assertNotIn("Go", job["skills"])
        with self.assertRaises(ValueError):
            normalize(record(url="javascript:alert(1)"), "test")

    def test_tracking_url_removed(self):
        job = normalize(record(url="https://example.com/job?id=4&utm_source=x#apply"), "test")
        self.assertEqual(job["url"], "https://example.com/job?id=4")

    def test_timezone_midnight(self):
        config = load()
        now = datetime(2026, 10, 3, 18, 31, tzinfo=timezone.utc)
        self.assertEqual(schedule_slot(config, now), "2026-10-04T00:00:00+05:30")
        self.assertEqual(next_schedule(config, now), "2026-10-05T00:00:00+05:30")
        before = datetime(2026, 10, 3, 18, 29, tzinfo=timezone.utc)
        self.assertEqual(schedule_slot(config, before), "2026-10-03T00:00:00+05:30")


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp.name) / "test.sqlite3"))
        self.config = load()
        self.config["_database"] = self.db.path
        self.config["sources"] = [{"name": "test", "type": "arbeitnow", "enabled": True, "min_interval_hours": 0}]

    def tearDown(self):
        self.temp.cleanup()

    def add(self, job):
        with self.db.connect() as conn:
            return self.db.upsert(conn, job, "2026-10-03T00:00:00+00:00")

    def test_idempotent_and_updates(self):
        self.assertEqual(self.add(normalize(record(), "test")), "inserted")
        self.assertEqual(self.add(normalize(record(), "test")), "unchanged")
        self.assertEqual(self.add(normalize(record(description="Python and AWS"), "test")), "updated")
        self.assertEqual(self.db.stats()["total"], 1)
        job = self.db.job(1)
        self.assertEqual(job["version_count"], 2)
        self.assertEqual([s["name"] for s in job["skills"]], ["AWS", "Python"])

    def test_cross_source_dedup_preserves_links(self):
        self.add(normalize(record(), "one"))
        self.add(normalize(record(url="https://another.example/jobs/1"), "two"))
        self.assertEqual(self.db.stats()["total"], 1)
        self.assertEqual(len(self.db.job(1)["sources"]), 2)

    def test_closure_and_reopening(self):
        collect(self.config, client=FakeClient())
        for _ in range(2):
            collect(self.config, client=FakeClient(Snapshot([])))
        self.assertEqual(self.db.stats()["active"], 1)
        collect(self.config, client=FakeClient(Snapshot([])))
        self.assertEqual(self.db.stats()["active"], 0)
        collect(self.config, client=FakeClient())
        self.assertEqual(self.db.stats()["active"], 1)

    def test_failed_and_truncated_sources_never_close(self):
        collect(self.config, client=FakeClient())
        for _ in range(4):
            collect(self.config, client=FakeClient(Snapshot([], False)))
        self.assertEqual(self.db.stats()["active"], 1)
        result = collect(self.config, client=FakeClient(fail=True))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.db.stats()["active"], 1)
        self.assertIn("upstream unavailable", self.db.runs()[0]["sources"][0]["error"])

    def test_invalid_job_prevents_closing(self):
        collect(self.config, client=FakeClient())
        for _ in range(4):
            result = collect(self.config, client=FakeClient(Snapshot([record(url="bad")])) )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.db.stats()["active"], 1)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rejected_jobs").fetchone()[0], 4)

    def test_remotive_request_budget_includes_retries(self):
        for _ in range(4):
            request_budget(self.db, "https://remotive.com/api/remote-jobs")
        with self.assertRaises(ValueError):
            request_budget(self.db, "https://remotive.com/api/remote-jobs")
        request_budget(self.db, "https://www.arbeitnow.com/api/job-board-api")

    def test_source_failure_isolated(self):
        self.config["sources"].append({"name": "other", "type": "arbeitnow", "enabled": True, "min_interval_hours": 0})
        client = FakeClient()
        original = client.fetch
        client.fetch = lambda s: (_ for _ in ()).throw(RuntimeError("failed")) if s["name"] == "test" else original(s)
        result = collect(self.config, client=client)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.db.stats()["total"], 1)

    def test_daily_slot_once(self):
        client = FakeClient()
        first = collect(self.config, due=True, client=client)
        second = collect(self.config, due=True, client=client)
        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "not_due")
        self.assertEqual(client.calls, 1)

    def test_polling_interval(self):
        self.config["sources"][0]["min_interval_hours"] = 6
        client = FakeClient()
        collect(self.config, client=client)
        result = collect(self.config, client=client)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(client.calls, 1)

    def test_concurrent_collections_blocked(self):
        with collection_lock(self.db.path):
            with self.assertRaises(AlreadyRunning):
                collect(self.config, client=FakeClient())

    def test_search_and_csv_and_backup(self):
        self.add(normalize(record(company="=Danger", description="Python, SQL"), "test"))
        self.assertEqual(self.db.search({"skill": "Python", "location": "India"})["total"], 1)
        self.assertEqual(self.db.search({"q": "%' OR 1=1 --"})["total"], 0)
        self.assertIn("'=Danger", csv_text(self.db))
        dest = Path(self.temp.name) / "backup.sqlite3"
        self.db.backup(dest)
        self.assertEqual(Database(dest).stats()["total"], 1)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.client = FeedClient({"max_pages": 2, "page_delay_seconds": 0})

    def test_remotive(self):
        self.client.get = lambda _: {"jobs": [{"id": 9, "title": "Engineer", "company_name": "Company", "description": "Python", "url": "https://remotive.com/9", "candidate_required_location": "Worldwide"}]}
        result = self.client.fetch({"type": "remotive"})
        self.assertTrue(result.complete)
        self.assertEqual(normalize(result.jobs[0], "remotive")["workplace_type"], "remote")

    def test_arbeitnow_pagination_and_cap(self):
        calls = []
        def response(url):
            calls.append(url)
            return {"data": [{"slug": str(len(calls))}], "links": {"next": "https://evil.example/" if len(calls) == 1 else None}}
        self.client.get = response
        result = self.client.fetch({"type": "arbeitnow"})
        self.assertEqual(len(result.jobs), 2)
        self.assertTrue(result.complete)
        self.assertTrue(all(url.startswith("https://www.arbeitnow.com/") for url in calls))
        self.client.get = lambda _: {"data": [], "links": {"next": "more"}}
        self.assertFalse(self.client.fetch({"type": "arbeitnow"}).complete)

    def test_greenhouse(self):
        self.client.get = lambda _: {"jobs": [{"id": 1, "title": "Developer", "content": "&lt;p&gt;Python&lt;/p&gt;", "absolute_url": "https://example.com/1", "location": {"name": "Bangalore"}, "updated_at": "2026-01-01"}]}
        job = normalize(self.client.fetch({"type": "greenhouse", "board": "acme", "company": "Acme"}).jobs[0], "gh")
        self.assertEqual(job["description"], "Python")
        self.assertIsNone(job["published_at"])

    def test_lever_full_description(self):
        self.client.get = lambda _: [{"id": "x", "text": "Engineer", "descriptionPlain": "Opening", "lists": [{"text": "Requirements", "content": "<li>Python</li>"}], "additionalPlain": "Benefits", "hostedUrl": "https://jobs.lever.co/acme/x", "categories": {"location": "India"}, "workplaceType": "hybrid"}]
        result = self.client.fetch({"type": "lever", "board": "acme", "company": "Acme"})
        job = normalize(result.jobs[0], "lever")
        self.assertIn("Requirements", job["description"])
        self.assertIn("Benefits", job["description"])
        self.assertEqual(job["workplace_type"], "hybrid")

    def test_malformed_feed_fails(self):
        self.client.get = lambda _: {"not_jobs": []}
        with self.assertRaises(ValueError):
            self.client.fetch({"type": "remotive"})


if __name__ == "__main__":
    unittest.main()
