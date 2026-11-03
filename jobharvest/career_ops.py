"""Hand off selected jobs to the existing Career-Ops inbox, without fake evaluations."""
import json
import re
import threading
from urllib.parse import urlsplit
from .profile import atomic_write
from .normalize import utcnow

LOCK = threading.Lock()


def connection(root):
    # Read the local remote configuration; never fetch or push from an HTTP request.
    config = root / ".git/config"
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    match = re.search(r'\[remote "origin"\][\s\S]*?url\s*=\s*(\S+)', text)
    return {"connected": bool(match), "repository": match.group(1) if match else None,
            "profile": "config/profile.yml", "resume": "cv.md", "inbox": "data/pipeline.md"}


def queue_job(root, job):
    sources = [s for s in job["sources"] if s["active"]] or job["sources"]
    if not sources:
        raise ValueError("This job has no source link")
    url = sources[0]["url"]
    if urlsplit(url).scheme not in {"https", "http"} or any(c in url for c in "\r\n| "):
        raise ValueError("Invalid original listing URL")
    with LOCK:
        inbox = root / "data/pipeline.md"
        text = inbox.read_text(encoding="utf-8") if inbox.exists() else "# Job inbox\n\n## Pendientes\n\n## Procesadas\n"
        if re.search(r"(?<!\S)" + re.escape(url) + r"(?=\s|$)", text):
            return {"status": "existing", "message": "Already in your Career-Ops inbox"}
        clean = lambda s: re.sub(r"[|\r\n]", " ", s)
        entry = f"- [ ] {url} | {clean(job['company'])} | {clean(job['role'])}\n"
        if "## Pendientes" not in text:
            text += "\n## Pendientes\n"
        text = text.replace("## Pendientes", "## Pendientes\n" + entry, 1)
        # Keep a full JD alongside the source URL for subsequent Career-Ops modes.
        atomic_write(root / "jds" / f"jobharvest-{job['id']}.md", f"# {job['role']}\n\nCompany: {job['company']}\nSource: {url}\nCollected: {job['last_seen_at']}\n\n{job['description']}\n")
        atomic_write(inbox, text)
        return {"status": "queued", "message": "Added to Career-Ops inbox for a full evaluation"}
