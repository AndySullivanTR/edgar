# SEC Cybersecurity Incident Monitor

Monitors SEC EDGAR filings in real-time for disclosures of nation-state cyber attacks.

## What It Does

- Polls SEC's RSS feed every 15 minutes for new filings
- Searches all filing types (10-K, 10-Q, 8-K, etc.)
- Uses proximity search to find cyber incident terms near nation-state references
- Logs matches to CSV with excerpts and links

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the monitor:
```bash
python sec_cyber_monitor.py
```

## Output

Results are saved to: `C:\Users\8010317\projects\government-bots\SEC-cyber\cyber_incidents.csv`

CSV columns:
- timestamp: When the match was found
- company_name: Company filing the document
- cik: SEC Central Index Key
- filing_type: Type of filing (10-K, 8-K, etc.)
- filing_date: Date of filing
- filing_url: Direct link to the filing
- page_number: Estimated page number where match was found (if detectable)
- cyber_terms_found: Which cybersecurity terms triggered
- nation_state_terms_found: Which nation-state terms triggered
- excerpt: ~400 character excerpt showing context

## Search Terms

**Cyber incident terms:**
- cybersecurity incident, cyber incident, cyber attack, cyberattack
- data breach, security breach
- unauthorized access, network intrusion, security intrusion
- malicious actor, threat actor

**Nation-state terms:**
- nation-state, nation state
- state-sponsored, state sponsored
- foreign government, foreign actor, foreign threat
- china, chinese, russia, russian, iran, iranian, north korea
- advanced persistent threat, APT

**Proximity:** Terms must appear within 100 words of each other.

## How It Works

1. Fetches SEC RSS feed (updated every 10 minutes by SEC)
2. Parses filing metadata (company, type, date, URL)
3. Downloads full filing HTML
4. Extracts text content
5. Performs proximity search for term combinations
6. Logs matches to CSV
7. Tracks processed filings to avoid duplicates
8. Repeats every 15 minutes

## Files Created

- `cyber_incidents.csv` - Match results
- `seen_filings.json` - Tracking file (prevents duplicate processing)

## Notes

- Respects SEC rate limits (0.1 second delay between requests)
- Uses Reuters user agent for identification
- Processes all filing types (not limited to 10-K/10-Q)
- Will catch the initial filing and any amendments
- Press Ctrl+C to stop the monitor

## Example Match

The tool would catch disclosures like Ribbon Communications' 10-Q (September 2025):
> "In early September 2025, the Company became aware that unauthorized persons, 
> reportedly associated with a nation-state actor, had gained access to the 
> Company's IT network."

Link: https://www.sec.gov/ix?doc=/Archives/edgar/data/1708055/000170805525000035/rbbn-20250930x10q.htm
