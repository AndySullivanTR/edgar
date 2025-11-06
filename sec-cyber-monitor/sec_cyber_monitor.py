#!/usr/bin/env python3
"""
SEC EDGAR Cybersecurity Incident Monitor
- Polls the SEC "current filings" Atom feed every 15 minutes
- Classifies filings for NEW disclosures of cyber incidents by foreign/state-backed actors
- Emails alerts on NEW
- Persists state to avoid re-processing the same filings

Run: python sec_cyber_monitor.py
Stop: Ctrl + C
"""

import os
import re
import json
import time
import httpx
import smtplib
import requests
from email.message import EmailMessage
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

# ========================
# Simple config (edit these)
# ========================


# Email configuration (using Gmail)
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
EMAIL_RECIPIENTS = os.getenv("EMAIL_RECIPIENTS", "andy.sullivan@thomsonreuters.com,chris.sanders@thomsonreuters.com").split(",")
SEND_EMAILS = bool(GMAIL_USER and GMAIL_APP_PASSWORD)

# Feed & polling
SEC_ATOM_FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&count=100&output=atom"
POLL_SECONDS = 15 * 60   # 15 minutes
RUN_ONCE = False         # set to True to test one cycle and exit

# User-Agent for SEC requests
USER_AGENT = "Andy Sullivan andy.sullivan@thomsonreuters.com"

# LLM (optional): set ANTHROPIC_API_KEY in your environment to enable.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
ANTHROPIC_URL = f"{ANTHROPIC_BASE_URL}/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-5"
MAX_EXCERPT_CHARS = 2000

# State file
STATE_FILE = "seen_filings.json"

# Forms to consider (tune as needed)
WATCH_FORMS = {"8-K", "10-Q", "10-K"}

# ========================
# Lexicons & Regex (same logic as your passing test)
# ========================

CYBER_TERMS = [
    "cybersecurity incident", "cybersecurity", "cyber attack", "cyberattack",
    "threat actor", "threat actors", "unauthorized access", "compromise",
    "breach", "ransomware", "malware", "exfiltrat", "intrusion", "lateral movement",
    "apt", "advanced persistent threat", "credential theft", "persistence"
]

NATION_STATE_TERMS = [
    "nation-state", "state-sponsored", "state-affiliated", "state-backed", "government-backed",
    "foreign intelligence", "foreign adversary", "apt", "advanced persistent threat",
    "prc", "chinese", "china", "russia", "russian", "gru", "fsb", "svr", "mss",
    "iran", "irgc", "dprk", "north korean", "vietnam", "apt32", "oceanlotus",
    "lazarus", "sandworm", "cozy bear", "apt29", "apt28", "unc", "volt typhoon",
    "believed to be", "linked to", "attributed to", "suspected"
]

SPLIT_SENTENCES = re.compile(r'(?<=[\.\?\!])\s+(?=[A-Z(“"])')
SPLIT_PARAS = re.compile(r'\n{2,}|\r{2,}|\s{2,}')

DATE_RE = re.compile(
    r'\b(on\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2})'
    r'|\b(20\d{2}-\d{2}-\d{2})'
    r'|\b(in\s+(?:early|late)?\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d{2})',
    re.I
)
MODALS_RE = re.compile(r'\b(may|could|might|would)\b', re.I)

RISK_RE = re.compile(r'item\s+1a\.\s*risk factors|forward-looking statements', re.I)
ITEM105_RE = re.compile(r'item\s+1\.05', re.I)
ITEM801_RE = re.compile(r'item\s+8\.01', re.I)
ITEM5_RE = re.compile(r'item\s+5\.\s*other information', re.I)
CYBER_DISCLOSURE_RE = re.compile(r'cybersecurity incident disclosure', re.I)

RESPONSE_SIGNALS = [
    "learned", "discovered", "detected", "became aware",
    "gained unauthorized access", "gained access", "exfiltrat", "persist",
    "initiated incident response", "activated incident response",
    "engaged third-party", "engaged cybersecurity", "forensic",
    "law enforcement", "contain", "contained", "remediat", "investigation"
]

def modal_density(text: str) -> float:
    tokens = re.findall(r'\w+', text)
    if not tokens:
        return 0.0
    return len(MODALS_RE.findall(text)) / max(1, len(tokens))

def is_time_anchored(text: str) -> bool:
    return bool(DATE_RE.search(text))

