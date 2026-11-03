"""Conservative normalization. Inferred fields are explicitly identified."""
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(html.unescape(str(value or "")))
    lines = [re.sub(r"[ \t\r]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def canonical_url(value):
    p = urlsplit(str(value or ""))
    if p.scheme not in {"https", "http"} or not p.netloc:
        return None
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k.lower() not in {"ref", "source"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))


SKILLS = {
    "Python": ["python"], "JavaScript": ["javascript"], "TypeScript": ["typescript"],
    "Java": ["java"], "C++": ["c++"], "C#": ["c#"], "Go": ["golang", "go programming"],
    "Rust": ["rust"], "PHP": ["php"], "Ruby": ["ruby"], "SQL": ["sql"],
    "React": ["react", "react.js", "reactjs"], "Angular": ["angular"], "Vue": ["vue", "vue.js"],
    "Node.js": ["node.js", "nodejs"], "Django": ["django"], "FastAPI": ["fastapi"],
    "Spring": ["spring boot", "spring framework"], ".NET": [".net", "dotnet"],
    "PostgreSQL": ["postgresql", "postgres"], "MySQL": ["mysql"], "MongoDB": ["mongodb"],
    "Redis": ["redis"], "AWS": ["aws", "amazon web services"], "Azure": ["azure"],
    "Google Cloud": ["google cloud", "gcp"], "Docker": ["docker"], "Kubernetes": ["kubernetes", "k8s"],
    "Terraform": ["terraform"], "Git": ["git"], "Linux": ["linux"], "CI/CD": ["ci/cd"],
    "Machine Learning": ["machine learning"], "Deep Learning": ["deep learning"],
    "PyTorch": ["pytorch"], "TensorFlow": ["tensorflow"], "Pandas": ["pandas"],
    "Excel": ["excel"], "Power BI": ["power bi"], "Tableau": ["tableau"],
    "Figma": ["figma"], "UX Design": ["ux design", "user experience design"],
    "Salesforce": ["salesforce"], "SAP": ["sap"], "SEO": ["seo", "search engine optimization"],
    "Project Management": ["project management"], "Agile": ["agile"], "Scrum": ["scrum"],
    "Accounting": ["accounting"], "Financial Analysis": ["financial analysis"],
    "Customer Service": ["customer service"], "Communication": ["communication skills"],
    "Negotiation": ["negotiation"], "Recruitment": ["recruitment"], "Copywriting": ["copywriting"],
    "Nursing": ["nursing"], "AutoCAD": ["autocad"], "SolidWorks": ["solidworks"],
}


def infer_skills(text):
    return sorted(name for name, aliases in SKILLS.items() if any(
        re.search(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", text, re.I) for alias in aliases))


def experience(title, description):
    # Title is stronger evidence than a passing mention in the description.
    rules = [("Internship", r"\b(intern|internship|trainee)\b"),
             ("Executive", r"\b(chief|vice president|vp|cto|ceo|cfo)\b"),
             ("Lead / Management", r"\b(lead|principal|staff|head|director|manager)\b"),
             ("Senior", r"\b(senior|sr\.?|III)\b"),
             ("Entry level", r"\b(junior|jr\.?|graduate|entry[- ]level|fresher)\b")]
    level = next((name for name, pat in rules if re.search(pat, title, re.I)), "Unknown")
    matches = re.findall(r"\b(\d{1,2})\s*(?:[-–]|to)\s*(\d{1,2})\s*(?:years?|yrs?)\b", description, re.I)
    single = re.findall(r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:relevant\s+|professional\s+|work\s+)?experience\b", description, re.I)
    # Only a single explicit requirement is treated as a range; do not blend unrelated numbers.
    low = high = None
    evidence = None
    if len(matches) == 1:
        low, high = map(int, matches[0])
        if low > high or high > 50:
            low = high = None
        else:
            evidence = f"{low}-{high} years mentioned in description"
    elif not matches and len(single) == 1 and int(single[0]) <= 50:
        low = int(single[0])
        evidence = f"{low}+ years mentioned in description"
    return level, low, high, evidence


def infer_domain(title):
    rules = [
        ("Data & AI", r"data|machine learning|artificial intelligence|\bAI\b|analytics"),
        ("Design", r"designer|\bUX\b|\bUI\b"),
        ("Software & IT", r"software|developer|devops|cloud|security|\bIT\b|\bSRE\b|frontend|backend|full.?stack"),
        ("Sales", r"sales|account executive|business development"),
        ("Marketing", r"marketing|\bSEO\b|content|copywriter"),
        ("Finance", r"finance|financial|accountant|accounting|audit"),
        ("People & HR", r"recruit|human resources|\bHR\b|talent acquisition"),
        ("Customer Support", r"customer|support|service representative"),
        ("Healthcare", r"nurs|physician|medical|clinical|healthcare"),
        ("Engineering", r"engineer|mechanical|electrical|civil"),
        ("Operations", r"operations|logistics|supply chain|warehouse"),
        ("Product", r"product manager|product owner"),
        ("Legal", r"legal|lawyer|counsel"),
        ("Education", r"teacher|professor|educator|tutor"),
    ]
    return next((name for name, pattern in rules if re.search(pattern, title, re.I)), "Unknown")


def normalize(record, source):
    title = plain(record.get("title"))
    company = plain(record.get("company"))
    url = canonical_url(record.get("url"))
    external_id = str(record.get("external_id") or url or "")
    if not title or not company or not url or not external_id:
        raise ValueError("Job requires title, company, ID and an HTTP(S) source link")
    jd = plain(record.get("description"))
    location = plain(record.get("location")) or "Unknown"
    supplied_domain = plain(record.get("domain"))
    level, low, high, evidence = experience(title, jd)
    supplied_level = plain(record.get("experience_level"))
    raw_skills = record.get("skills") or []
    if not isinstance(raw_skills, list):
        raw_skills = []
    explicit = sorted(set(plain(s) for s in raw_skills if plain(s)))
    extracted = infer_skills(title + "\n" + jd)
    remote = record.get("remote")
    workplace = record.get("workplace_type") or ("remote" if remote is True else "Unknown")
    if workplace == "Unknown":
        workplace_text = title + " " + location
        if re.search(r"\bhybrid\b", workplace_text, re.I):
            workplace = "hybrid"
        elif re.search(r"\bremote\b|\bhome[ -]based\b|\bwork(?:ing)? from home\b", workplace_text, re.I):
            workplace = "remote"
        elif re.search(r"\boffice[ -]based\b|\bon[ -]?site\b", workplace_text, re.I):
            workplace = "onsite"
    fingerprint_text = [title, company, location, jd or url]
    fingerprint = hashlib.sha256("\0".join(re.sub(r"\s+", " ", s).casefold() for s in fingerprint_text).encode()).hexdigest()
    published = record.get("published_at")
    if isinstance(published, (float, int)):
        published = datetime.fromtimestamp(published, timezone.utc).isoformat()
    data = {
        "fingerprint": fingerprint, "role": title, "company": company,
        "domain": supplied_domain or infer_domain(title), "domain_inferred": not bool(supplied_domain),
        "description": jd, "location": location, "country": record.get("country") or None,
        "experience_level": supplied_level or level, "experience_inferred": not bool(supplied_level) and level != "Unknown",
        "experience_min_years": low, "experience_max_years": high, "experience_evidence": evidence,
        "workplace_type": workplace, "employment_type": plain(record.get("employment_type")) or "Unknown",
        "salary": plain(record.get("salary")) or None, "published_at": published,
        "skills": sorted(set(explicit + extracted)), "explicit_skills": explicit,
        "url": url, "source": source, "external_id": external_id,
        "raw": record.get("raw", record),
    }
    data["content_hash"] = hashlib.sha256(json.dumps({k: v for k, v in data.items() if k not in {"raw", "content_hash", "source", "external_id", "url"}}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return data


def matches_filters(job, filters):
    roles = filters.get("roles", [])
    locations = filters.get("locations", [])
    return ((not roles or any(r.casefold() in job["role"].casefold() for r in roles))
            and (not locations or any(l.casefold() in job["location"].casefold() for l in locations))
            and (not filters.get("remote_only") or job["workplace_type"] == "remote"))
