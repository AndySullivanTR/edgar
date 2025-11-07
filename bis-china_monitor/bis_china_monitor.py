#!/usr/bin/env python3
"""
BIS/China Monitor — JSON Backfill + Global Polling with Email Alerts

- Startup backfill: Uses SEC submissions JSON per company for last BACKFILL_DAYS
- Polling: Global "current events" Atom feed every CHECK_INTERVAL seconds
- Filters to target CIKs and forms (8-K, 10-Q, 10-K, 6-K)
- Resolves primary document, finds BIS/China passages, classifies NEW vs BOILERPLATE
- Sends email alerts when NEW matches are found
- Persists 'seen' filing URLs and 'events_seen' for cross-filing de-dupe
"""

import os
import re
import json
import time
import httpx
import random
import hashlib
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# =========================
# Config
# =========================
CHECK_INTERVAL = 15 * 60              # 15 minutes
BACKFILL_ON_START = False
BACKFILL_DAYS = 200                   # ~6–7 months
TARGET_FORMS = {"8-K", "10-Q", "10-K", "6-K"}  # include 6-K for foreign issuers
RUN_ONCE = os.getenv("RUN_ONCE", "false").lower() == "true"  # For GitHub Actions - check once and exit

USER_AGENT = "Andy Sullivan andy.sullivan@thomsonreuters.com"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Email configuration
GMAIL_USER = os.getenv("GMAIL_USER", "")  # Your Gmail address
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")  # Gmail App Password
EMAIL_RECIPIENTS = os.getenv("EMAIL_RECIPIENTS", "andy.sullivan@thomsonreuters.com,chris.sanders@thomsonreuters.com").split(",")
SEND_EMAILS = bool(GMAIL_USER and GMAIL_APP_PASSWORD)  # Only send if credentials are configured

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "."))
SEEN_FILE = OUTPUT_DIR / "bis_seen_filings.json"
EVENTS_FILE = OUTPUT_DIR / "bis_events_seen.json"

FRESHNESS_DAYS_FOR_EVENT = 60         # dated events older than this before filing → stale
EVENT_DEDUPE_WINDOW_DAYS = 180        # per-CIK event repeat suppression
DEBUG_GUARDS = True                   # set False to silence guard-prints

# =========================
# Company universe (SOX + CDNS + SNPS)
# NOTE: Fixed CIKs for ARM and ONTO (ONTO uses Nanometrics' legacy CIK)
# =========================
COMPANIES = {
    'AMD': ('Advanced Micro Devices Inc', '0000002488'),
    'ADI': ('Analog Devices Inc', '0000006281'),
    'AMAT': ('Applied Materials Inc', '0000006951'),
    'AMKR': ('Amkor Technology Inc', '0001047127'),
    'ARM': ('Arm Holdings PLC', '0001973239'),     # correct Arm CIK
    'ASML': ('ASML Holding NV', '0000937966'),
    'AVGO': ('Broadcom Inc', '0001730168'),
    'CDNS': ('Cadence Design Systems Inc', '0000813672'),
    'COHR': ('Coherent Corp', '0001562287'),
    'CRUS': ('Cirrus Logic Inc', '0000772406'),
    'ENTG': ('Entegris Inc', '0001101302'),
    'GFS': ('GLOBALFOUNDRIES Inc', '0001709048'),
    'INTC': ('Intel Corp', '0000050863'),
    'KLAC': ('KLA Corp', '0000319201'),
    'LRCX': ('Lam Research Corp', '0000707549'),
    'LSCC': ('Lattice Semiconductor Corp', '0000855658'),
    'MCHP': ('Microchip Technology Inc', '0000827054'),
    'MPWR': ('Monolithic Power Systems Inc', '0001136640'),
    'MRVL': ('Marvell Technology Inc', '0001058057'),
    'MTSI': ('MACOM Technology Solutions Holdings Inc', '0001493594'),
    'MU': ('Micron Technology Inc', '0000723125'),
    'NVDA': ('NVIDIA Corp', '0001045810'),
    'NXPI': ('NXP Semiconductors NV', '0001413447'),
    'ON': ('ON Semiconductor Corp', '0001097864'),
    # ONTO uses Nanometrics' legacy submissions CIK; alias fallback supports 1784048
    'ONTO': ('Onto Innovation Inc', '0000707388'),
    'QCOM': ('QUALCOMM Inc', '0000804328'),
    'QRVO': ('Qorvo Inc', '0001604778'),
    'SWKS': ('Skyworks Solutions Inc', '0000004127'),
    'TER': ('Teradyne Inc', '0000097210'),
    'TSM': ('Taiwan Semiconductor Manufacturing Co Ltd', '0001046179'),
    'TXN': ('Texas Instruments Inc', '0000097476'),
    'SNPS': ('Synopsys Inc', '0000883241'),
}
TARGET_CIKS = {cik.zfill(10) for _, cik in COMPANIES.values()}

