"""Discover public company boards from career links, without guessing board names."""
import html
import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import ROOT, load
from .robots import RobotsPolicy

USER_AGENT = "JobHarvest/1.0"
CONFIG_LOCK = threading.Lock()
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,150}\Z")
MAX_SITES = 25


def board_from_url(url, company):
    """Return a source for a recognized hosted board or embed; never match lookalike hosts."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").lower()
    parts = [unquote(p) for p in parsed.path.split("/") if p]
    board = None
    region = None
    if host in {"www.metacareers.com", "metacareers.com"} and re.fullmatch(r"/(?:jobsearch/?|profile/job_details/\d+/?|jobs/\d+/?)", parsed.path):
        return {"name": "meta-careers", "type": "meta", "enabled": True, "company": "Meta",
                "min_interval_hours": 6, "career_url": "https://www.metacareers.com/jobsearch/"}
    if host in {"www.google.com", "careers.google.com"} and (parsed.path.startswith("/about/careers/applications/jobs/results") or (host == "careers.google.com" and parsed.path.startswith("/jobs/results"))):
        return {"name": "google-careers", "type": "google", "enabled": True, "company": "Google",
                "min_interval_hours": 6, "career_url": "https://www.google.com/about/careers/applications/jobs/results"}
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"}:
        kind = "greenhouse"
        board = parse_qs(parsed.query).get("for", [None])[0] if parts and parts[0] == "embed" else (parts[0] if parts else None)
    elif host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        kind, board = "lever", parts[0] if parts else None
        region = "eu" if host == "jobs.eu.lever.co" else "global"
    elif host == "jobs.ashbyhq.com":
        kind, board = "ashby", parts[0] if parts else None
    else:
        return None
    if not board or not TOKEN.fullmatch(board):
        return None
    if kind == "greenhouse":
        # The EU Greenhouse data region has a separate API; do not use the global endpoint.
        if ".eu.greenhouse.io" in host:
            return None
        canonical = f"https://job-boards.greenhouse.io/{board}"
    else:
        canonical = f"https://{host}/{board}"
    source = {"name": f"{kind}-{board}" + ("-eu" if region == "eu" else ""),
              "type": kind, "enabled": True, "board": board, "company": company,
              "min_interval_hours": 6, "career_url": canonical}
    if region:
        source["region"] = region
    return source


def validate_public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("Use a public HTTPS career URL without a custom port or login details")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Local and private network addresses cannot be scanned")
    return parsed


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


class CareerPageError(ValueError):
    def __init__(self, message, code="unavailable"):
        super().__init__(message)
        self.code = code


class CareerPageClient:
    """Bounded public-page discovery. A denied or unavailable robots policy stops discovery."""
    def __init__(self, timeout=10):
        self.timeout = min(timeout, 15)
        self.opener = build_opener(PublicRedirects())
        self.policies = {}

    def read(self, url, maximum=2 * 1024 * 1024):
        return self.download(url, self.opener, maximum)

    def download(self, url, opener, maximum=2 * 1024 * 1024):
        for attempt in range(2):
            validate_public_url(url)
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/xml"})
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    body = response.read(maximum + 1)
                    if len(body) > maximum:
                        raise ValueError("Career page exceeded the discovery size limit")
                    return body.decode(response.headers.get_content_charset() or "utf-8", errors="replace"), response.geturl()
            except HTTPError as exc:
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After", "")
                    exc.close()
                    raise CareerPageError("This site temporarily limited requests (HTTP 429). Collection paused; try later" + (f" after {retry_after} seconds." if retry_after.isdigit() else "."), "rate_limited") from exc
                if exc.code in {401, 403}:
                    code = exc.code
                    exc.close()
                    raise CareerPageError(f"This site denied public access (HTTP {code}). Use an accessible feed or import an authorized export; no login or challenge is bypassed.", "access_denied") from exc
                if exc.code in {500, 502, 503, 504} and attempt == 0:
                    exc.close()
                    time.sleep(2)
                    continue
                raise

    def get_page(self, url):
        parsed = validate_public_url(url)
        origin = f"https://{parsed.netloc}"
        if origin not in self.policies:
            try:
                text, _ = self.read(origin + "/robots.txt", 256 * 1024)
                policy = RobotsPolicy(text)
            except HTTPError as exc:
                if exc.code != 404:
                    raise ValueError("Career page discovery could not read the site's robots policy") from exc
                policy = RobotsPolicy("")
            self.policies[origin] = policy
        if not self.policies[origin].can_fetch(USER_AGENT, url):
            raise CareerPageError("This site does not permit career-page discovery at this URL; use an allowed public job feed instead", "robots_denied")
        # Check destination robots policy before reading a redirected career page.
        class CareerRedirects(HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, message, headers, newurl):
                validate_public_url(newurl)
                raise CareerRedirect(newurl)
        opener = build_opener(CareerRedirects())
        return self.download(url, opener)


class CareerRedirect(Exception):
    def __init__(self, url):
        self.url = url


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and key in {"href", "src", "data-src", "data-url"}:
                self.urls.append(value)


def discover_company_sites(url, company, client=None):
    """Find boards on the supplied page and at most three same-host careers/jobs links."""
    direct = board_from_url(url, company)
    if direct:
        return [direct]
    client = client or CareerPageClient()
    queue = [url]
    visited = set()
    sources = {}
    while queue and len(visited) < 4:
        page = queue.pop(0)
        if page in visited:
            continue
        visited.add(page)
        try:
            text, final = client.get_page(page)
        except CareerRedirect as redirect:
            direct = board_from_url(redirect.url, company)
            if direct:
                sources[direct["name"]] = direct
            else:
                queue.insert(0, redirect.url)
            continue
        parser = Links()
        parser.feed(text)
        # Recognize embedded board links inside JSON/script strings as well as links/iframes.
        decoded = html.unescape(text).replace("\\/", "/").replace("\\u0026", "&")
        urls = parser.urls + re.findall(r'https?://(?:job-boards\.greenhouse\.io|boards\.greenhouse\.io|jobs\.(?:eu\.)?lever\.co|jobs\.ashbyhq\.com)/[^\s<>"\x27\\]+', decoded)
        for value in urls:
            link = urljoin(final, value)
            source = board_from_url(link, company)
            if source:
                sources[source["name"]] = source
            elif urlsplit(link).hostname == urlsplit(final).hostname and re.search(r"/(?:careers?|jobs?|join-us|open-positions)(?:/|$)", urlsplit(link).path, re.I):
                if link not in visited and link not in queue and len(queue) < 3:
                    queue.append(link)
        if sources:
            break
        from .structured_jobs import job_postings, job_record
        postings = job_postings(text)
        valid = False
        for posting in postings:
            try:
                job_record(posting, final, company)
                valid = True
                break
            except ValueError:
                continue
        if valid:
            from .normalize import canonical_url
            career_url = canonical_url(final)
            name = "career-page-" + hashlib.sha256(career_url.encode()).hexdigest()[:12]
            sources[name] = {"name": name, "type": "structured", "company": company, "enabled": True,
                             "career_url": career_url, "min_interval_hours": 6}
            break
    if not sources:
        raise ValueError("No supported public board or JobPosting metadata found. Google, Meta, Greenhouse, Lever and Ashby are supported. Other JavaScript-only systems need another connector.")
    if len(sources) > 5:
        raise ValueError("This page links to many company boards; use the individual company's board URL")
    return list(sources.values())


def parse_sites(text):
    """Each nonempty line is Company name | https://career-url."""
    if not isinstance(text, str) or len(text) > 24000:
        raise ValueError("Career sites must be text under 24,000 characters")
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        company, separator, url = line.partition("|")
        company, url = company.strip(), url.strip()
        if not separator or not company or len(company) > 150 or len(url) > 2000 or urlsplit(url).scheme != "https":
            raise ValueError("Use one site per line: Company name | https://career-url")
        entries.append({"company": company, "url": url})
    if not 1 <= len(entries) <= MAX_SITES:
        raise ValueError(f"Add between 1 and {MAX_SITES} company sites at a time")
    return entries