def section_score(context: str) -> int:
    s = 0
    text = context
    if ITEM105_RE.search(text) or ITEM801_RE.search(text):
        s += 2
    if ITEM5_RE.search(text):
        s += 1
    if CYBER_DISCLOSURE_RE.search(text):
        s += 1
    if RISK_RE.search(text):
        s -= 2
    return s

def looks_like_boilerplate(text: str) -> bool:
    return modal_density(text) > 0.02 or bool(RISK_RE.search(text))

def verb_signal_score(text: str) -> int:
    t = text.lower()
    return sum(1 for v in RESPONSE_SIGNALS if v in t)

def has_nation_state_token(text: str) -> bool:
    low = (text or "").lower()
    return any(term.lower() in low for term in NATION_STATE_TERMS)

def obvious_new(text: str) -> bool:
    return is_time_anchored(text) and (verb_signal_score(text) >= 2) and (section_score(text) >= 1)

def windows(text: str):
    paras = [p.strip() for p in SPLIT_PARAS.split(text) if p and p.strip()]
    for p in paras:
        sents = [s.strip() for s in SPLIT_SENTENCES.split(p) if s and s.strip()]
        for s in sents:
            yield ("sentence", s)
        yield ("paragraph", p)

def proximity_by_scope(text: str, terms1, terms2):
    hits = []
    for scope, chunk in windows(text):
        low = chunk.lower()
        t1 = [t for t in terms1 if t.lower() in low]
        t2 = [t for t in terms2 if t.lower() in low]
        if t1 and t2:
            hits.append((scope, t1, t2, chunk))
    hits.sort(key=lambda h: 0 if h[0] == "sentence" else 1)
    return hits

def excerpt_score(scope: str, excerpt: str) -> int:
    score = 0
    if scope == "sentence":
        score += 2
    if is_time_anchored(excerpt):
        score += 2
    score += verb_signal_score(excerpt)
    score += section_score(excerpt)
    if modal_density(excerpt) > 0.02:
        score -= 2
    if RISK_RE.search(excerpt):
        score -= 3
    return score

# ========================
# Fetch & parse
# ========================

def fetch_text_from_sec(url: str) -> str:
    with requests.Session() as s:
        s.headers.update({"User-Agent": USER_AGENT})
        r = s.get(url, timeout=45)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.extract()
        text = soup.get_text(separator="\n")
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text

def fetch_atom():
    """Return a list of entries: each is dict(title, link, updated, summary)."""
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(SEC_ATOM_FEED, headers=headers, timeout=45)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    # Atom namespace handling
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = []
    for e in root.findall("a:entry", ns):
        title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_el = e.find("a:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        updated = (e.findtext("a:updated", default="", namespaces=ns) or "").strip()
        summary = (e.findtext("a:summary", default="", namespaces=ns) or "").strip()
        entries.append({"title": title, "link": link, "updated": updated, "summary": summary})
    return entries

def parse_form_from_title(title: str) -> str:
    # Example Atom title often contains the form: "8-K - Company Name (CIK 000000)"
    m = re.search(r'\b(8-K|10-Q|10-K)\b', title, flags=re.I)
    return m.group(1).upper() if m else ""

# ========================
# LLM (optional)
# ========================

CLAUDE_PROMPT = """You are classifying SEC filing text.

Return exactly one word:
- NEW        -> if this is a disclosure of a specific, already-occurred cybersecurity incident attributed to or reportedly involving a foreign/state-backed actor.
- BOILERPLATE-> if this is generic risk language, hypothetical/forward-looking statements, or lacks a concrete, past-tense incident.

Think carefully but output ONLY 'NEW' or 'BOILERPLATE'.
"""

def claude_classify(excerpt: str):
    """
    Returns (label, err) where err is None on success, otherwise a short code.
    If ANTHROPIC_API_KEY is not set, we return ("BOILERPLATE", "NO_KEY").
    """
    if not ANTHROPIC_API_KEY:
        return ("BOILERPLATE", "NO_KEY")

    ex = (excerpt or "").strip()[:MAX_EXCERPT_CHARS]
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 10,
        "temperature": 0,
        "system": CLAUDE_PROMPT,
        "messages": [{"role": "user", "content": ex}],
    }
    print(f"[Claude] POST -> {ANTHROPIC_URL}")

    try:
        with httpx.Client(timeout=40.0, trust_env=False) as client:
            resp = client.post(ANTHROPIC_URL, headers=headers, json=payload)
            if resp.status_code in (404, 405):
                print(f"  ⚠️ Anthropic {resp.status_code}. Body (trunc): {resp.text[:400]}")
                return ("BOILERPLATE", str(resp.status_code))
            if resp.status_code == 401:
                print("  ⚠️ Anthropic 401 Unauthorized (check API key).")
                return ("BOILERPLATE", "401")
            resp.raise_for_status()
            data = resp.json()
            out = (data.get("content", [{}])[0].get("text") or "").strip().upper()
            return (("NEW" if out == "NEW" else "BOILERPLATE"), None)
    except httpx.HTTPError as e:
        print(f"  ⚠️ HTTP error calling Anthropic: {e}")
        return ("BOILERPLATE", "HTTP")
    except Exception as e:
        print(f"  ⚠️ Unexpected error calling Anthropic: {e}")
        return ("BOILERPLATE", "EXC")