# CIK alias mapping (submissions JSON fallback)
CIK_ALIASES = {
    "0001784048": "0000707388",  # ONTO → Nanometrics
}

# =========================
# Terms (precise phrases only)
# =========================
BIS_TERMS = [
    "bureau of industry and security",
    "export control", "export controls",
    "export restriction", "export restrictions",
    "export administration regulations",
    "commerce department",
    "trade restriction", "trade restrictions",
    "entity list",
    "denied persons list",
    "unverified list",
    "license required", "license is required", "license is now required", "license was required",
    "licensing requirement", "licensing requirements",
    "export license",
    "commerce control list",
    "military end use", "military end user",
    "unacceptable risk",
    "export control classification",
]
CHINA_TERMS = ["china", "chinese", "prc", "people's republic of china"]

PROXIMITY_WINDOW = 150  # words (heuristic via char distance / ~6)

# =========================
# Pre-filters (before Claude)
# =========================
RISK_MARKERS = (
    " risk factor", "risk factors", " may ", " could ", " might ", " no assurance",
    " would ", " if ", " from time to time", " could adversely", " could negatively"
)
TARIFF_TERMS = (" tariff", " tariffs", "reciprocal tariffs", "countermeasures")
PREV_REPORTED_TERMS = ("previously reported", "previously disclosed", "as previously disclosed", "as previously reported", "as disclosed", "as reported")
ENFORCEMENT_TERMS = ("subpoena", "subpoenas", "investigation", "preliminary findings", "settlement", "resolution", "consent decree")

BOILERPLATE_SECTION_TITLES = (
    "developments in export control regulations",
    "risk factors",
    "legal and regulatory",
)

MONTHS = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_PAT = re.compile(
    rf"(?:(?:on\s+)?{MONTHS}\s+\d{{1,2}},\s*\d{{4}}|{MONTHS}\s+\d{{4}})",
    re.IGNORECASE
)