def starter_sites():
    return json.loads((ROOT / "company-sites.json").read_text(encoding="utf-8"))


def add_company_sites(config_path, entries, client=None, progress=None):
    """Discover independently, persist new boards atomically, preserve existing settings."""
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_SITES:
        raise ValueError(f"Add between 1 and {MAX_SITES} company sites at a time")
    client = client or CareerPageClient()
    outcomes, discovered = [], []
    for index, entry in enumerate(entries):
        if progress:
            progress(f"Finding boards: {index + 1}/{len(entries)} · {entry['company']}")
        try:
            sources = discover_company_sites(entry["url"], entry["company"], client)
            discovered.extend(sources)
            outcomes.append({**entry, "status": "found", "sources": [s["name"] for s in sources]})
        except Exception as exc:
            status = "blocked" if isinstance(exc, CareerPageError) else "unsupported"
            outcomes.append({**entry, "status": status, "message": str(exc)[:500]})
    added, existing, disabled = [], [], []
    with CONFIG_LOCK:
        config = load(config_path)
        identity = lambda s: (s["type"], s.get("career_url") if s["type"] == "structured" else s.get("board"), s.get("region", "global") if s["type"] == "lever" else "")
        known = {identity(s): s for s in config["sources"]}
        names = {s["name"] for s in config["sources"]}
        for source in discovered:
            key = identity(source)
            if key in known:
                bucket = existing if known[key].get("enabled", True) else disabled
                bucket.append(known[key]["name"])
                continue
            base, suffix = source["name"], 2
            while source["name"] in names:
                source["name"] = f"{base}-{suffix}"
                suffix += 1
            config["sources"].append(source)
            known[key] = source
            names.add(source["name"])
            added.append(source["name"])
        if added:
            path = Path(config["_path"])
            clean = {key: value for key, value in config.items() if not key.startswith("_")}
            fd, temporary = tempfile.mkstemp(prefix=".company-sites-", suffix=".json", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(clean, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
                load(temporary)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    return {"added": added, "existing": sorted(set(existing)), "disabled": sorted(set(disabled)), "sites": outcomes}