# ========================
# Email
# ========================


def send_email(subject: str, body: str):
    """Send email alert via Gmail SMTP"""
    if not SEND_EMAILS:
        print("\n=== EMAIL ALERT (printing because Gmail not configured) ===")
        print(subject)
        print(body)
        print("=== END EMAIL ALERT ===\n")
        return

    try:
        msg = EmailMessage()
        msg["From"] = GMAIL_USER
        msg["To"] = ", ".join(EMAIL_RECIPIENTS)
        msg["Subject"] = subject
        msg.set_content(body)

        # Use Gmail SMTP with SSL
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        
        print(f"  📧 Alert emailed to {len(EMAIL_RECIPIENTS)} recipient(s)")
    except Exception as e:
        print(f"  ✗ Email failed: {e}")


# ========================
# State (seen filings)
# ========================

def load_state():
    """
    Loads state from seen_filings.json.
    Accepts legacy/corrupt shapes and migrates to:
      {"last_checked": <iso or None>, "seen": {<url>: {...}}}
    """
    default = {"last_checked": None, "seen": {}}
    if not os.path.exists(STATE_FILE):
        return default

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        # unreadable -> start fresh but keep a backup
        try:
            os.replace(STATE_FILE, STATE_FILE + ".bad")
            print(f"  ⚠️ State file unreadable. Renamed to {STATE_FILE}.bad and starting fresh.")
        except Exception:
            print("  ⚠️ State file unreadable and could not be renamed. Starting fresh.")
        return default

    # Already in the new shape
    if isinstance(data, dict) and "seen" in data:
        if not isinstance(data["seen"], dict):
            data["seen"] = {}
        data.setdefault("last_checked", None)
        return data

    # Legacy: list of URLs (or list of dicts). Migrate.
    if isinstance(data, list):
        migrated_seen = {}
        for item in data:
            if isinstance(item, str):
                migrated_seen[item] = {"title": "", "updated": "", "label": "UNKNOWN", "reason": "migrated_list", "first_seen": datetime.now(timezone.utc).isoformat()}
            elif isinstance(item, dict):
                # Try to pull a URL-ish key
                url = item.get("url") or item.get("link") or item.get("href") or item.get("filing_url")
                if isinstance(url, str) and url:
                    migrated_seen[url] = {
                        "title": item.get("title", ""),
                        "updated": item.get("updated", ""),
                        "label": item.get("label", "UNKNOWN"),
                        "reason": "migrated_obj",
                        "first_seen": item.get("first_seen", datetime.now(timezone.utc).isoformat()),
                    }
        print(f"  ℹ️ Migrated legacy state list with {len(migrated_seen)} entries.")
        return {"last_checked": None, "seen": migrated_seen}

    # Anything else -> start fresh but back it up
    try:
        os.replace(STATE_FILE, STATE_FILE + ".unknown")
        print(f"  ⚠️ Unexpected state format. Renamed to {STATE_FILE}.unknown and starting fresh.")
    except Exception:
        print("  ⚠️ Unexpected state format and could not be renamed. Starting fresh.")
    return default


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)

# ========================
# Core classify for a filing page
# ========================

