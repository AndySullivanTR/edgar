#!/usr/bin/env python3
"""
BIS/China Monitor — Blind TEST Runner
- Runs a fixed list of filings without telling the script what’s "positive" or "negative".
- No polling, no email; terminal output (and optional CSV) only.
"""

import os, time, httpx, requests, csv
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path

# ========= Config =========
USER_AGENT = "Andy Sullivan andy.sullivan@thomsonreuters.com"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
PROXIMITY_WINDOW = 150
WRITE_CSV = False
CSV_PATH = Path(os.getenv("OUTPUT_DIR", ".")) / "bis_china_test_results.csv"

# Combined list (no labels given to the code)
FILINGS = [
    # (previous four you tested)
    "https://www.sec.gov/ix?doc=/Archives/edgar/data/0000813672/000081367225000093/cdns-20250702.htm",
    "https://www.sec.gov/ix?doc=/Archives/edgar/data/0000813672/000081367225000079/cdns-20250523.htm",
    "https://www.sec.gov/ix?doc=/Archives/edgar/data/0000883241/000119312525155294/d80081d8k.htm",
    "https://www.sec.gov/ix?doc=/Archives/edgar/data/0000883241/000119312525130725/d14316d8k.htm",
    # (four known boilerplate examples — direct SEC URLs)
    "https://www.sec.gov/Archives/edgar/data/833640/000083364025000043/0000833640-25-000043.txt",
    "https://www.sec.gov/Archives/edgar/data/895419/000119312525224251/0001193125-25-224251.txt",
    "https://www.sec.gov/Archives/edgar/data/4127/000000412725000072/0000004127-25-000072.txt",
    "https://www.sec.gov/Archives/edgar/data/827054/000082705425000061/0000827054-25-000061.txt",
]

BIS_TERMS = [
    "bureau of industry and security", "export control", "export controls",
    "export restriction", "export restrictions", "export administration regulations",
    "commerce department", "trade restriction", "trade restrictions", "entity list",
    "denied persons list", "unverified list", "license required", "license is required",
    "license is now required", "licensing requirement", "licensing requirements",
    "export license", "commerce control list", "military end use", "military end user",
    "unacceptable risk", "export control classification"
]
CHINA_TERMS = ["china", "chinese", "prc", "people's republic of china"]

PROMPT = """Classify the excerpt as either NEW or BOILERPLATE regarding U.S. BIS/China export restrictions.

BOILERPLATE (reject):
- Generic risk factor language with conditionals ("may", "could", "might") and no dates.
- Vague statements about potential BIS rules, compliance frameworks, or hypothetical licensing risks.
- Standard disclosures repeated each quarter with no concrete change.

NEW (alert):
- A specific, dated, recent action/change impacting the company (e.g., "On Oct 17, 2025, BIS issued...", "effective immediately", "new license now required", "added to Entity List", "revoked/denied license", "shipping halted", "material revenue impact in China").
- Mentions company response or operational changes (suspending shipments, seeking licenses, halting fulfillment, revising guidance).
- Concrete references to affected products/customers, or quantified impacts.

Return ONLY one word: NEW or BOILERPLATE.

EXCERPT:
{excerpt}
"""

# ========= Helpers =========
def fetch_html(url: str) -> str:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    u = url
    if "/ix?doc=" in u:
        u = "https://www.sec.gov" + u.split("/ix?doc=")[1]
    try:
        r = s.get(u, timeout=30)
        # brief retry on occasional 403/429 from sec.gov
        if r.status_code in (403, 429):
            time.sleep(1.0)
            r = s.get(u, timeout=30)
        r.raise_for_status()
        time.sleep(0.15)
        return r.text
    except Exception as e:
        print(f"  !! Fetch error for {u}: {e}")
        return ""

def html_to_text(html: str) -> str:
    # Handles both HTML (.htm) and plain text (.txt) filings
    try:
        soup = BeautifulSoup(html, "html.parser")
        for n in soup(["script","style"]):
            n.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text if text else html  # fall back to raw text if parser yields empty
    except Exception:
        return html

def proximity_search(text: str, terms1, terms2, window=150):
    t = text.lower()
    def positions(terms):
        out=[]
        for term in terms:
            start=0; q=term.lower()
            while True:
                pos=t.find(q,start)
                if pos==-1: break
                out.append((pos,term))
                start=pos+1
        return out
    bisp, chnp = positions(terms1), positions(terms2)
    matches=[]
    for bp, bt in bisp:
        for cp, ct in chnp:
            if abs(bp-cp)/6 <= window:  # ~avg word length heuristic
                start=max(0, min(bp,cp)-300)
                end=min(len(text), max(bp,cp)+300)
                matches.append((bt, ct, text[start:end]))
    return matches

def score_excerpt(excerpt: str) -> int:
    e = excerpt.lower()
    pos = ['on ','effective ','effective immediately','as of ',
           'license now required','entity list','denied','revoked',
           'halted','suspended','ceased','material impact','new rule','interim final rule']
    neg = [' may ',' could ',' might ',' if ',' would ','no assurance','forward-looking']
    return sum(1 for p in pos if p in e) - sum(1 for n in neg if n in e)

def classify_with_claude(excerpt: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "BOILERPLATE"  # conservative default
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model":"claude-sonnet-4-20250514",
                "max_tokens":20,
                "messages":[{"role":"user","content":PROMPT.format(excerpt=excerpt[:2000])}]
            },
            timeout=30
        )
        data = r.json()
        text = (data.get("content",[{}])[0].get("text","") or "").strip().upper()
        if "NEW" in text and "BOILERPLATE" not in text: return "NEW"
        if "BOILERPLATE" in text: return "BOILERPLATE"
        return "BOILERPLATE"
    except Exception as e:
        print("  Claude error:", e)
        return "BOILERPLATE"

def maybe_write_csv(rows):
    if not WRITE_CSV or not rows: return
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    new = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp","url","label","excerpt"])
        for r in rows:
            w.writerow(r)

# ========= Main =========
def main():
    print("\nBIS/China Blind TEST — no expected labels given to the classifier")
    print(f"Anthropic key: {'YES' if ANTHROPIC_API_KEY else 'NO (default BOILERPLATE)'}")
    print("---------------------------------------------------------------")

    rows = []
    for url in FILINGS:
        print("\n• URL:", url)
        html = fetch_html(url)
        if not html:
            print("  (fetch failed)")
            continue
        text = html_to_text(html)
        matches = proximity_search(text, BIS_TERMS, CHINA_TERMS, PROXIMITY_WINDOW)
        if not matches:
            print("  No BIS/China proximity matches found.")
            continue

        ranked = sorted(((score_excerpt(m[2]), m) for m in matches), key=lambda x: x[0], reverse=True)
        top_excerpt = ranked[0][1][2]
        label = classify_with_claude(top_excerpt)

        print("  Claude label:", label)
        print("  Top excerpt:", top_excerpt[:600].replace("\n"," "))
        rows.append([datetime.now().isoformat(), url, label, top_excerpt[:500]])

    maybe_write_csv(rows)
    if WRITE_CSV:
        print(f"\nCSV written to: {CSV_PATH.resolve()}")

    print("\nDone.\n")

if __name__ == "__main__":
    main()
