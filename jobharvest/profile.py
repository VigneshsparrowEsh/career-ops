"""Canonical Career-Ops profile adapter. JSON output is valid YAML."""
import json
import math
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from .normalize import SKILLS

LOCK = threading.RLock()


def read_document(path):
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            output = subprocess.check_output(["node", str(Path(__file__).resolve().parents[1] / "scripts/read-yaml.mjs"), str(path)], timeout=10, stderr=subprocess.DEVNULL)
            return json.loads(output)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("Install the Career-Ops dependencies with npm install to read YAML profiles") from exc


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".jobharvest-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def root_for(config):
    return Path(config["_path"]).parent


def get_profile(root):
    with LOCK:
        doc = read_document(root / "config/profile.yml")
        cv = (root / "cv.md").read_text(encoding="utf-8") if (root / "cv.md").exists() else ""
        custom = doc.get("jobharvest", {})
        candidate, location, comp = doc.get("candidate", {}), doc.get("location", {}), doc.get("compensation", {})
        return {
            "name": candidate.get("full_name", ""),
            "roles": doc.get("target_roles", {}).get("primary", []),
            "skills": custom.get("skills", [name for name in SKILLS if name.lower() in cv.lower()]),
            "years_experience": custom.get("years_experience"),
            "country": location.get("country", ""), "city": location.get("city", ""),
            "remote_only": custom.get("remote_only", False),
            "open_to_relocation": custom.get("open_to_relocation", False),
            "authorized_countries": custom.get("authorized_countries", []),
            "preferred_locations": custom.get("preferred_locations", []),
            "salary_target": comp.get("target_range", ""),
            "salary_currency": comp.get("currency", ""),
            "resume": cv, "updated_at": custom.get("updated_at"),
            "ready": bool(doc.get("target_roles", {}).get("primary") and cv.strip()),
        }


def validate_profile(value):
    if not isinstance(value, dict):
        raise ValueError("Expected a career profile")
    clean = {}
    for key, maximum in [("name", 150), ("country", 80), ("city", 100), ("salary_target", 100), ("salary_currency", 10), ("resume", 60000)]:
        text = value.get(key, "")
        if not isinstance(text, str) or len(text) > maximum:
            raise ValueError(f"Invalid {key}; maximum {maximum} characters")
        clean[key] = text.strip()
    for key in ("roles", "skills", "authorized_countries", "preferred_locations"):
        items = value.get(key, [])
        if not isinstance(items, list) or len(items) > 60 or any(not isinstance(x, str) or not x.strip() or len(x) > 100 for x in items):
            raise ValueError(f"{key} must be a list of up to 60 short text values")
        clean[key] = list(dict.fromkeys(x.strip() for x in items))
    if not clean["roles"] or not clean["country"] or not clean["resume"]:
        raise ValueError("Add at least one target role, your country and your résumé")
    years = value.get("years_experience")
    if years in (None, ""):
        years = None
    elif isinstance(years, bool) or not isinstance(years, (int, float)) or not math.isfinite(years) or not 0 <= years <= 60:
        raise ValueError("Experience must be between 0 and 60 years, or blank")
    clean["years_experience"] = years
    for key in ("remote_only", "open_to_relocation"):
        if not isinstance(value.get(key, False), bool):
            raise ValueError(f"Invalid {key}")
        clean[key] = value.get(key, False)
    return clean


def save_profile(root, value):
    from .normalize import utcnow
    clean = validate_profile(value)
    with LOCK:
        path = root / "config/profile.yml"
        doc = read_document(path)
        doc.setdefault("candidate", {}).update(full_name=clean["name"], location=", ".join(filter(None, [clean["city"], clean["country"]])))
        doc.setdefault("target_roles", {})["primary"] = clean["roles"]
        doc.setdefault("location", {}).update(country=clean["country"], city=clean["city"], onsite_availability="Remote only" if clean["remote_only"] else "Open to local and remote roles")
        doc.setdefault("compensation", {}).update(target_range=clean["salary_target"], currency=clean["salary_currency"])
        doc.setdefault("jobharvest", {}).update({k: clean[k] for k in ("skills", "years_experience", "remote_only", "open_to_relocation", "authorized_countries", "preferred_locations")})
        doc["jobharvest"]["updated_at"] = utcnow()
        # Keep the CV as Career-Ops' source of truth; do not send it to an external service.
        atomic_write(root / "cv.md", clean["resume"] + "\n")
        atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        return get_profile(root)