# =========================
# Email functionality
# =========================
def send_email_alert(company: str, ticker: str, form: str, filing_date: str, filing_url: str, excerpt: str):
    """Send email alert for a NEW BIS/China disclosure"""
    if not SEND_EMAILS:
        print("  [Email] Skipping (no credentials configured)")
        return
    
    try:
        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"BIS/China Alert: {company} ({ticker}) - {form}"
        msg["From"] = GMAIL_USER
        msg["To"] = ", ".join(EMAIL_RECIPIENTS)
        
        # Plain text version
        text = f"""BIS/China Export Restriction Disclosure Detected
        
Company: {company} ({ticker})
Filing Type: {form}
Filing Date: {filing_date}
SEC Filing: {filing_url}

Excerpt:
{excerpt[:800]}

---
This is an automated alert from the BIS/China Monitor
"""
        
        # Prepare excerpt for HTML (can't use backslash in f-string)
        excerpt_html = excerpt[:800].replace('\n', '<br>')
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        # HTML version
        html = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
        .header {{ background-color: #d32f2f; color: white; padding: 15px; }}
        .content {{ padding: 20px; }}
        .detail {{ margin: 10px 0; }}
        .label {{ font-weight: bold; }}
        .excerpt {{ background-color: #f5f5f5; padding: 15px; border-left: 4px solid #d32f2f; margin: 15px 0; }}
        .footer {{ color: #666; font-size: 12px; margin-top: 20px; padding-top: 20px; border-top: 1px solid #ddd; }}
    </style>
</head>
<body>
    <div class="header">
        <h2>🚨 BIS/China Export Restriction Disclosure Detected</h2>
    </div>
    <div class="content">
        <div class="detail"><span class="label">Company:</span> {company} ({ticker})</div>
        <div class="detail"><span class="label">Filing Type:</span> {form}</div>
        <div class="detail"><span class="label">Filing Date:</span> {filing_date}</div>
        <div class="detail"><span class="label">SEC Filing:</span> <a href="{filing_url}">{filing_url}</a></div>
        
        <div class="excerpt">
            <strong>Excerpt:</strong><br>
            {excerpt_html}
        </div>
        
        <div class="footer">
            This is an automated alert from the BIS/China Monitor<br>
            Generated at {timestamp}
        </div>
    </div>
</body>
</html>"""
        
        # Attach both versions
        part1 = MIMEText(text, "plain")
        part2 = MIMEText(html, "html")
        msg.attach(part1)
        msg.attach(part2)
        
        # Send email via Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        
        print(f"  [Email] ✓ Sent alert to {len(EMAIL_RECIPIENTS)} recipient(s)")
        
    except Exception as e:
        print(f"  [Email] ✗ Failed to send: {e}")

# =========================
# Concrete-change detection
# =========================
CONCRETE_PHRASES = (
    "effective immediately",
    "added to entity list",
    "removed from entity list",
    "license is now required",
    "license was required",
    "license required for",
    "license required to",
    "rescinded",
    "revoked",
    "denied",
    "halted shipments",
    "suspended",
    "ceased",
    "restoring access",
)
DATED_ACTION_PATTERNS = [
    re.compile(rf"\bon\s+{MONTHS}\s+\d{{1,2}},\s*\d{{4}}[, ]+\s*(bis|bureau of industry and security|department of commerce)\s+(informed|notified|issued|published|added|required)", re.I),
    re.compile(rf"\b(effective)\s+{MONTHS}\s+\d{{1,2}},\s*\d{{4}}\b", re.I),
]

def has_concrete_change(text: str) -> bool:
    t = " " + text.lower() + " "
    if any(p in t for p in CONCRETE_PHRASES):
        return True
    if any(p.search(text) for p in DATED_ACTION_PATTERNS):
        if (" bureau of industry and security " in t) or (" bis " in t) or (" department of commerce " in t) or (" entity list " in t):
            return True
    return False

def looks_like_risk_boilerplate(text: str) -> bool:
    t = " " + text.lower() + " "
    return any(m in t for m in RISK_MARKERS) and not has_concrete_change(text)

def contains_tariff_without_concrete(text: str) -> bool:
    t = " " + text.lower() + " "
    return any(term in t for term in TARIFF_TERMS) and not has_concrete_change(text)

def previously_reported_without_update(text: str) -> bool:
    t = " " + text.lower() + " "
    return any(term in t for term in PREV_REPORTED_TERMS) and not has_concrete_change(text)

def enforcement_without_concrete_change(text: str) -> bool:
    t = " " + text.lower() + " "
    return any(term in t for term in ENFORCEMENT_TERMS) and not has_concrete_change(text)

def looks_like_section_boilerplate(text: str) -> bool:
    t = " " + text.lower() + " "
    return any(h in t for h in BOILERPLATE_SECTION_TITLES) and not has_concrete_change(text)

# =========================
# Date helpers (for stale-dated guard)
# =========================
def extract_dates_from_text(text: str) -> list[datetime]:
    dates = []
    for m in DATE_PAT.finditer(text):
        s = m.group(0).strip().lower().removeprefix("on ").title()
        for fmt in ("%B %d, %Y", "%B %Y"):
            try:
                d = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                dates.append(d)
                break
            except Exception:
                continue
    return dates

def is_stale_dated_event(excerpt: str, filing_date_iso: str, freshness_days: int = FRESHNESS_DAYS_FOR_EVENT) -> bool:
    """True if most recent explicit date is older than freshness_days before filing, and no clear update language."""
    try:
        fdt = datetime.fromisoformat(filing_date_iso.replace("Z","+00:00"))
        if fdt.tzinfo is None:
            fdt = fdt.replace(tzinfo=timezone.utc)
    except Exception:
        return False

    dates = extract_dates_from_text(excerpt)
    if not dates:
        return False

    most_recent = max(dates)
    age_days = (fdt - most_recent).days

    t = " " + excerpt.lower() + " "
    UPDATE_MARKERS = (
        "updated", "subsequently", "later", "amended", "modified",
        "rescinded", "revoked", "extended", "effective immediately",
    )
    has_update = any(u in t for u in UPDATE_MARKERS)

    if DEBUG_GUARDS:
        try:
            found = sorted({d.strftime('%Y-%m-%d') for d in dates})
        except Exception:
            found = []
        print(f"  [dates] found={found} most_recent={most_recent.strftime('%Y-%m-%d')} age_days={age_days} has_update={has_update}")

    return (age_days > freshness_days) and (not has_update)

# =========================
# Event dedupe store (per CIK)
# =========================
def load_events_seen() -> dict:
    if EVENTS_FILE.exists():
        try:
            with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_events_seen(events: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2)

def normalize_event_signature(excerpt: str) -> str:
    """Signature from phrase family + latest explicit date."""
    t = excerpt.lower()
    families = [
        "license is now required",
        "license was required",        # catches many 10-Q rehashes
        "license required for",
        "license required to",
        "added to entity list",
        "removed from entity list",
        "bis informed",
        "bis issued",
        "bis published",
        "export control classification",
        "restoring access",
        "effective immediately",
    ]
    fam = next((f for f in families if f in t), "generic-bis-china")
    dates = extract_dates_from_text(excerpt)
    most_recent_str = max(dates).strftime("%Y-%m-%d") if dates else "no-date"
    raw = f"{fam}|{most_recent_str}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

# =========================
# Claude classifier
# =========================
PROMPT = """You are classifying SEC filing excerpts about U.S. BIS/China export controls as NEW or BOILERPLATE.

Context:
- Filing date: {filing_date}

NEW (alert) when the excerpt reports a specific, dated, recent change directly affecting the filer, e.g.:
- "On May 29, 2025, BIS informed <Company> …"
- "effective immediately" / "rescinded" / "added to Entity List" / "license is now required"
- concrete operational response (halted shipments, restoring access) tied to the dated action.

BOILERPLATE (reject) when the excerpt is:
- Generic risk-factor language with "may / could / might / no assurance" and compliance generalities,
- Summaries of older rules/events or background that do not report a new company-specific change,
- Mentions of proposed rules or government intention to act (not yet effective),
- Enforcement/compliance items (e.g., subpoenas) that do not describe a new export-rule change.

Return ONLY one word: NEW or BOILERPLATE.

EXCERPT:
{excerpt}
"""

def classify_with_claude(excerpt: str, filing_date_iso: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "BOILERPLATE"
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 20,
                "messages": [{
                    "role": "user",
                    "content": PROMPT.format(
                        filing_date=filing_date_iso or "unknown",
                        excerpt=excerpt[:2000]
                    )
                }],
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        text = (data.get("content", [{}])[0].get("text", "") or "").strip().upper()
        if "NEW" in text and "BOILERPLATE" not in text:
            return "NEW"
        if "BOILERPLATE" in text:
            return "BOILERPLATE"
        return "BOILERPLATE"
    except Exception as e:
        print(f"  [Claude error] {e}")
        return "BOILERPLATE"

# =========================
# State helpers
# =========================
def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen(seen: set) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f)

# =========================
# HTTP helper with backoff
# =========================
def _http_get(url: str, session: requests.Session, tries: int = 4, pause: float = 0.15) -> requests.Response | None:
    attempt = 0
    while attempt < tries:
        try:
            r = session.get(url, timeout=30)
            if r.status_code in (403, 429, 503):
                delay = min(8.0, 1.5 ** attempt) + random.uniform(0, 0.5)
                print(f"[Backoff] {r.status_code} {url} — sleeping {delay:.1f}s")
                time.sleep(delay)
                attempt += 1
                continue
            r.raise_for_status()
            time.sleep(pause)
            return r
        except Exception as e:
            delay = min(8.0, 1.5 ** attempt) + random.uniform(0, 0.5)
            print(f"[HTTP error] {e} — retrying {url} in {delay:.1f}s")
            time.sleep(delay)
            attempt += 1
    print(f"[HTTP error] giving up on {url}")
    return None

# =========================
# Global current-events Atom (polling)
# =========================
def fetch_global_feed(start: int = 0) -> bytes | None:
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
        f"&CIK=&type=&company=&dateb=&owner=include&start={start}&count=100&output=atom"
    )
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    r = _http_get(url, s)
    return r.content if r else None

def parse_atom(xml_bytes: bytes) -> list[dict]:
    if not xml_bytes:
        return []
    soup = BeautifulSoup(xml_bytes, "xml")  # lenient
    filings = []
    for entry in soup.find_all("entry"):
        title = entry.find("title")
        link = entry.find("link")
        updated = entry.find("updated")
        if not title or not link:
            continue
        ttxt = (title.get_text() or "").strip()
        href = link.get("href", "").strip()
        utxt = (updated.get_text().strip() if updated else "")
        m = re.search(r"^([\w\-/]+)\s+-\s+(.+?)\s+\((\d+)\)", ttxt)
        if not m:
            continue
        form = m.group(1).upper()
        company = m.group(2)
        cik = m.group(3).zfill(10)
        filings.append({"form": form, "company": company, "cik": cik, "url": href, "updated": utxt})
    return filings

def parse_iso8601(ts: str) -> datetime | None:
    try:
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except Exception:
        return None

# =========================
# Submissions JSON (backfill)
# =========================
def fetch_company_submissions_json(cik: str) -> dict | None:
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    r = _http_get(url, s)
    if not r and cik in CIK_ALIASES:
        alias = CIK_ALIASES[cik]
        print(f"  [Alias] retrying submissions JSON using alias CIK {alias} for {cik}")
        url = f"https://data.sec.gov/submissions/CIK{alias}.json"
        r = _http_get(url, s)
    if not r:
        return None
    try:
        return r.json()
    except Exception:
        try:
            return json.loads(r.text)
        except Exception:
            return None

def iter_recent_filings_from_json(data: dict):
    if not data or "filings" not in data or "recent" not in data["filings"]:
        return
    recent = data["filings"]["recent"]
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primaries = recent.get("primaryDocument", [])
    companies = data.get("name", "")
    cik = str(data.get("cik", "")).zfill(10) or str(data.get("cik_str", "")).zfill(10)
    n = min(len(forms), len(dates), len(accessions), len(primaries))
    for i in range(n):
        yield {
            "company": companies,
            "cik": cik,
            "form": (forms[i] or "").upper(),
            "filingDate": dates[i],
            "accession": accessions[i],
            "primary": primaries[i],
        }

def build_doc_urls(cik10: str, accession: str, primary: str):
    cik_nolead = str(int(cik10))  # strip leading zeros
    acc_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_nolead}/{acc_nodash}/"
    index_url = base + f"{acc_nodash}-index.htm"
    primary_url = base + primary
    return index_url, primary_url

# =========================
# Filing fetching (resolve index -> primary)
# =========================
def _pick_primary_doc_from_index(index_html: str, index_url: str) -> str | None:
    soup = BeautifulSoup(index_html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]; h = href.lower()
        if h.endswith(("-index.htm", "-index.html")):
            continue
        if h.endswith((".htm", ".html", ".txt")):
            return urljoin(index_url, href)
    base = index_url.rsplit("/", 1)[0] + "/"
    for a in soup.find_all("a", href=True):
        full = urljoin(index_url, a["href"])
        fl = full.lower()
        if not full.startswith(base):
            continue
        if fl.endswith((".htm", ".html", ".txt")) and "-index" not in fl:
            return full
    return None

def fetch_filing_html_from_urls(index_url: str, primary_url: str) -> str:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    r = _http_get(primary_url, s)
    if r:
        return r.text
    r_index = _http_get(index_url, s)
    if not r_index:
        return ""
    html = r_index.text
    doc_url = _pick_primary_doc_from_index(html, index_url)
    if not doc_url:
        return html
    r2 = _http_get(doc_url, s)
    return r2.text if r2 else ""

# =========================
# Text & proximity
# =========================
def html_to_text(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "html.parser")
        for n in soup(["script", "style"]): n.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text if text else html
    except Exception:
        return html

def proximity_search(text: str, terms1: list[str], terms2: list[str], window: int = PROXIMITY_WINDOW):
    t = text.lower()
    def positions(terms):
        out=[]
        for term in terms:
            q=term.lower(); i=0
            while True:
                j=t.find(q,i)
                if j==-1: break
                out.append((j,term)); i=j+1
        return out
    bisp, chnp = positions(terms1), positions(terms2)
    matches=[]
    for bp, bt in bisp:
        for cp, ct in chnp:
            if abs(bp-cp)/6 <= window:
                start=max(0,min(bp,cp)-300)
                end=min(len(text),max(bp,cp)+300)
                matches.append((bt, ct, text[start:end]))
    return matches

def get_ticker(cik: str) -> str:
    for ticker, (_, tcik) in COMPANIES.items():
        if tcik.zfill(10) == cik:
            return ticker
    return "UNKNOWN"

# =========================
# Labeling with guards + dedupe
# =========================
def _label_with_guards(excerpt: str, filing_date_iso: str, cik: str, events_seen: dict) -> str:
    # 0) Hard stop: stale-dated rehash (e.g., a May 23 letter cited in an Oct 29 10-Q)
    if is_stale_dated_event(excerpt, filing_date_iso, FRESHNESS_DAYS_FOR_EVENT):
        if DEBUG_GUARDS: print("  Guard(stale-dated FIRST) → BOILERPLATE")
        return "BOILERPLATE"

    # 1) Generic section boilerplate
    if looks_like_section_boilerplate(excerpt):
        if DEBUG_GUARDS: print("  Guard(section-boilerplate) → BOILERPLATE")
        return "BOILERPLATE"

    # 2) Tariff talk without concrete BIS/China change
    if contains_tariff_without_concrete(excerpt):
        if DEBUG_GUARDS: print("  Guard(tariff) → BOILERPLATE")
        return "BOILERPLATE"

    # 3) Risk-language boilerplate (allow Claude only if very recent)
    if looks_like_risk_boilerplate(excerpt):
        if DEBUG_GUARDS: print("  Guard(risk) → (let Claude only if recent)")

    # 4) Previously reported/disclosed without an update
    if previously_reported_without_update(excerpt):
        if DEBUG_GUARDS: print("  Guard(previously-reported) → BOILERPLATE")
        return "BOILERPLATE"

    # 5) Enforcement/investigation mention without an export-rule change
    if enforcement_without_concrete_change(excerpt):
        if DEBUG_GUARDS: print("  Guard(enforcement) → BOILERPLATE")
        return "BOILERPLATE"

    # 6) Cross-filing event de-dupe
    sig = normalize_event_signature(excerpt)
    recent = events_seen.get(cik, {})
    if sig in recent:
        last_dt = datetime.fromisoformat(recent[sig])
        if (datetime.now(timezone.utc) - last_dt).days <= EVENT_DEDUPE_WINDOW_DAYS:
            if DEBUG_GUARDS: print("  Guard(event-dedupe) → BOILERPLATE (OLD)")
            return "BOILERPLATE"

    # 7) Otherwise ask Claude
    return classify_with_claude(excerpt, filing_date_iso)

# =========================
# Processing
# =========================
def process_filing_from_urls(meta: dict, index_url: str, primary_url: str, seen: set, events_seen: dict):
    filing_date_iso = meta.get("filingDate","")  # YYYY-MM-DD
    if filing_date_iso:
        filing_date_iso += "T00:00:00+00:00"

    ticker = get_ticker(meta["cik"])
    print(f"\n→ {meta['form']} — {meta.get('company','')} ({ticker}) — filed {meta.get('filingDate','')}")
    print(f"  URL: {index_url}")
    html = fetch_filing_html_from_urls(index_url, primary_url)
    if not html:
        print("  (fetch failed)"); seen.add(index_url); return
    text = html_to_text(html)
    matches = proximity_search(text, BIS_TERMS, CHINA_TERMS, PROXIMITY_WINDOW)
    if not matches:
        print("  No BIS/China proximity matches found."); seen.add(index_url); return
    excerpt = matches[0][2]

    label = _label_with_guards(excerpt, filing_date_iso, meta["cik"], events_seen)
    print(f"  Claude label: {label}")
    print("  Excerpt:", excerpt[:600].replace("\n"," "))

    # Send email alert if NEW
    if label == "NEW":
        send_email_alert(
            company=meta.get('company', ''),
            ticker=ticker,
            form=meta['form'],
            filing_date=meta.get('filingDate', ''),
            filing_url=index_url,
            excerpt=excerpt
        )
        # Record event signature for dedupe
        sig = normalize_event_signature(excerpt)
        events_seen.setdefault(meta["cik"], {})[sig] = datetime.now(timezone.utc).isoformat()

    seen.add(index_url)

# =========================
# Backfill (per-company via submissions JSON)
# =========================
def backfill_last_days(days: int, seen: set, events_seen: dict):
    print(f"\n[Backfill] Scanning last {days} day(s) per company via submissions JSON…")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    processed_total = 0
    for ticker, (_, cik) in COMPANIES.items():
        cik10 = cik.zfill(10)
        data = fetch_company_submissions_json(cik10)
        company_considered = 0
        company_processed = 0
        if data:
            for rec in iter_recent_filings_from_json(data):
                if rec["form"] not in TARGET_FORMS:
                    continue
                try:
                    fdate = datetime.strptime(rec["filingDate"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if fdate < cutoff:
                    continue
                index_url, primary_url = build_doc_urls(rec["cik"], rec["accession"], rec["primary"])
                company_considered += 1
                if index_url in seen:
                    continue
                process_filing_from_urls(rec, index_url, primary_url, seen, events_seen)
                company_processed += 1
                processed_total += 1
                time.sleep(0.25)
        print(f"  [Backfill] {ticker}: {company_considered} candidate(s) in window, {company_processed} processed")
    if processed_total == 0:
        print("  [Backfill] No new target filings to process in window.")
    else:
        print(f"  [Backfill] Processed {processed_total} target filing(s) in window.")

# =========================
# Polling (global feed)
# =========================
def run_monitor():
    print("============================================================", flush=True)
    print("BIS/China Monitor with Email Alerts", flush=True)
    print(f"Anthropic key present: {'YES' if ANTHROPIC_API_KEY else 'NO (default BOILERPLATE)'}", flush=True)
    print(f"Email alerts: {'ENABLED' if SEND_EMAILS else 'DISABLED (configure GMAIL_USER and GMAIL_APP_PASSWORD)'}", flush=True)
    if SEND_EMAILS:
        print(f"Email recipients: {', '.join(EMAIL_RECIPIENTS)}", flush=True)
    print(f"Tracking {len(TARGET_CIKS)} CIKs; forms: {', '.join(sorted(TARGET_FORMS))}", flush=True)
    print(f"Startup backfill: {'YES' if BACKFILL_ON_START else 'NO'} (last {BACKFILL_DAYS} day(s))", flush=True)
    print(f"Run mode: {'ONCE (for GitHub Actions)' if RUN_ONCE else f'CONTINUOUS (poll every {CHECK_INTERVAL//60} min)'}", flush=True)
    print("============================================================", flush=True)

    seen = load_seen()
    events_seen = load_events_seen()

    if BACKFILL_ON_START:
        backfill_last_days(BACKFILL_DAYS, seen, events_seen)
        save_seen(seen)
        save_events_seen(events_seen)

    try:
        while True:
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking SEC feed…", flush=True)
            xml = fetch_global_feed(start=0)
            filings = parse_atom(xml) if xml else []
            if not filings:
                print("  No entries (or feed temporarily unavailable)", flush=True)
                if RUN_ONCE:
                    break
                time.sleep(CHECK_INTERVAL); continue

            new_filings = [
                f for f in filings
                if (f["cik"] in TARGET_CIKS) and (f["form"] in TARGET_FORMS)
                and (f["url"] not in seen)
            ]

            if not new_filings:
                print("  No new filings for target companies.", flush=True)
            else:
                print(f"  Found {len(new_filings)} new filing(s) for target companies.", flush=True)
                for f in new_filings:
                    rec = {
                        "company": f["company"], "cik": f["cik"], "form": f["form"],
                        "filingDate": parse_iso8601(f["updated"]).date().isoformat() if f.get("updated") else ""
                    }
                    index_url = f["url"]
                    primary_url = f["url"]  # resolver handles this path
                    process_filing_from_urls(rec, index_url, primary_url, seen, events_seen)
                    time.sleep(0.25)

            save_seen(seen)
            save_events_seen(events_seen)
            
            if RUN_ONCE:
                print("\n[RUN_ONCE mode] Check complete. Exiting.", flush=True)
                break
            
            print(f"\nSleeping {CHECK_INTERVAL//60} minutes…", flush=True)
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\nStopped by user. Saving state…")
        save_seen(seen)
        save_events_seen(events_seen)
        print("State saved. Bye.")

if __name__ == "__main__":
    run_monitor()
