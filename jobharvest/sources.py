"""Source adapters for documented public feeds; never log in or bypass challenges."""
import json
import re
import socket
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


@dataclass
class Snapshot:
    jobs: list
    complete: bool = True
    note: str = ""
    cursor: str | None = None


class FeedClient:
    def __init__(self, settings, before_request=None, progress=None):
        self.timeout = settings.get("timeout_seconds", 30)
        self.retries = settings.get("retries", 2)
        self.delay = settings.get("page_delay_seconds", 1)
        self.max_pages = settings.get("max_pages", 30)
        self.before_request = before_request
        self.progress = progress

    def get(self, url):
        if urlsplit(url).scheme != "https":
            raise ValueError("Feed endpoints must use HTTPS")
        for attempt in range(self.retries + 1):
            if self.before_request:
                self.before_request(url)
            request = Request(url, headers={"User-Agent": "JobHarvest/1.0 (daily public-feed collector)", "Accept": "application/json"})
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = response.read(40 * 1024 * 1024 + 1)
                    if len(body) > 40 * 1024 * 1024:
                        raise ValueError("Feed exceeded the 40 MB response limit")
                    return json.loads(body.decode("utf-8-sig"))
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise
                retry_after = exc.headers.get("Retry-After", "")
                wait = min(60, max(1, int(retry_after))) if retry_after.isdigit() else 2 ** (attempt + 1)
                exc.close()
                time.sleep(wait)
            except (URLError, TimeoutError, socket.timeout):
                if attempt == self.retries:
                    raise
                time.sleep(2 ** (attempt + 1))

    @staticmethod
    def array(payload, key=None):
        value = payload.get(key) if isinstance(payload, dict) and key else payload
        if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
            raise ValueError(f"Unexpected feed schema: expected a job array ({key or 'root'})")
        return value

    def fetch(self, source):
        kind = source["type"]
        if kind == "meta":
            from .meta_careers import fetch_meta
            return fetch_meta(source, self.max_pages, self.delay, self.timeout, progress=self.progress)
        if kind == "structured":
            from .structured_jobs import fetch_structured
            return fetch_structured(source)
        if kind == "google":
            from .google_careers import fetch_google
            return fetch_google(source, self.max_pages, self.delay, self.timeout)
        if kind == "remotive":
            rows = self.array(self.get("https://remotive.com/api/remote-jobs"), "jobs")
            return Snapshot([{
                "external_id": j.get("id"), "title": j.get("title"), "company": j.get("company_name"),
                "description": j.get("description"), "location": j.get("candidate_required_location"),
                "domain": j.get("category"), "employment_type": j.get("job_type"),
                "salary": j.get("salary"), "published_at": j.get("publication_date"),
                "url": j.get("url"), "remote": True, "raw": j,
            } for j in rows])
        if kind == "arbeitnow":
            jobs = []
            for page in range(1, self.max_pages + 1):
                payload = self.get(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
                rows = self.array(payload, "data")
                jobs.extend({
                    "external_id": j.get("slug"), "title": j.get("title"), "company": j.get("company_name"),
                    "description": j.get("description"), "location": j.get("location"),
                    "employment_type": ", ".join(j.get("job_types") or []), "published_at": j.get("created_at"),
                    "url": j.get("url"), "remote": j.get("remote"), "raw": j,
                } for j in rows)
                links = payload.get("links")
                if not isinstance(links, dict) or "next" not in links:
                    return Snapshot(jobs, False, "Pagination metadata missing; closure detection disabled")
                if not links["next"]:
                    return Snapshot(jobs)
                # Build page URL ourselves rather than following arbitrary upstream links.
                time.sleep(self.delay)
            return Snapshot(jobs, False, "Page limit reached; closure detection disabled")
        board = source.get("board", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
            raise ValueError("Company board token must contain only letters, numbers, hyphens or underscores")
        company = source.get("company")
        if not company:
            raise ValueError("Company-board sources require an explicit company name")
        if kind == "ashby":
            rows = self.array(self.get(f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}?includeCompensation=true"), "jobs")
            jobs = []
            for j in rows:
                # Unlisted postings are only intended to be available via direct link.
                if j.get("isListed") is False:
                    continue
                locations = [j.get("location")] + [x.get("location") for x in j.get("secondaryLocations", [])]
                compensation = j.get("compensation") or {}
                address = (j.get("address") or {}).get("postalAddress") or {}
                jobs.append({
                    "external_id": j.get("id") or j.get("jobUrl"), "title": j.get("title"), "company": company,
                    "description": j.get("descriptionPlain") or j.get("descriptionHtml"),
                    "location": "; ".join(dict.fromkeys(x for x in locations if x)),
                    "country": address.get("addressCountry"), "domain": j.get("department") or j.get("team"),
                    "remote": j.get("isRemote"),
                    "workplace_type": {"Remote": "remote", "Hybrid": "hybrid", "OnSite": "onsite"}.get(j.get("workplaceType")),
                    "employment_type": {"FullTime": "Full time", "PartTime": "Part time", "Intern": "Internship"}.get(j.get("employmentType"), j.get("employmentType")),
                    "salary": compensation.get("scrapeableCompensationSalarySummary") or compensation.get("compensationTierSummary"),
                    "published_at": j.get("publishedAt"), "url": j.get("jobUrl"), "raw": j,
                })
            return Snapshot(jobs)
        if kind == "greenhouse":
            rows = self.array(self.get(f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs?content=true"), "jobs")
            return Snapshot([{
                "external_id": j.get("id"), "title": j.get("title"), "company": company,
                "description": j.get("content"), "location": (j.get("location") or {}).get("name"),
                "domain": ", ".join(d["name"] for d in j.get("departments", []) if d.get("name")),
                "url": j.get("absolute_url"), "raw": j,
                # Greenhouse updated_at is NOT a publication date.
            } for j in rows])
        if kind == "lever":
            host = "api.eu.lever.co" if source.get("region") == "eu" else "api.lever.co"
            jobs = []
            for page in range(self.max_pages):
                rows = self.array(self.get(f"https://{host}/v0/postings/{quote(board)}?mode=json&skip={page * 100}&limit=100"))
                for j in rows:
                    c = j.get("categories") or {}
                    description = "\n".join([j.get("descriptionPlain") or j.get("description") or ""] + [
                        str(item.get("text", "")) + "\n" + str(item.get("content", "")) for item in j.get("lists", [])
                    ] + [j.get("additionalPlain") or j.get("additional") or ""])
                    jobs.append({
                        "external_id": j.get("id"), "title": j.get("text"), "company": company,
                        "description": description, "location": c.get("location"), "country": j.get("country"),
                        "domain": c.get("department") or c.get("team"), "experience_level": c.get("level"),
                        "employment_type": c.get("commitment"), "workplace_type": j.get("workplaceType"),
                        "salary": j.get("salaryDescriptionPlain") or j.get("salaryDescription") or (json.dumps(j["salaryRange"]) if j.get("salaryRange") else None),
                        "url": j.get("hostedUrl"), "raw": j,
                    })
                if len(rows) < 100:
                    return Snapshot(jobs)
                time.sleep(self.delay)
            return Snapshot(jobs, False, "Page limit reached; closure detection disabled")
        raise ValueError(f"Unsupported source type: {kind}")
