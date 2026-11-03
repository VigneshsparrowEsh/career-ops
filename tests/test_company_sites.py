import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from jobharvest.company_sites import (add_company_sites, board_from_url, discover_company_sites,
                                     parse_sites, validate_public_url, CareerPageClient, CareerRedirect)
from jobharvest.config import load
from jobharvest.collector import collect
from jobharvest.database import Database
from jobharvest.google_careers import BASE, SITEMAP, fetch_google, job_record, page_data, sitemap_jobs
from jobharvest.normalize import normalize
from jobharvest.robots import RobotsPolicy
from jobharvest.sources import FeedClient, Snapshot


def google_row(external_id="123"):
    row = [None] * 20
    row[0], row[1], row[7] = external_id, "Python Engineer", "Google"
    row[3], row[4], row[10] = [None, "<p>Build systems</p>"], [None, "<h3>Minimum qualifications</h3><p>Python</p>"], [None, "<p>Full job description</p>"]
    row[9] = [["Bengaluru, India", [], "Bengaluru", None, None, "IN"]]
    return row


def google_chunk(key, data):
    return "<script>AF_initDataCallback({key: '" + key + "', hash: '1', data:" + json.dumps(data) + ", sideChannel: {}});</script>"


class PageClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_page(self, url):
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value, url


class CompanySitesTests(unittest.TestCase):
    def test_detect_boards_and_embeds(self):
        for url, kind, board in [
            ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse", "acme"),
            ("https://boards.greenhouse.io/embed/job_board?for=acme", "greenhouse", "acme"),
            ("https://jobs.eu.lever.co/acme/123", "lever", "acme"),
            ("https://jobs.ashbyhq.com/acme/123", "ashby", "acme"),
        ]:
            source = board_from_url(url, "Acme")
            self.assertEqual((source["type"], source["board"]), (kind, board))
        self.assertEqual(board_from_url(BASE, "Google")["type"], "google")
        self.assertIsNone(board_from_url("https://jobs.ashbyhq.com.evil.example/acme", "Acme"))
        self.assertIsNone(board_from_url("https://boards.greenhouse.io/%2E%2E", "Acme"))
        self.assertIsNone(board_from_url("https://jobs.ashbyhq.com/acme:123", "Acme"))

    def test_discover_links_iframes_scripts_and_same_site_page(self):
        client = PageClient({"https://acme.test": '<a href="/careers">Careers</a>',
                             "https://acme.test/careers": '<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe><script>{"url":"https:\\/\\/jobs.ashbyhq.com\\/other"}</script>'})
        sources = discover_company_sites("https://acme.test", "Acme", client)
        self.assertEqual({s["type"] for s in sources}, {"greenhouse", "ashby"})
        self.assertEqual(len(client.calls), 2)
        client = PageClient({"https://acme.test": CareerRedirect("https://jobs.lever.co/acme")})
        self.assertEqual(discover_company_sites("https://acme.test", "Acme", client)[0]["board"], "acme")

    def test_unsupported_and_bounded_discovery(self):
        with self.assertRaisesRegex(ValueError, "No supported"):
            discover_company_sites("https://acme.test", "Acme", PageClient({"https://acme.test": "No jobs"}))
        self.assertEqual(parse_sites("Acme | https://acme.test\n"), [{"company": "Acme", "url": "https://acme.test"}])
        for value in ["https://acme.test", "Acme | http://acme.test", None, "Acme | https://acme.test\n" * 26]:
            with self.assertRaises(ValueError):
                parse_sites(value)

    def test_reject_private_and_mixed_dns(self):
        for addresses in [["127.0.0.1"], ["10.0.0.1"], ["::1"], ["8.8.8.8", "192.168.0.1"]]:
            with patch("jobharvest.company_sites.socket.getaddrinfo", return_value=[(2, 1, 6, "", (address, 443)) for address in addresses]):
                with self.assertRaises(ValueError):
                    validate_public_url("https://acme.test")
        for url in ["http://example.com", "https://user:secret@example.com", "https://example.com:8765"]:
            with self.assertRaises(ValueError):
                validate_public_url(url)

    def test_save_deduplicate_preserve_settings_and_isolate_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config = {k: v for k, v in load().items() if not k.startswith("_")}
            config["filters"]["roles"] = ["engineer"]
            path.write_text(json.dumps(config), encoding="utf-8")
            entries = parse_sites("Acme | https://jobs.ashbyhq.com/acme\nBad | https://bad.test")
            result = add_company_sites(path, entries, PageClient({"https://bad.test": "No supported board"}))
            self.assertEqual(result["added"], ["ashby-acme"])
            self.assertEqual(result["sites"][1]["status"], "unsupported")
            second = add_company_sites(path, entries[:1])
            self.assertEqual(second["added"], [])
            self.assertEqual(second["existing"], ["ashby-acme"])
            self.assertEqual(load(path)["filters"]["roles"], ["engineer"])

    def test_ashby_full_fields_and_unlisted_excluded(self):
        client = FeedClient({})
        client.get = lambda _: {"jobs": [{"id": "x", "title": "Engineer", "descriptionPlain": "Python", "location": "India", "secondaryLocations": [{"location": "UK"}], "workplaceType": "Hybrid", "isRemote": False, "employmentType": "FullTime", "publishedAt": "2026-10-01", "address": {"postalAddress": {"addressCountry": "IN"}}, "compensation": {"scrapeableCompensationSalarySummary": "INR 20-30 lakh"}, "jobUrl": "https://jobs.ashbyhq.com/acme/x"}, {"isListed": False}]}
        snapshot = client.fetch({"type": "ashby", "board": "acme", "company": "Acme"})
        self.assertEqual(len(snapshot.jobs), 1)
        job = normalize(snapshot.jobs[0], "ashby-acme")
        self.assertEqual((job["location"], job["country"], job["workplace_type"]), ("India; UK", "IN", "hybrid"))
        self.assertEqual(job["salary"], "INR 20-30 lakh")


