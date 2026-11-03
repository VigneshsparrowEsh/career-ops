"""Read Google's public HTML and sitemap. Do not request blocked search pagination."""
import bisect
import json
import re
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .company_sites import CareerPageClient, CareerRedirect
from .sources import Snapshot

BASE = "https://www.google.com/about/careers/applications/jobs/results"
SITEMAP = "https://www.google.com/about/careers/applications/jobs/sitemap.xml"


def page_data(text):
    """Decode literal embedded JSON only; never execute page scripts."""
    chunks = {}
    for match in re.finditer(r"AF_initDataCallback\(\{key:\s*'([^']+)'[^<]*?\bdata:", text):
        try:
            data, _ = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
        except ValueError as exc:
            raise ValueError("Google careers data format changed") from exc
        chunks[match.group(1)] = data
    return chunks


def job_record(row):
    if not isinstance(row, list) or len(row) < 11 or not isinstance(row[0], str) or not row[0].isdigit() or not isinstance(row[1], str):
        raise ValueError("Google careers job format changed")
    def fragment(index):
        value = row[index] if index < len(row) else None
        return value[1] if isinstance(value, list) and len(value) > 1 and isinstance(value[1], str) else ""
    locations = row[9] or []
    if not isinstance(locations, list) or any(not isinstance(x, list) or not x or not isinstance(x[0], str) for x in locations):
        raise ValueError("Google careers location format changed")
    country = {x[5] for x in locations if len(x) > 5 and x[5]}
    return {"external_id": row[0], "title": row[1], "company": row[7] or "Google",
            "location": "; ".join(x[0] for x in locations), "country": next(iter(country)) if len(country) == 1 else None,
            "description": "\n".join(filter(None, [fragment(15), fragment(18), fragment(4), "About the job\n" + fragment(10), "Responsibilities\n" + fragment(3)])),
            "url": BASE + "/" + row[0], "raw": {"google_careers_row": row}}
    # Embedded timestamps and numeric levels have no documented semantics; leave those fields unknown.


def get_page(client, url):
    for _ in range(4):
        try:
            return client.get_page(url)[0]
        except CareerRedirect as redirect:
            host = urlsplit(redirect.url).hostname
            if host not in {"www.google.com", "careers.google.com"}:
                raise ValueError("Google redirected outside its public careers site")
            url = redirect.url
    raise ValueError("Too many Google careers redirects")


def sitemap_jobs(text):
    tree = ElementTree.fromstring(text)
    entries = {}
    for loc in tree.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        parsed = urlsplit(loc.text or "")
        if parsed.hostname not in {"careers.google.com", "www.google.com"}:
            continue
        match = re.fullmatch(r"/(?:about/careers/applications/)?jobs/results/(\d+)(?:-[^/?#]*)?/?", parsed.path)
        if match:
            entries[match.group(1)] = BASE + "/" + match.group(1)
    if not entries:
        raise ValueError("Google careers sitemap did not contain recognized job links")
    return entries


def fetch_google(source, max_pages=30, delay=1, timeout=15, client=None):
    client = client or CareerPageClient(timeout)
    data = page_data(get_page(client, BASE))
    search = data.get("ds:1")
    if not isinstance(search, list) or not search or not isinstance(search[0], list):
        raise ValueError("Google careers search data was not found; no jobs were changed")
    jobs = {row[0]: job_record(row) for row in search[0]}
    entries = sitemap_jobs(get_page(client, SITEMAP))
    ids = sorted(entries)
    offset = bisect.bisect_right(ids, source.get("_cursor", "")) % len(ids)
    ordered = ids[offset:] + ids[:offset]
    cursor = source.get("_cursor", "")
    failures, fetched = [], 0
    for external_id in ordered[:max_pages]:
        cursor = external_id
        if external_id in jobs:
            continue
        time.sleep(delay)
        try:
            detail = page_data(get_page(client, entries[external_id])).get("ds:0")
            if not isinstance(detail, list) or not detail:
                raise ValueError("Google job detail data was not found")
            job = job_record(detail[0])
            if job["external_id"] != external_id:
                raise ValueError("Google job detail did not match its sitemap ID")
            jobs[external_id] = job
            fetched += 1
        except Exception as exc:
            failures.append(f"{external_id}: {type(exc).__name__}: {exc}")
    note = (f"Google sitemap contains {len(entries)} job links. Saved first-page jobs plus {fetched} job details in a rotating batch "
            f"of up to {max_pages}. Later collections continue from the saved position. Search pagination is blocked by robots rules. "
            "This is a partial inventory; closure detection is disabled.")
    if failures:
        note += f" {len(failures)} details could not be read; first: {failures[0]}"
    return Snapshot(list(jobs.values()), False, note, cursor)
