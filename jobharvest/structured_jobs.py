"""Reusable extraction for public schema.org JobPosting metadata."""
import json
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .normalize import canonical_url


class JSONScripts(HTMLParser):
    def __init__(self, types=("application/ld+json",)):
        super().__init__(convert_charrefs=False)
        self.types, self.active, self.parts, self.documents = types, False, [], []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = dict(attrs).get("type", "").lower() in self.types
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.active = False
            try:
                self.documents.append(json.loads("".join(self.parts)))
            except ValueError:
                # An unrelated broken metadata block must not hide a valid job block.
                pass


def json_documents(text, types=("application/ld+json",)):
    parser = JSONScripts(types)
    parser.feed(text)
    return parser.documents


def walk_json(documents, limit=30000):
    stack = [(d, 0) for d in reversed(documents)]
    count = 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if count > limit:
            raise ValueError("Structured career data exceeded the extraction limit")
        if isinstance(value, dict):
            yield value
            children = value.values()
        elif isinstance(value, list):
            children = value
        else:
            continue
        if depth < 40:
            stack.extend((child, depth + 1) for child in reversed(list(children)))


def job_postings(text):
    return [value for value in walk_json(json_documents(text))
            if "JobPosting" in (value.get("@type") if isinstance(value.get("@type"), list) else [value.get("@type")])]


def salary_text(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    amount = value.get("value")
    if not isinstance(amount, dict):
        amount = {"value": amount}
    low, high, fixed = amount.get("minValue"), amount.get("maxValue"), amount.get("value")
    if low is not None and high is not None:
        numbers = f"{low}–{high}"
    elif fixed is not None:
        numbers = str(fixed)
    elif low is not None:
        numbers = f"{low}+"
    elif high is not None:
        numbers = f"up to {high}"
    else:
        return None
    return " ".join(x for x in [str(value.get("currency") or ""), numbers, str(amount.get("unitText") or "")] if x)


def job_record(posting, page_url, company, external_id=None):
    title, description = posting.get("title"), posting.get("description")
    if not isinstance(title, str) or not title.strip() or not isinstance(description, str) or not description.strip():
        raise ValueError("JobPosting requires a title and a description")
    supplied_url = posting.get("url") or page_url
    if not isinstance(supplied_url, str):
        raise ValueError("JobPosting URL must be text")
    url = urljoin(page_url, supplied_url)
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname != urlsplit(page_url).hostname:
        raise ValueError("JobPosting links must stay on the supplied public career site")
    url = canonical_url(url)
    organization = posting.get("hiringOrganization") or {}
    if not isinstance(organization, dict):
        organization = {}
    locations, countries = [], set()
    places = posting.get("jobLocation") or []
    if isinstance(places, dict):
        places = [places]
    if not isinstance(places, list):
        raise ValueError("JobPosting locations must be an object or list")
    for place in places:
        if not isinstance(place, dict):
            continue
        address = place.get("address") or {}
        if isinstance(address, str):
            locations.append(address)
            continue
        if not isinstance(address, dict):
            continue
        country = address.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        if isinstance(country, str) and country:
            countries.add(country)
        label = place.get("name") or ", ".join(str(address[key]) for key in ["addressLocality", "addressRegion", "addressCountry"] if isinstance(address.get(key), str) and address[key])
        if isinstance(label, str) and label:
            locations.append(label)
    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("value")
    if not isinstance(identifier, (str, int)):
        identifier = None
    sections = [description]
    for key, heading in [("responsibilities", "Responsibilities"), ("qualifications", "Qualifications"),
                         ("skills", "Skills"), ("experienceRequirements", "Experience"), ("jobBenefits", "Benefits")]:
        value = posting.get(key)
        if isinstance(value, list):
            value = "\n".join(x for x in value if isinstance(x, str))
        if isinstance(value, str) and value:
            sections.append(heading + "\n" + value.replace("&nbsp;", "\n"))
    employment = posting.get("employmentType")
    if isinstance(employment, list):
        employment = ", ".join(x for x in employment if isinstance(x, str))
    if isinstance(employment, str):
        employment = employment.replace("_", " ").title()
    remote = posting.get("jobLocationType") == "TELECOMMUTE"
    return {"external_id": str(external_id or identifier or url), "title": title,
            "company": organization.get("name") or company, "description": "\n\n".join(sections),
            "location": "; ".join(dict.fromkeys(locations)), "country": next(iter(countries)) if len(countries) == 1 else None,
            "employment_type": employment, "remote": True if remote else None,
            "salary": salary_text(posting.get("baseSalary")), "published_at": posting.get("datePosted"),
            "url": url, "raw": {"schema_org": posting}}


def fetch_structured(source, client=None):
    from .company_sites import CareerPageClient, CareerRedirect
    from .sources import Snapshot
    client = client or CareerPageClient()
    url = source["career_url"]
    for _ in range(4):
        try:
            text, final = client.get_page(url)
            break
        except CareerRedirect as redirect:
            if urlsplit(redirect.url).hostname != urlsplit(url).hostname:
                raise ValueError("Career page redirected to another site; add the final career URL")
            url = redirect.url
    else:
        raise ValueError("Too many career page redirects")
    postings = job_postings(text)
    if not postings:
        raise ValueError("No JobPosting metadata found. The page may have changed or require JavaScript/login; saved jobs are preserved.")
    jobs, errors, urls = [], [], set()
    for posting in postings:
        try:
            if len(postings) > 1 and not posting.get("url"):
                raise ValueError("Multiple JobPosting records require separate job URLs")
            record = job_record(posting, final, source["company"])
            if record["url"] in urls:
                raise ValueError("Multiple JobPosting records share the same job URL")
            urls.add(record["url"])
            jobs.append(record)
        except ValueError as exc:
            errors.append(str(exc))
    if not jobs:
        raise ValueError("No valid public JobPosting records found: " + errors[0])
    note = "Structured jobs from the supplied page only; complete company inventory is not confirmed, so closure detection is disabled."
    if errors:
        note += f" {len(errors)} invalid metadata blocks were skipped: {errors[0]}"
    return Snapshot(jobs, False, note)
