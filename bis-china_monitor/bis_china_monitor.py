#!/usr/bin/env python3
"""
SEC EDGAR BIS/China Trade Monitor - FINAL VERSION
Monitors filings from SOX semiconductor companies + Synopsys
for mentions of China-related trade restrictions from BIS
"""

import requests
import time
import csv
import re
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configuration
CHECK_INTERVAL = 300  # 5 minutes
OUTPUT_DIR = Path(r"C:\Users\8010317\projects\government-bots\SEC-cyber")
CSV_FILE = OUTPUT_DIR / "bis_china_disclosures.csv"
SEEN_FILE = OUTPUT_DIR / "bis_seen_filings.json"
USER_AGENT = "Andy Sullivan andy.sullivan@thomsonreuters.com"

# Email configuration
GMAIL_ADDRESS = "andy.sullivan@gmail.com"
GMAIL_APP_PASSWORD = "awgoydeecxhksgwt"
ALERT_RECIPIENTS = ["andy.sullivan@thomsonreuters.com", "chris.sanders@thomsonreuters.com"]

# SOX Index companies + Cadence + Synopsys
COMPANIES = {
    'AMD': ('Advanced Micro Devices Inc', '0000002488'),
    'ADI': ('Analog Devices Inc', '0000006281'),
    'AMAT': ('Applied Materials Inc', '0000006951'),
    'AMKR': ('Amkor Technology Inc', '0001047127'),
    'ARM': ('Arm Holdings PLC', '0001973723'),
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
    'ONTO': ('Onto Innovation Inc', '0001517413'),
    'QCOM': ('QUALCOMM Inc', '0000804328'),
    'QRVO': ('Qorvo Inc', '0001604778'),
    'SWKS': ('Skyworks Solutions Inc', '0000004127'),
    'TER': ('Teradyne Inc', '0000097210'),
    'TSM': ('Taiwan Semiconductor Manufacturing Co Ltd', '0001046179'),
    'TXN': ('Texas Instruments Inc', '0000097476'),
    'SNPS': ('Synopsys Inc', '0000883241')
}

# Search terms - based on actual Cadence filing language
BIS_TERMS = [
    "bureau of industry and security",
    "bis",
    "export control",
    "export controls",
    "export restriction",
    "export restrictions",
    "export administration regulations",
    "ear",
    "commerce department",
    "trade restriction",
    "trade restrictions",
    "entity list",
    "denied persons list",
    "unverified list",
    "license required",
    "license is required",
    "license is now required",
    "licensing requirement",
    "licensing requirements",
    "export license",
    "commerce control list",
    "military end use",
    "military end user",
    "unacceptable risk",
    "export control classification"
]

CHINA_TERMS = [
    "china",
    "chinese",
    "prc",
    "people's republic of china"
]

PROXIMITY_WINDOW = 150  # Increased from 100 words

