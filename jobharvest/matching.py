"""Explainable preliminary ranking, separate from Career-Ops' A–G evaluation."""
import re
from functools import lru_cache
from datetime import datetime, timezone
from .normalize import SKILLS

COUNTRIES = {
    "India": ["india", "in"], "United States": ["united states", "usa", "us", "u.s.", "united states of america"],
    "United Kingdom": ["united kingdom", "uk", "gb", "great britain"], "Canada": ["canada", "ca"],
    "Germany": ["germany", "de"], "France": ["france", "fr"], "Australia": ["australia", "au"],
    "Singapore": ["singapore", "sg"], "Netherlands": ["netherlands", "nl"], "Spain": ["spain", "es"],
    "Ireland": ["ireland", "ie"], "Poland": ["poland", "pl"], "Portugal": ["portugal", "pt"],
    "Brazil": ["brazil", "br"], "Mexico": ["mexico", "mx"], "Japan": ["japan", "jp"],
}
ALIASES = {alias.casefold(): country for country, names in COUNTRIES.items() for alias in names}
GROUPS = [
    ("business analysis", r"\bbusiness (?:systems? )?analyst\b|\bbusiness analysis\b|\bbusiness intelligence\b"),
    ("supply chain", r"\bsupply[ -]chain\b|\blogistics? (?:analyst|planning|manager)\b|\binventory (?:analyst|planning)\b|\bdemand plann"),
    ("data analysis", r"\bdata analyst\b|\bdata analysis\b|\banalytics? (?:analyst|engineer|manager|consultant)\b|\bbi analyst\b"),
    ("market research", r"\bmarket(?:ing)? research(?:er)?\b|\bmarket intelligence\b|\bconsumer insights?\b"),
    ("operations", r"\boperations?\b|\bops analyst\b"),
    ("project management", r"\bproject (?:manager|management|coordinator|analyst)\b|\bprogram (?:manager|management)\b|\bpmo\b"),
]


def contains(text, phrase):
    folded, term = text.casefold(), phrase.casefold()
    if term not in folded:
        return False
    return bool(word_pattern(term).search(folded))


@lru_cache(maxsize=500)
def word_pattern(term):
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")


def canon_country(value):
    return ALIASES.get((value or "").strip().casefold(), (value or "").strip())


def location_fit(job, profile):
    location = job.get("location", "")
    description = job.get("description", "")
    home = canon_country(profile["country"])
    country = canon_country(job.get("country"))
    remote = job.get("workplace_type") == "remote"
    work = job.get("workplace_type", "unknown")
    authorized = {canon_country(x) for x in profile.get("authorized_countries", [])}
    # Recognize country names in location, not ambiguous two-letter city/state abbreviations.
    located = {name for name, aliases in COUNTRIES.items() if any(contains(location, a) for a in aliases if len(a) > 3)}
    if country in COUNTRIES:
        located.add(country)
    for name, pattern in [("United States", r"\bUS\b|\bUSA\b|U\.S\."), ("United Kingdom", r"\bUK\b|\bU\.K\.")]:
        if re.search(pattern, location):
            located.add(name)
    india_city = bool(re.search(r"\b(?:bengaluru|bangalore|mumbai|chennai|hyderabad|pune|delhi|gurugram|noida|india)\b", location, re.I))
    local = home in located or (home == "India" and india_city)
    worldwide = bool(re.search(r"\b(?:worldwide|anywhere in the world|global remote|remote globally|any country)\b", location, re.I))
    # Explicit requirements take precedence over a broad 'remote' label.
    restricted = re.search(r"\b(?:must (?:be (?:based|located|resident)|reside|live)|only (?:candidates|applicants) (?:based|located)|remote (?:only )?(?:within|in)|(?:candidates|applicants) must (?:be based|reside))\s+(?:in\s+)?([^.;\n]{2,85})", description, re.I)
    restriction = restricted.group(1) if restricted else ""
    restricted_countries = {name for name, aliases in COUNTRIES.items() if any(contains(restriction, a) for a in aliases if len(a) > 2 or a in ("us", "uk"))}
    only = re.search(r"\b(US|USA|UK|United States|United Kingdom|Canada|Europe|EU)[ -]only\b", description, re.I)
    if only and canon_country(only.group(1)) != home:
        return "mismatch", "The description restricts the hiring region", [only.group(0)]
    warnings = []
    if profile.get("remote_only") and work in {"onsite", "hybrid"}:
        return "mismatch", "This role requires onsite or hybrid work", ["Your profile requests remote-only work"]
    if remote:
        if restricted_countries and home not in restricted_countries:
            return "mismatch", "Remote role has a country restriction", [restricted.group(0)]
        if local:
            return "compatible", "Remote location includes your country or worldwide", ["Confirm employer hiring coverage and working hours"]
        if located and home not in located:
            return "mismatch", "Remote hiring location is outside your country", ["Remote does not imply worldwide hiring"]
        if re.search(r"\b(?:europe|emea|latam|americas|north america|eu only|us only|usa|u\.s\.|united states)\b", location, re.I):
            return "mismatch", "Remote role is limited to another region", ["Check the listed hiring region"]
        if worldwide:
            return "compatible", "Remote listing explicitly includes worldwide hiring", ["Confirm employer hiring coverage and working hours"]
        return "review", "Remote eligibility from your country is unconfirmed", ["Employer hiring coverage and timezone overlap need checking"]
    if local:
        if profile.get("remote_only"):
            return "review", "Local role; remote policy is unknown", ["Confirm fully remote availability"]
        return "compatible", "Job location matches your country", ["Confirm work authorization and workplace arrangement"]
    if located or (location and location.lower() != "unknown"):
        if profile.get("open_to_relocation"):
            if located & authorized:
                return "review", "Relocation destination matches a stated work authorization", ["Confirm employer requirements and relocation support"]
            return "review", "International role; relocation needs checking", ["Visa sponsorship and relocation support are unconfirmed"]
        return "mismatch", "International location requires relocation", ["Relocation is not enabled in your profile"]
    return "review", "Work location is not supplied", ["Location and work authorization need checking"]


