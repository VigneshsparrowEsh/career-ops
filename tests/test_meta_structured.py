import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from unittest.mock import MagicMock, patch

from jobharvest.company_sites import (CareerPageClient, CareerPageError, add_company_sites,
                                     board_from_url, discover_company_sites)
from jobharvest.config import load
from jobharvest.meta_careers import BASE, SITEMAP, extract_job, fetch_meta, sitemap_jobs
from jobharvest.normalize import normalize
from jobharvest.robots import RobotsPolicy
from jobharvest.structured_jobs import fetch_structured, job_postings, job_record
from jobharvest.database import Database


def posting(**changes):
    value = {"@type": "JobPosting", "title": "Python Engineer", "description": "<p>Build Python systems.</p>",
             "datePosted": "2026-10-03", "hiringOrganization": {"name": "Meta"}, "employmentType": "FULL_TIME",
             "jobLocation": [{"name": "Bengaluru, India", "address": {"addressCountry": "IN"}}],
             "qualifications": "Python &nbsp; SQL", "responsibilities": "Build software", "jobLocationType": "TELECOMMUTE"}
    value.update(changes)
    return value


def script(value, kind="application/ld+json"):
    return '<script type="' + kind + '">' + json.dumps(value) + '</script>'


def meta_page(external_id):
    details = {"id": external_id, "title": "Python Engineer", "description": json.dumps({"__html": "<p>Complete overview</p>"}),
               "responsibilities": [{"item": "Build services"}], "minimum_qualifications": [{"item": "Python"}],
               "preferred_qualifications": [{"item": "SQL"}], "departments": ["Engineering"],
               "public_compensation": [{"compensation_amount_minimum": "$100/year", "compensation_amount_maximum": "$200/year", "country_code": "US", "has_bonus": True}]}
    return script(posting()) + script({"__bbox": {"result": {"data": {"xcp_requisition_job_description": details}}}}, "application/json")


class PageClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_page(self, url):
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value, url


def sitemap(ids):
    return '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join('<url><loc>' + BASE + '/profile/job_details/' + id + '/</loc></url>' for id in ids) + '</urlset>'


class MetaTests(unittest.TestCase):
    def test_detection_sitemap_validation_and_no_cursor_pagination(self):
        self.assertEqual(board_from_url(BASE + "/jobsearch/", "Meta")["type"], "meta")
        self.assertEqual(board_from_url(BASE + "/profile/job_details/123/", "Meta")["name"], "meta-careers")
        self.assertIsNone(board_from_url("https://www.metacareers.com.evil.test/jobsearch/", "Meta"))
        self.assertEqual(len(sitemap_jobs(sitemap(["123", "124"]))), 2)
        with self.assertRaises(ValueError):
            sitemap_jobs(sitemap(["123"]).replace(BASE, "https://evil.test"))
        self.assertFalse(RobotsPolicy("User-agent: *\nDisallow: /*cursor=").can_fetch("JobHarvest", BASE + "/jobsearch/?cursor=next"))

    def test_full_description_public_salary_and_cursor_resume(self):
        pages = {SITEMAP: sitemap(["123", "124", "125"]), **{BASE + "/profile/job_details/" + id + "/": meta_page(id) for id in ["123", "124", "125"]}}
        client = PageClient(pages)
        first = fetch_meta({}, max_pages=2, delay=0, client=client)
        second = fetch_meta({"_cursor": first.cursor}, max_pages=1, delay=0, client=client)
        self.assertEqual(first.cursor, "124")
        self.assertEqual(second.jobs[0]["external_id"], "125")
        self.assertFalse(first.complete)
        job = normalize(first.jobs[0], "meta-careers")
        for heading in ["Complete overview", "Minimum qualifications", "Preferred qualifications", "Responsibilities"]:
            self.assertIn(heading, job["description"])
        self.assertEqual(job["salary"], "US $100/year – $200/year + bonus")
        self.assertEqual(job["country"], "IN")
        self.assertEqual(job["workplace_type"], "remote")
        self.assertTrue(all("cursor=" not in url for url in client.calls))

    def test_rate_limit_pauses_batch_and_retains_success_cursor(self):
        client = PageClient({SITEMAP: sitemap(["123", "124", "125"]), BASE + "/profile/job_details/123/": meta_page("123"), BASE + "/profile/job_details/124/": CareerPageError("Rate limited", "rate_limited")})
        result = fetch_meta({}, max_pages=3, delay=0, client=client)
        self.assertEqual(result.cursor, "123")
        self.assertEqual(len(result.jobs), 1)
        self.assertIn("paused", result.note)
        self.assertFalse(any("125" in url for url in client.calls))

    def test_changed_or_login_page_fails_without_empty_success(self):
        with self.assertRaisesRegex(ValueError, "JobPosting"):
            extract_job("Sign in", BASE + "/profile/job_details/123/", "123")
        with self.assertRaisesRegex(ValueError, "No Meta job pages"):
            fetch_meta({}, max_pages=1, delay=0, client=PageClient({SITEMAP: sitemap(["123"]), BASE + "/profile/job_details/123/": "Changed page"}))