class BISChinaMonitor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.seen_filings = self.load_seen_filings()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.initialize_csv()
        
        # Create CIK lookup for filtering
        self.target_ciks = set(cik for _, cik in COMPANIES.values())
    
    def load_seen_filings(self):
        """Load set of already-processed filing URLs"""
        if SEEN_FILE.exists():
            with open(SEEN_FILE, 'r') as f:
                return set(json.load(f))
        return set()
    
    def save_seen_filings(self):
        """Save set of processed filing URLs"""
        with open(SEEN_FILE, 'w') as f:
            json.dump(list(self.seen_filings), f)
    
    def initialize_csv(self):
        """Create CSV file with headers if it doesn't exist"""
        if not CSV_FILE.exists():
            with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp',
                    'company_name',
                    'ticker',
                    'cik',
                    'filing_type',
                    'filing_date',
                    'filing_url',
                    'bis_terms_found',
                    'china_terms_found',
                    'excerpt'
                ])
    
    def send_email(self, filing, excerpt, bis_terms, china_terms, ticker):
        """Send email alert when a match is found"""
        try:
            msg = MIMEMultipart()
            msg['From'] = GMAIL_ADDRESS
            msg['To'] = ', '.join(ALERT_RECIPIENTS)
            msg['Subject'] = f"BIS/China Alert: {filing['company_name']} ({ticker}) - {filing['filing_type']}"
            
            body = f"""
A potential BIS/China trade restriction disclosure has been detected:

Company: {filing['company_name']} ({ticker})
Filing Type: {filing['filing_type']}
Filing Date: {filing['filing_date']}
Filing URL: {filing['filing_url']}

BIS/Trade Terms Found: {', '.join(bis_terms)}
China Terms Found: {', '.join(china_terms)}

Excerpt:
{excerpt}

---
This is an automated alert from the BIS/China Trade Monitor.
"""
            
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            text = msg.as_string()
            server.sendmail(GMAIL_ADDRESS, ALERT_RECIPIENTS, text)
            server.quit()
            
            print(f"  Email alert sent to {', '.join(ALERT_RECIPIENTS)}")
        except Exception as e:
            print(f"  Error sending email: {e}")
    
    def fetch_rss_feed(self, start=0):
        """Fetch SEC RSS feed"""
        try:
            url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&CIK=&type=&company=&dateb=&owner=include&start={start}&count=100&output=atom"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            time.sleep(0.15)  # Slightly increased delay
            return response.content
        except Exception as e:
            print(f"Error fetching RSS feed: {e}")
            return None
    
    def parse_rss_feed(self, xml_content):
        """Parse RSS feed and extract filing information for target companies only"""
        filings = []
        try:
            root = ET.fromstring(xml_content)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            
            for entry in root.findall('atom:entry', ns):
                title = entry.find('atom:title', ns)
                link = entry.find('atom:link', ns)
                updated = entry.find('atom:updated', ns)
                
                if title is not None and link is not None:
                    title_text = title.text
                    match = re.search(r'^([\w\-/]+)\s+-\s+(.+?)\s+\((\d+)\)', title_text)
                    if match:
                        filing_type = match.group(1)
                        company_name = match.group(2)
                        cik = match.group(3).zfill(10)
                        filing_url = link.get('href')
                        filing_date = updated.text if updated is not None else ''
                        
                        # Only include filings from target companies
                        if cik in self.target_ciks:
                            filings.append({
                                'company_name': company_name,
                                'cik': cik,
                                'filing_type': filing_type,
                                'filing_date': filing_date,
                                'filing_url': filing_url
                            })
        except Exception as e:
            print(f"Error parsing RSS feed: {e}")
        
        return filings
    
    def fetch_filing_content(self, filing_url):
        """Fetch the actual filing document content"""
        try:
            url = filing_url
            if '/ix?doc=' in filing_url:
                doc_path = filing_url.split('/ix?doc=')[1]
                url = f"https://www.sec.gov{doc_path}"
            
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            time.sleep(0.15)
            return response.text
        except Exception as e:
            print(f"Error fetching filing content: {e}")
            return None
    
    def extract_text_from_html(self, html_content):
        """Extract text from HTML filing"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            for script in soup(["script", "style"]):
                script.decompose()
            text = soup.get_text(separator=' ', strip=True)
            return text
        except Exception as e:
            return ""
    
    def proximity_search(self, text, terms1, terms2, window=150):
        """Search for terms1 within 'window' words of terms2"""
        text_lower = text.lower()
        matches = []
        
        # Find all positions of both term sets
        bis_positions = []
        for term in terms1:
            term_lower = term.lower()
            start = 0
            while True:
                pos = text_lower.find(term_lower, start)
                if pos == -1:
                    break
                bis_positions.append((pos, term))
                start = pos + 1
        
        china_positions = []
        for term in terms2:
            term_lower = term.lower()
            start = 0
            while True:
                pos = text_lower.find(term_lower, start)
                if pos == -1:
                    break
                china_positions.append((pos, term))
                start = pos + 1
        
        # Check for proximity
        for bis_pos, bis_term in bis_positions:
            for china_pos, china_term in china_positions:
                char_distance = abs(bis_pos - china_pos)
                word_distance = char_distance / 6
                
                if word_distance <= window:
                    start_pos = max(0, min(bis_pos, china_pos) - 200)
                    end_pos = min(len(text), max(bis_pos, china_pos) + 200)
                    excerpt = text[start_pos:end_pos].strip()
                    matches.append((bis_term, china_term, excerpt))
        
        return matches
    
    def get_ticker_for_cik(self, cik):
        """Get ticker symbol for a given CIK"""
        for ticker, (name, company_cik) in COMPANIES.items():
            if company_cik == cik:
                return ticker
        return "UNKNOWN"
    
    def log_match(self, filing, bis_terms, china_terms, excerpt, ticker):
        """Log a matching filing to CSV and send email alert"""
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().isoformat(),
                filing['company_name'],
                ticker,
                filing['cik'],
                filing['filing_type'],
                filing['filing_date'],
                filing['filing_url'],
                ', '.join(set(bis_terms)),
                ', '.join(set(china_terms)),
                excerpt[:500]
            ])
        
        self.send_email(filing, excerpt, bis_terms, china_terms, ticker)
    
    def process_filing(self, filing):
        """Process a single filing and check for matches"""
        if filing['filing_url'] in self.seen_filings:
            return False
        
        ticker = self.get_ticker_for_cik(filing['cik'])
        print(f"Checking: {filing['company_name']} ({ticker}) - {filing['filing_type']}")
        
        content = self.fetch_filing_content(filing['filing_url'])
        if not content:
            self.seen_filings.add(filing['filing_url'])
            return False
        
        text = self.extract_text_from_html(content)
        matches = self.proximity_search(text, BIS_TERMS, CHINA_TERMS, PROXIMITY_WINDOW)
        
        if matches:
            print(f"  *** MATCH FOUND: {filing['company_name']} ({ticker}) ***")
            bis_terms_found = list(set([m[0] for m in matches]))
            china_terms_found = list(set([m[1] for m in matches]))
            excerpt = matches[0][2]
            
            self.log_match(filing, bis_terms_found, china_terms_found, excerpt, ticker)
            self.seen_filings.add(filing['filing_url'])
            return True
        
        self.seen_filings.add(filing['filing_url'])
        return False
    
    def run(self):
        """Main monitoring loop"""
        print(f"Starting BIS/China Trade Monitor")
        print(f"Output directory: {OUTPUT_DIR}")
        print(f"Monitoring {len(COMPANIES)} semiconductor companies (SOX + CDNS + SNPS)")
        print(f"Checking every {CHECK_INTERVAL/60} minutes")
        print(f"Email alerts will be sent to: {', '.join(ALERT_RECIPIENTS)}")
        print(f"Press Ctrl+C to stop\n")
        
        try:
            while True:
                print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking for new filings...")
                
                # Fetch filings with pagination
                all_filings = []
                start = 0
                while True:
                    xml_content = self.fetch_rss_feed(start)
                    if not xml_content:
                        break
                    
                    filings = self.parse_rss_feed(xml_content)
                    if not filings:
                        break
                    
                    # Check if we've hit filings we've already seen
                    new_filings = []
                    for filing in filings:
                        if filing['filing_url'] not in self.seen_filings:
                            new_filings.append(filing)
                    
                    all_filings.extend(new_filings)
                    
                    # If all filings in this batch were already seen, stop paginating
                    if len(new_filings) == 0:
                        print(f"  All filings in batch starting at {start} already processed")
                        break
                    
                    # If we got fewer than 100 new filings, we've likely hit the end
                    if len(new_filings) < 100:
                        break
                    
                    start += 100
                    print(f"  Fetching next page (start={start})...")
                
                if all_filings:
                    print(f"Found {len(all_filings)} new filings from target companies to process")
                    
                    matches_found = 0
                    for filing in all_filings:
                        if self.process_filing(filing):
                            matches_found += 1
                    
                    if matches_found > 0:
                        print(f"\n*** {matches_found} MATCH(ES) FOUND THIS CYCLE ***")
                else:
                    print("No new filings from target companies")
                
                self.save_seen_filings()
                
                print(f"Next check in {CHECK_INTERVAL/60} minutes...")
                time.sleep(CHECK_INTERVAL)
                
        except KeyboardInterrupt:
            print("\n\nMonitoring stopped by user")
            self.save_seen_filings()

if __name__ == "__main__":
    monitor = BISChinaMonitor()
    monitor.run()