class RobotsTests(unittest.TestCase):
    def test_queries_wildcards_longest_rule_and_agent_groups(self):
        policy = RobotsPolicy("User-agent: *\nDisallow: /?\nDisallow: /jobs/results?page=\nDisallow: /jobs/results?*&page=\nDisallow: /private\nAllow: /private/public$\nUser-agent: OtherBot\nDisallow: /")
        self.assertTrue(policy.can_fetch("JobHarvest/1.0", "https://example.com/jobs/results"))
        self.assertFalse(policy.can_fetch("JobHarvest/1.0", "https://example.com/jobs/results?page=2"))
        self.assertFalse(policy.can_fetch("JobHarvest", "https://example.com/jobs/results?q=python&page=2"))
        self.assertTrue(policy.can_fetch("JobHarvest", "https://example.com/private/public"))
        self.assertFalse(policy.can_fetch("JobHarvest", "https://example.com/private/public/child"))
        self.assertFalse(policy.can_fetch("OtherBot", "https://example.com/jobs/results"))
        self.assertTrue(RobotsPolicy("").can_fetch("JobHarvest", "https://example.com"))
        self.assertTrue(RobotsPolicy("User-agent: *\nDisallow:\nUser-agent: OtherBot\nDisallow: /").can_fetch("JobHarvest", "https://example.com"))

    def test_blocked_page_is_not_requested(self):
        client = CareerPageClient()
        with patch("jobharvest.company_sites.validate_public_url", return_value=urlsplit("https://acme.test/private")), patch.object(client, "read", return_value=("User-agent: *\nDisallow: /private", "https://acme.test/robots.txt")):
            with self.assertRaisesRegex(ValueError, "does not permit"):
                client.get_page("https://acme.test/private")


class GoogleTests(unittest.TestCase):
    def test_cursor_survives_collections_and_partial_batches_never_close_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config = {k: v for k, v in load().items() if not k.startswith("_")}
            config["database"] = "jobs.sqlite3"
            config["sources"] = [{"name": "google-careers", "type": "google", "enabled": True, "min_interval_hours": 6}]
            path.write_text(json.dumps(config), encoding="utf-8")
            config = load(path)
            config["sources"][0]["min_interval_hours"] = 0
            client = FeedClient({})
            cursors = []
            def fetch(source):
                cursors.append(source["_cursor"])
                return Snapshot([job_record(google_row("123"))], False, "batch", "124")
            client.fetch = fetch
            first = collect(config, client=client)
            collect(config, client=client)
            self.assertEqual(first["status"], "partial")
            self.assertEqual(cursors, ["", "124"])
            db = Database(config["_database"])
            self.assertEqual(db.state("cursor:google-careers"), "124")
            client.fetch = lambda source: Snapshot([], False, "batch", "125")
            for _ in range(3):
                collect(config, client=client)
            self.assertEqual(db.stats()["active"], 1)

    def test_rotating_sitemap_batches_and_full_description(self):
        sitemap = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(f'<url><loc>https://careers.google.com/jobs/results/{id}-engineer/</loc></url>' for id in ["123", "124", "125"]) + '</urlset>'
        client = PageClient({BASE: google_chunk("ds:1", [[google_row()], None, 3, 20]), SITEMAP: sitemap,
                             BASE + "/124": google_chunk("ds:0", [google_row("124")]), BASE + "/125": google_chunk("ds:0", [google_row("125")])})
        first = fetch_google({}, max_pages=2, delay=0, client=client)
        self.assertFalse(first.complete)
        self.assertEqual(first.cursor, "124")
        self.assertEqual(len(first.jobs), 2)
        second = fetch_google({"_cursor": first.cursor}, max_pages=1, delay=0, client=client)
        self.assertEqual(second.cursor, "125")
        self.assertTrue(any(j["external_id"] == "125" for j in second.jobs))
        job = normalize(second.jobs[-1], "google-careers")
        self.assertIn("Full job description", job["description"])
        self.assertIn("Build systems", job["description"])
        self.assertEqual(job["country"], "IN")
        self.assertIsNone(job["published_at"])
        self.assertFalse(any("page=" in url for url in client.calls))

    def test_google_format_changes_and_offsite_sitemap(self):
        with self.assertRaises(ValueError):
            fetch_google({}, delay=0, client=PageClient({BASE: "Changed website"}))
        with self.assertRaises(ValueError):
            sitemap_jobs('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://evil.test/jobs/results/123/</loc></url></urlset>')
        self.assertEqual(page_data("<script>alert('x')</script>"), {})