class StructuredTests(unittest.TestCase):
    def test_graph_arrays_salary_multiple_locations_and_invalid_block(self):
        text = '<script type="application/ld+json">not json</script>' + script({"@graph": [posting(baseSalary={"currency": "INR", "value": {"minValue": 100, "maxValue": 200, "unitText": "YEAR"}}, jobLocation=[{"address": {"addressLocality": "Bengaluru", "addressCountry": "IN"}}, {"name": "London", "address": {"addressCountry": "GB"}}])]})
        job = normalize(job_record(job_postings(text)[0], "https://acme.test/careers/123", "Acme"), "structured")
        self.assertEqual(job["salary"], "INR 100–200 YEAR")
        self.assertIsNone(job["country"])
        self.assertIn("SQL", job["skills"])
        with self.assertRaises(ValueError):
            job_record(posting(url="https://private.test/123"), "https://acme.test/jobs", "Acme")

    def test_independent_page_identity_and_partial_inventory(self):
        client = PageClient({"https://acme.test/careers": script(posting()), "https://other.test/careers": script(posting(hiringOrganization={"name": "Other"}))})
        a = discover_company_sites("https://acme.test/careers", "Acme", client)[0]
        b = discover_company_sites("https://other.test/careers", "Other", client)[0]
        self.assertEqual(a["type"], "structured")
        self.assertNotEqual(a["name"], b["name"])
        self.assertFalse(fetch_structured(a, client).complete)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({k: v for k, v in load().items() if not k.startswith("_")}), encoding="utf-8")
            result = add_company_sites(path, [{"company": "Acme", "url": a["career_url"]}, {"company": "Other", "url": b["career_url"]}], client)
            self.assertEqual(len(result["added"]), 2)

    def test_multiple_postings_require_distinct_source_urls(self):
        source = {"company": "Acme", "career_url": "https://acme.test/jobs"}
        client = PageClient({source["career_url"]: script([posting(), posting(title="Other")])})
        with self.assertRaises(ValueError):
            fetch_structured(source, client)
        client.pages[source["career_url"]] = script([posting(url="/jobs/123"), posting(title="Other", url="/jobs/124")])
        self.assertEqual(len(fetch_structured(source, client).jobs), 2)


class PageReliabilityTests(unittest.TestCase):
    def test_access_denied_and_rate_limit_stop_without_immediate_retry(self):
        for code, expected in [(403, "access_denied"), (429, "rate_limited")]:
            client = CareerPageClient()
            client.opener = MagicMock()
            client.opener.open.side_effect = HTTPError("https://acme.test", code, "error", {"Retry-After": "60"}, None)
            with patch("jobharvest.company_sites.validate_public_url", return_value=urlsplit("https://acme.test")):
                with self.assertRaises(CareerPageError) as exc:
                    client.read("https://acme.test")
            self.assertEqual(exc.exception.code, expected)
            self.assertEqual(client.opener.open.call_count, 1)

    def test_transient_server_error_has_one_bounded_retry(self):
        client = CareerPageClient()
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"ok"
        response.headers.get_content_charset.return_value = "utf-8"
        response.geturl.return_value = "https://acme.test"
        client.opener = MagicMock()
        client.opener.open.side_effect = [HTTPError("https://acme.test", 503, "error", {}, None), response]
        with patch("jobharvest.company_sites.validate_public_url", return_value=urlsplit("https://acme.test")), patch("jobharvest.company_sites.time.sleep"):
            self.assertEqual(client.read("https://acme.test")[0], "ok")
        self.assertEqual(client.opener.open.call_count, 2)

    def test_source_health_survives_later_cooldown_skip(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "jobs.sqlite3")
            with db.connect() as conn:
                run = conn.execute("INSERT INTO ingestion_runs(started_at,status,trigger) VALUES ('2026-10-03','success','manual')").lastrowid
                conn.execute("INSERT INTO source_runs(run_id,source,started_at,status,fetched) VALUES (?,'meta-careers','2026-10-03','partial',30)", (run,))
                conn.execute("INSERT INTO source_runs(run_id,source,started_at,status) VALUES (?,'meta-careers','2026-10-03','skipped')", (run,))
            health = db.stats()["source_health"]["meta-careers"]
            self.assertEqual(health["status"], "partial")
            self.assertEqual(health["fetched"], 30)
