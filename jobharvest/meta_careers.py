"""Meta public sitemap and job-page connector; no login, GraphQL calls or cursor pagination."""
import bisect
import json
import re
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .company_sites import CareerPageClient, CareerPageError, CareerRedirect
from .sources import Snapshot
from .structured_jobs import job_postings, job_record, json_documents, walk_json

BASE = "https://www.metacareers.com"
SITEMAP = BASE + "/jobsearch/sitemap.xml"


def sitemap_jobs(text):
    tree = ElementTree.fromstring(text)
    if tree.tag != "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset":
        raise ValueError("Meta career sitemap format changed")
    entries = {}
    for loc in tree.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        parsed = urlsplit(loc.text or "")
        match = re.fullmatch(r"/profile/job_details/(\d+)/?", parsed.path)
        if parsed.scheme == "https" and parsed.hostname == "www.metacareers.com" and parsed.port in {None, 443} and not parsed.username and not parsed.query and match:
            entries[match[1]] = BASE + "/profile/job_details/" + match[1] + "/"
    if not entries:
        raise ValueError("Meta career sitemap has no recognized job links; saved jobs are preserved")
    return entries


def html_fragment(value):
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, dict):
                return decoded.get("__html") or ""
        except ValueError:
            return value
    return value.get("__html", "") if isinstance(value, dict) else ""


def extract_job(text, url, external_id):
    postings = job_postings(text)
    if len(postings) != 1:
        raise ValueError("Meta page did not contain exactly one public JobPosting; it may have changed or require login")
    record = job_record(postings[0], url, "Meta", external_id)
    for value in walk_json(json_documents(text, ("application/json",))):
        details = value.get("xcp_requisition_job_description")
        if not isinstance(details, dict) or str(details.get("id")) != external_id:
            continue
        if details.get("title") != record["title"]:
            raise ValueError("Meta job title did not match its public structured metadata")
        sections = [html_fragment(details.get("description")) or postings[0]["description"]]
        for key, heading in [("responsibilities", "Responsibilities"), ("minimum_qualifications", "Minimum qualifications"), ("preferred_qualifications", "Preferred qualifications")]:
            items = details.get(key) or []
            if not isinstance(items, list):
                raise ValueError("Meta job requirement format changed")
            lines = [str(item["item"]) for item in items if isinstance(item, dict) and item.get("item")]
            if lines:
                sections.append(heading + "\n" + "\n".join(lines))
        for key in ["california_disclaimer", "intl_disclaimer", "boiler_plate_intro", "equal_opportunity_message", "accommodations_message"]:
            fragment = html_fragment(details.get(key))
            if fragment:
                sections.append(fragment)
        record["description"] = "\n\n".join(sections)
        departments = details.get("departments") or []
        if isinstance(departments, list):
            record["domain"] = ", ".join(x for x in departments if isinstance(x, str))
        salaries = []
        for compensation in details.get("public_compensation") or []:
            if not isinstance(compensation, dict):
                continue
            low, high = compensation.get("compensation_amount_minimum"), compensation.get("compensation_amount_maximum")
            amount = " – ".join(dict.fromkeys(str(x) for x in [low, high] if x))
            if amount:
                label = compensation.get("country_code") or ""
                salaries.append(" ".join(filter(None, [label, amount])) + (" + bonus" if compensation.get("has_bonus") else "") + (" + equity" if compensation.get("has_equity") else ""))
        if salaries:
            record["salary"] = "; ".join(salaries)
            record["description"] += "\n\nPublished compensation\n" + record["salary"]
        record["raw"]["meta_public_details"] = {key: details[key] for key in ["id", "title", "locations", "departments", "description", "responsibilities", "minimum_qualifications", "preferred_qualifications", "public_compensation"] if key in details}
        break
    return record


def get_page(client, url):
    for _ in range(4):
        try:
            return client.get_page(url)[0]
        except CareerRedirect as redirect:
            parsed = urlsplit(redirect.url)
            if parsed.hostname != "www.metacareers.com" or parsed.path.startswith(("/login", "/profile/login", "/signup", "/resume")):
                raise CareerPageError("Meta requires login or redirected outside its public job pages", "access_denied")
            url = redirect.url
    raise ValueError("Too many Meta careers redirects")


def fetch_meta(source, max_pages=30, delay=1, timeout=15, client=None, progress=None):
    client = client or CareerPageClient(timeout)
    entries = sitemap_jobs(get_page(client, SITEMAP))
    ids = sorted(entries)
    offset = bisect.bisect_right(ids, source.get("_cursor", "")) % len(ids)
    selected = (ids[offset:] + ids[:offset])[:max_pages]
    jobs, errors, consecutive = [], [], 0
    cursor = source.get("_cursor", "")
    for index, external_id in enumerate(selected):
        if progress:
            progress(f"Meta: reading job {index + 1}/{len(selected)} · {len(entries)} sitemap links discovered")
        time.sleep(delay)
        try:
            jobs.append(extract_job(get_page(client, entries[external_id]), entries[external_id], external_id))
            consecutive = 0
            cursor = external_id
        except CareerPageError as exc:
            if not jobs:
                raise
            errors.append("Collection paused: " + str(exc))
            break
        except Exception as exc:
            errors.append(f"{external_id}: {type(exc).__name__}: {exc}")
            consecutive += 1
            cursor = external_id
            if consecutive >= 3:
                errors.append("Stopped after three consecutive unreadable job pages")
                break
    if not jobs:
        raise ValueError("No Meta job pages could be read; saved jobs and the cursor are preserved. " + (errors[0] if errors else ""))
    note = (f"Meta sitemap contains {len(entries)} job links. Read {len(jobs)} full job pages in a rotating batch of up to {max_pages}. "
            "Later collections continue from the saved position. Inventory is partial; closure detection is disabled.")
    if errors:
        note += f" {len(errors)} warnings; first: {errors[0]}"
    return Snapshot(jobs, False, note, cursor)
