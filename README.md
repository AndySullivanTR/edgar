# SEC EDGAR Monitoring Tools

Two automated monitoring scripts for tracking corporate disclosures in SEC EDGAR filings for investigative journalism.

## Scripts

### 1. SEC Cybersecurity Incident Monitor (`sec_cyber_monitor.py`)
Monitors all SEC filings for disclosures of nation-state cyber attacks.

### 2. BIS/China Trade Monitor (`bis_china_monitor.py`)
Monitors semiconductor companies for disclosures of China-related trade restrictions from the Bureau of Industry and Security (BIS).

---

## Setup

### Prerequisites
- Python 3.8+
- Dependencies listed in `requirements.txt`

### Installation

```bash
pip install -r requirements.txt
```

### Configuration

Both scripts require updating the output directory path and email settings:

**In `sec_cyber_monitor.py` and `bis_china_monitor.py`:**
- Update `OUTPUT_DIR` to your desired location
- Configure email settings (Gmail address and app password)
- Update `ALERT_RECIPIENTS` list

---

## 1. Cybersecurity Incident Monitor

### What It Does
- Polls SEC's RSS feed every 15 minutes for new filings
- Searches all filing types (10-K, 10-Q, 8-K, etc.) from all companies
- Uses proximity search to find cyber incident terms near nation-state references
- Logs matches to CSV with excerpts and links

### Running
```bash
python sec_cyber_monitor.py
```

### Output
Results saved to: `cyber_incidents.csv`

**CSV columns:**
- timestamp
- company_name
- cik (SEC Central Index Key)
- filing_type
- filing_date
- filing_url
- page_number (estimated, if detectable)
- cyber_terms_found
- nation_state_terms_found
- excerpt (~400 characters)

### Search Terms

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

### Example Match
Ribbon Communications 10-Q (September 2025):
> "In early September 2025, the Company became aware that unauthorized persons, 
> reportedly associated with a nation-state actor, had gained access to the 
> Company's IT network."

Link: https://www.sec.gov/ix?doc=/Archives/edgar/data/1708055/000170805525000035/rbbn-20250930x10q.htm

---

## 2. BIS/China Trade Monitor

### What It Does
- Monitors filings from 31 Philadelphia Semiconductor Index (SOX) companies plus Cadence and Synopsys
- Checks every 5 minutes for new filings
- Searches for mentions of China-related trade restrictions from BIS
- Sends email alerts for matches
- Logs matches to CSV with excerpts and links

### Running
```bash
python bis_china_monitor.py
```

### Monitored Companies
31 SOX index constituents plus:
- Cadence Design Systems (CDNS)
- Synopsys (SNPS)

Full list includes: AMD, ADI, AMAT, AMKR, ARM, ASML, AVGO, CDNS, COHR, CRUS, ENTG, GFS, INTC, KLAC, LRCX, LSCC, MCHP, MPWR, MRVL, MTSI, MU, NVDA, NXPI, ON, ONTO, QCOM, QRVO, SWKS, TER, TSM, TXN, SNPS

### Output
Results saved to: `bis_china_disclosures.csv`

**CSV columns:**
- timestamp
- company_name
- ticker
- cik
- filing_type
- filing_date
- filing_url
- bis_terms_found
- china_terms_found
- excerpt (~500 characters)

### Search Terms

**BIS/Trade terms:**
- bureau of industry and security, bis
- export control, export restriction
- export administration regulations, ear
- commerce department, trade restriction
- entity list, denied persons list, unverified list
- license required, license is now required
- licensing requirement, export license
- commerce control list
- military end use, military end user
- unacceptable risk
- export control classification

**China terms:**
- china, chinese, prc, people's republic of china

**Proximity:** Terms must appear within 150 words of each other.

### Example Match
Cadence Design Systems 8-K (May 2025):
> Disclosure of BIS export control changes affecting sales to Chinese customers,
> including language "license is now required"

---

## How Both Scripts Work

1. Fetch SEC RSS feed (updated every 10 minutes by SEC)
2. Parse filing metadata (company, type, date, URL)
3. Download full filing HTML
4. Extract text content
5. Perform proximity search for term combinations
6. Log matches to CSV
7. Send email alerts (BIS monitor only)
8. Track processed filings to avoid duplicates
9. Repeat at configured interval

## Files Created

### Cyber Monitor:
- `cyber_incidents.csv` - Match results
- `seen_filings.json` - Tracking file

### BIS Monitor:
- `bis_china_disclosures.csv` - Match results
- `bis_seen_filings.json` - Tracking file

## Notes

- Both scripts respect SEC rate limits (0.1-0.15 second delays between requests)
- Use Reuters user agent for identification
- BIS monitor filters RSS feed to only process target companies
- Cyber monitor processes all companies
- Press Ctrl+C to stop either monitor
- Can run both simultaneously in separate terminal windows/screens

## Deployment

### Local Development
Run scripts directly in separate terminal windows.

### Production (VPS)
Use `screen` or `tmux` to keep scripts running:

```bash
# Start cyber monitor
screen -S cyber
python3 sec_cyber_monitor.py
# Press Ctrl+A then D to detach

# Start BIS monitor
screen -S bis
python3 bis_china_monitor.py
# Press Ctrl+A then D to detach

# Reattach to check status
screen -r cyber
screen -r bis
```

## Updates

When scripts are updated:
```bash
git pull
# Then restart the appropriate monitor(s)
```