def classify_filing(url: str):
    text = fetch_text_from_sec(url)
    matches = proximity_by_scope(text, CYBER_TERMS, NATION_STATE_TERMS)

    if not matches:
        return ("BOILERPLATE", None, None)

    # choose best by score
    ranked = sorted(
        ((scope, t1, t2, chunk, excerpt_score(scope, chunk)) for (scope, t1, t2, chunk) in matches),
        key=lambda x: x[4],
        reverse=True
    )
    scope, t1, t2, best_excerpt, best_score = ranked[0]

    # Heuristic-only decisions
    if looks_like_boilerplate(best_excerpt) and section_score(best_excerpt) < 1 and verb_signal_score(best_excerpt) < 2 and not is_time_anchored(best_excerpt):
        return ("BOILERPLATE", best_excerpt, "heuristics_boilerplate")

    if obvious_new(best_excerpt):
        return ("NEW", best_excerpt, "heuristics_obvious_new")

    # Try LLM (optional)
    label, err = claude_classify(best_excerpt)
    if err is not None:
        # If LLM failed but the excerpt is strong, promote to NEW
        if is_time_anchored(best_excerpt) and verb_signal_score(best_excerpt) >= 2 and has_nation_state_token(best_excerpt):
            return ("NEW", best_excerpt, f"promoted_no_llm_{err}")
        return (label, best_excerpt, f"llm_fallback_{err}")
    return (label, best_excerpt, "llm_ok")

# ========================
# One poll cycle
# ========================

def poll_once(state):
    print("\n=== Polling SEC Atom feed ===")
    entries = fetch_atom()
    if not entries:
        print("No entries found.")
        return state, 0

    last_checked = state.get("last_checked")
    seen = state.get("seen", {})

    # parse feed entries and filter new ones
    new_entries = []
    for ent in entries:
        link = ent.get("link") or ""
        title = ent.get("title") or ""
        updated = ent.get("updated") or ""
        form = parse_form_from_title(title)
        if not link or form not in WATCH_FORMS:
            continue

        # Use link as primary unique key
        if link in seen:
            continue

        # If we have a last_checked timestamp, skip entries older than that (best-effort)
        # Atom 'updated' example: 2025-10-29T22:03:00-04:00
        if last_checked:
            try:
                updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                last_dt = datetime.fromisoformat(last_checked)
                if updated_dt <= last_dt:
                    continue
            except Exception:
                # If parsing fails, still queue it (we won't skip due to timestamp)
                pass

        new_entries.append(ent)

    print(f"Found {len(new_entries)} new candidate filings (forms: {', '.join(sorted(WATCH_FORMS))}).")

    alerts_sent = 0

    for ent in new_entries:
        link = ent["link"]
        title = ent["title"]
        updated = ent["updated"]
        print(f"\nProcessing: {title}\n{link}\nUpdated: {updated}")

        try:
            label, excerpt, reason = classify_filing(link)
        except Exception as e:
            print(f"  ⚠️ Error classifying filing: {e}")
            label, excerpt, reason = ("BOILERPLATE", None, "exception")

        print(f"  -> Classification: {label} (reason: {reason})")

        # mark as seen regardless of label (so we don't reprocess)
        seen[link] = {
            "title": title,
            "updated": updated,
            "label": label,
            "reason": reason,
            "first_seen": datetime.now(timezone.utc).isoformat()
        }
        save_state({"last_checked": datetime.now(timezone.utc).isoformat(), "seen": seen})

        if label == "NEW":
            # build email
            subject = f"[SEC CYBER] NEW disclosure detected — {title}"
            body_lines = [
                f"Title: {title}",
                f"URL: {link}",
                f"Updated: {updated}",
                "",
                "Classification: NEW",
                f"Reason: {reason}",
                "",
                "Excerpt:",
                (excerpt or "(excerpt unavailable)")[:1000],
                "",
                "--",
                "SEC Cyber Monitor",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            ]
            send_email(subject, "\n".join(body_lines))
            alerts_sent += 1

        # politeness to SEC
        time.sleep(1.0)

    # update last_checked even if nothing was new
    state["last_checked"] = datetime.now(timezone.utc).isoformat()
    state["seen"] = seen
    save_state(state)
    return state, alerts_sent

# ========================
# Main loop
# ========================

def main():
    print("="*80)
    print("SEC CYBER MONITOR")
    print("="*80)
    if not ANTHROPIC_API_KEY:
        print("Note: ANTHROPIC_API_KEY not set; using heuristics only (still good).")
    else:
        print("Anthropic key detected; LLM will be used as a secondary check.")
    print(f"Polling feed: {SEC_ATOM_FEED}")
    print(f"Alerts to: {ALERT_EMAIL_TO}")
    print("="*80)

    state = load_state()
    try:
        while True:
            state, count = poll_once(state)
            print(f"\nCycle complete. Alerts sent this cycle: {count}")
            if RUN_ONCE:
                break
            print(f"Sleeping {POLL_SECONDS} seconds...")
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping. Goodbye!")

if __name__ == "__main__":
    main()
