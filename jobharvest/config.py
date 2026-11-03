import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]


def tz(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {"Asia/Kolkata", "Asia/Calcutta"}:
            return timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
        raise ValueError("Install Python tzdata to use this timezone on Windows: " + name)


def load(path=None):
    config_path = Path(path or ROOT / "config.json").resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["_path"] = str(config_path)
    config["_database"] = str((config_path.parent / config["database"]).resolve())
    schedule = config["schedule"]
    datetime.strptime(schedule["time"], "%H:%M")
    tz(schedule["timezone"])
    sources = config["sources"]
    names = [s["name"] for s in sources]
    if len(names) != len(set(names)):
        raise ValueError("Source names must be unique")
    for s in sources:
        if s["type"] not in {"remotive", "arbeitnow", "greenhouse", "lever", "ashby", "google", "meta", "structured"}:
            raise ValueError("Unsupported source type: " + s["type"])
        if s.get("min_interval_hours", 1) < (6 if s["type"] == "remotive" else 0.25):
            raise ValueError("Source polling interval is too short: " + s["name"])
    settings = config["collection"]
    for key, low, high in [("timeout_seconds", 1, 120), ("retries", 0, 3), ("max_pages", 1, 100), ("close_after_missing_runs", 1, 30), ("page_delay_seconds", 0.5, 30)]:
        if not low <= settings[key] <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
    filters = config.get("filters", {})
    for key in ["roles", "locations"]:
        if not isinstance(filters.get(key, []), list) or any(not isinstance(x, str) for x in filters.get(key, [])):
            raise ValueError("Filter values must be lists of text")
    return config


def schedule_slot(config, now=None):
    zone = tz(config["schedule"]["timezone"])
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    hour, minute = map(int, config["schedule"]["time"].split(":"))
    scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local < scheduled:
        scheduled -= timedelta(days=1)
    return scheduled.isoformat()


def next_schedule(config, now=None):
    slot = datetime.fromisoformat(schedule_slot(config, now))
    return (slot + timedelta(days=1)).isoformat()