@lru_cache(maxsize=30)
def role_rules(roles):
    patterns = [re.compile(pattern, re.I) for _, pattern in GROUPS if any(re.search(pattern, target, re.I) for target in roles)]
    words = {w for r in roles for w in re.findall(r"[a-z]+", r.lower()) if w not in {"analyst", "manager", "management", "researcher"}}
    return patterns, words


@lru_cache(maxsize=50000)
def role_alignment(title, roles):
    patterns, words = role_rules(roles)
    if any(contains(title, target) for target in roles):
        return 45
    if any(pattern.search(title) for pattern in patterns):
        return 40
    return 18 if any(contains(title, word) for word in words) else 0


def score_job(job, profile, now=None, location=None):
    title = job["role"]
    body = job.get("description", "")
    reasons, gaps = [], []
    role_score = role_alignment(title, tuple(profile["roles"]))
    adjacent = bool(re.search(r"\b(?:engineer|developer|architect|scientist|devops|sre)\b", title, re.I)) and not any(re.search(r"\b(?:engineer|developer|architect|scientist|devops|sre)\b", target, re.I) for target in profile["roles"])
    if adjacent:
        role_score = min(role_score, 18)
        gaps.append("Technical role outside your target titles; review specialist requirements")
    if role_score:
        reasons.append("Title aligns with your target roles" if role_score >= 40 else "Related title; review responsibilities")
    text = title + "\n" + body
    matched = [s for s in profile.get("skills", []) if any(contains(text, a) for a in SKILLS.get(s, [s]))]
    skill_score = min(25, len(matched) * 5)
    if matched:
        reasons.append("Skills mentioned: " + ", ".join(matched[:6]))
    candidate_skills = {s.casefold() for s in profile.get("skills", [])}
    missing = [s["name"] for s in job.get("skills", []) if s["name"].casefold() not in candidate_skills]
    if missing:
        gaps.append("Other skills mentioned in the JD: " + ", ".join(missing[:6]) + " (may be optional)")
    if not matched:
        gaps.append("No explicit overlap with your saved skills")
    experience_score = 5
    years, minimum = profile.get("years_experience"), job.get("experience_min_years")
    if years is not None and minimum is not None:
        if years >= minimum:
            experience_score = 10
            reasons.append(f"Your {years:g} years meet the {minimum:g}+ years mentioned")
        else:
            experience_score = 0
            gaps.append(f"JD mentions {minimum:g}+ years; your profile has {years:g} (verify the requirement)")
    else:
        gaps.append("Experience requirement or your experience is unconfirmed")
    eligibility, location_reason, warnings = location or location_fit(job, profile)
    location_score = {"compatible": 15, "review": 5, "mismatch": 0}[eligibility]
    reasons.append(location_reason)
    freshness = 0
    stale = False
    observed = job.get("last_seen_at")
    if observed:
        try:
            observed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            age = ((now or datetime.now(timezone.utc)) - observed).days
            freshness = 5 if age <= 7 else 2 if age <= 30 else 0
            stale = age > 14
        except (ValueError, TypeError):
            pass
    if stale:
        warnings.append("Not seen in a recent collection; confirm the original listing is still open")
    if not job.get("salary"):
        warnings.append("Salary is not supplied; compensation fit is unknown")
    if not job.get("active", True):
        warnings.append("This listing is marked inactive")
    score = role_score + skill_score + experience_score + location_score + freshness
    if eligibility == "mismatch":
        score = min(score, 39)
    if not role_score:
        score = min(score, 29)
    if adjacent:
        score = min(score, 59)
    if eligibility == "review":
        score = min(score, 79)
    return {"score": score, "label": "Strong potential" if score >= 80 else "Worth reviewing" if score >= 60 else "Possible fit" if score >= 40 else "Low fit", "eligibility": eligibility, "reasons": reasons, "gaps": gaps, "warnings": warnings, "matched_skills": matched, "role_relevant": role_score >= 18, "stale": stale, "method": "Preliminary rules-based fit, not a probability or Career-Ops evaluation"}


def matches(db, profile, params, limit=25, offset=0):
    if not profile["ready"]:
        return {"total": 0, "jobs": [], "limit": limit, "offset": offset, "needs_profile": True}
    candidates = db.matching_candidates(dict(params, exclude_hidden="true"), lambda title: role_alignment(title, tuple(profile["roles"])) >= 18)
    ranked = []
    for job in candidates:
        location = location_fit(job, profile)
        if params.get("include_restricted") != "true" and location[0] == "mismatch":
            continue
        if params.get("eligible_only") == "true" and location[0] != "compatible":
            continue
        match = score_job(job, profile, location=location)
        if match["score"] < int(params.get("min_score", 0)):
            continue
        job["match"] = match
        job.pop("description", None)
        ranked.append(job)
    ranked.sort(key=lambda j: (j["match"]["score"], j["last_seen_at"], j["id"]), reverse=True)
    return {"total": len(ranked), "jobs": ranked[offset:offset+limit], "limit": limit, "offset": offset, "needs_profile": False}
