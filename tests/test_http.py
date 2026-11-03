import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from jobharvest.config import load
from jobharvest.database import Database
from jobharvest.normalize import normalize, utcnow


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        config = load()
        config.pop("_path")
        config.pop("_database")
        config["database"] = "jobs.sqlite3"
        for source in config["sources"]:
            source["enabled"] = False
        if not any(s["type"] == "google" for s in config["sources"]):
            config["sources"].append({"name": "google-careers", "type": "google", "company": "Google", "enabled": False, "min_interval_hours": 6})
        path = root / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        db = Database(root / "jobs.sqlite3")
        job = normalize({"external_id": "1", "title": "Python Developer", "company": "Test Company", "location": "India", "description": "<p>Python</p><script>alert(1)</script>", "url": "https://example.com/job"}, "fixture")
        with db.connect() as conn:
            db.upsert(conn, job, utcnow())
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        cls.process = subprocess.Popen([sys.executable, "-m", "jobharvest", "--config", str(path), "serve", "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                with urlopen(cls.base + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.1)
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.temp.cleanup()
        raise RuntimeError("HTTP test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.temp.cleanup()

    def get(self, path):
        with urlopen(self.base + path, timeout=5) as response:
            return json.load(response)

    def test_search_detail_and_pagination(self):
        self.assertEqual(self.get("/api/jobs?skill=Python&location=India")["total"], 1)
        self.assertEqual(self.get("/api/jobs?offset=1")["jobs"], [])
        self.assertEqual(self.get("/api/jobs/1")["description"], "Python")
        self.assertEqual(self.get("/api/jobs?limit=999")["limit"], 100)
        with self.assertRaises(HTTPError) as exc:
            self.get("/api/jobs/999")
        self.assertEqual(exc.exception.code, 404)

    def test_csv_export_and_security_headers(self):
        with urlopen(self.base + "/api/export.csv", timeout=5) as response:
            self.assertIn("Python Developer", response.read().decode("utf-8-sig"))
            self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

    def test_collect_requires_token_and_same_origin(self):
        with self.assertRaises(HTTPError) as exc:
            urlopen(Request(self.base + "/api/collect", method="POST"), timeout=5)
        self.assertEqual(exc.exception.code, 403)
        csrf = self.get("/api/stats")["csrf_token"]
        with self.assertRaises(HTTPError) as exc:
            urlopen(Request(self.base + "/api/collect", method="POST", headers={"X-JobHarvest-Token": csrf, "Origin": "https://external.example"}), timeout=5)
        self.assertEqual(exc.exception.code, 403)

    def test_dns_rebinding_and_bad_parameters(self):
        with self.assertRaises(HTTPError) as exc:
            urlopen(Request(self.base + "/api/stats", headers={"Host": "attacker.example"}), timeout=5)
        self.assertEqual(exc.exception.code, 403)
        with self.assertRaises(HTTPError) as exc:
            self.get("/api/jobs?limit=invalid")
        self.assertEqual(exc.exception.code, 400)

    def test_personalized_profile_matches_and_shortlist_security(self):
        from test_personalization import PROFILE
        csrf = self.get("/api/stats")["csrf_token"]
        headers = {"X-JobHarvest-Token": csrf, "Content-Type": "application/json"}
        for path in ["/api/profile", "/api/preferences", "/api/career-ops/queue"]:
            with self.assertRaises(HTTPError) as exc:
                urlopen(Request(self.base + path, data=b'{}'),timeout=5)
            self.assertEqual(exc.exception.code,403)
        def post(path, body):
            with urlopen(Request(self.base + path,data=json.dumps(body).encode(),headers=headers),timeout=10) as response:
                return json.load(response)
        self.assertTrue(post("/api/profile",PROFILE)["profile"]["ready"])
        self.assertEqual(self.get("/api/profile")["profile"]["country"],"India")
        # Change targets to the fixture's actual role; ranking must reflect the saved profile.
        post("/api/profile",dict(PROFILE,roles=["Python Developer"]))
        self.assertEqual(self.get("/api/matches")["total"],1)
        self.assertEqual(self.get("/api/matches?remote=true")["total"],0)
        post("/api/preferences",{"job_id":1,"status":"saved","notes":"Read full JD"})
        self.assertEqual(self.get("/api/jobs?saved=true")["jobs"][0]["preference"]["notes"],"Read full JD")
        self.assertEqual(post("/api/career-ops/queue",{"job_id":1})["status"],"queued")
        self.assertEqual(post("/api/career-ops/queue",{"job_id":1})["status"],"existing")
        post("/api/preferences",{"job_id":1,"status":"hidden"})
        self.assertEqual(self.get("/api/matches")["total"],0)
        post("/api/preferences",{"job_id":1,"status":"none"})
        for path,body in [("/api/profile",{}),("/api/preferences",{"job_id":1,"status":"Applied"}),("/api/preferences",{"job_id":True,"status":"saved"})]:
            with self.assertRaises(HTTPError) as exc:
                post(path,body)
            self.assertEqual(exc.exception.code,400)

    def test_company_sites_authenticated_validation_and_async_result(self):
        with self.assertRaises(HTTPError) as exc:
            urlopen(Request(self.base + "/api/company-sites", data=b'{}', method="POST"), timeout=5)
        self.assertEqual(exc.exception.code, 403)
        csrf = self.get("/api/stats")["csrf_token"]
        headers = {"X-JobHarvest-Token": csrf, "Content-Type": "application/json"}
        for body in [b'{bad json', b'[]', b'{"sites":"not a career URL"}', b'{}']:
            with self.assertRaises(HTTPError) as exc:
                urlopen(Request(self.base + "/api/company-sites", data=body, headers=headers), timeout=5)
            self.assertEqual(exc.exception.code, 400)
        body = json.dumps({"sites": "Google | https://www.google.com/about/careers/applications/jobs/results"}).encode()
        with urlopen(Request(self.base + "/api/company-sites", data=body, headers=headers), timeout=5) as response:
            self.assertEqual(response.status, 202)
        for _ in range(50):
            stats = self.get("/api/stats")
            if not stats["collector"]["running"]:
                break
            time.sleep(0.05)
        self.assertEqual(stats["collector"]["result"]["discovery"]["added"], [])
        self.assertIn("google-careers", stats["collector"]["result"]["discovery"]["disabled"])
        self.assertEqual(stats["total"], 1)
