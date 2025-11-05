#!/usr/bin/env python3
"""
SEC EDGAR BIS/China Trade Monitor - GitHub Actions Version
Monitors semiconductor company filings for BIS/China trade restriction mentions
Runs once per execution (no infinite loop)
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
import os

# Configuration
OUTPUT_DIR = Path(".")  # Current directory for GitHub Actions
CSV_FILE = OUTPUT_DIR / "bis_china_disclosures.csv"
SEEN_FILE = OUTPUT_DIR / "bis_seen_filings.json"
USER_AGENT = "Andy Sullivan andy.sullivan@thomsonreuters.com"

# Email configuration from environment variables
GMAIL_ADDRESS = os.getenv('GMAIL_ADDRESS')
GMAIL_APP_PASSWORD = os.getenv('GMAIL_APP_PASSWORD')
ALERT_RECIPIENTS = os.getenv('ALERT_RECIPIENTS', '').split(',')

# Target companies (semiconductor manufacturers)
TARGET_COMPANIES = {
    'NVDA': {'name': 'NVIDIA Corporation', 'cik': '0001045810'},
    'INTC': {'name': 'Intel Corporation', 'cik': '0000050863'},
    'AMD': {'name': 'Advanced Micro Devices', 'cik': '0000002488'},
    'TSM': {'name': 'Taiwan Semiconductor', 'cik': '0001046179'},
    'QCOM': {'name': 'Qualcomm', 'cik': '0000804328'},
    'AVGO': {'name': 'Broadcom', 'cik': '0001730168'},
    'TXN': {'name': 'Texas Instruments', 'cik': '0000097476'},
    'MU': {'name': 'Micron Technology', 'cik': '0000723125'},
    'AMAT': {'name': 'Applied Materials', 'cik': '0000006951'},
    'LRCX': {'name': 'Lam Research', 'cik': '0000707549'},
    'ASML': {'name': 'ASML Holding', 'cik': '0000937966'},
    'KLAC': {'name': 'KLA Corporation', 'cik': '0000319201'},
    'ADI': {'name': 'Analog Devices', 'cik': '0000006281'},
    'MRVL': {'name': 'Marvell Technology', 'cik': '0001058057'},
    'NXPI': {'name': 'NXP Semiconductors', 'cik': '0001413447'},
    'ON': {'name': 'ON Semiconductor', 'cik': '0001097864'},
    'MCHP': {'name': 'Microchip Technology', 'cik': '0000827054'},
    'MPWR': {'name': 'Monolithic Power Systems', 'cik': '0001280452'},
    'STM': {'name': 'STMicroelectronics', 'cik': '0001066134'},
    'WOLF': {'name': 'Wolfspeed', 'cik': '0000895419'},
    'SWKS': {'name': 'Skyworks Solutions', 'cik': '0000004127'},
    'QRVO': {'name': 'Qorvo', 'cik': '0001604778'},
    'GFS': {'name': 'GlobalFoundries', 'cik': '0001709048'},
    'UMC': {'name': 'United Microelectronics', 'cik': '0000913228'},
    'POWI': {'name': 'Power Integrations', 'cik': '0000833640'},
    'ONTO': {'name': 'Onto Innovation', 'cik': '0001096906'},
    'CRUS': {'name': 'Cirrus Logic', 'cik': '0000772406'},
    'SLAB': {'name': 'Silicon Laboratories', 'cik': '0001066959'},
    'ALGM': {'name': 'Allegro MicroSystems', 'cik': '0001773240'},
    'SIMO': {'name': 'Silicon Motion Technology', 'cik': '0001090370'},
    'SMTC': {'name': 'Semtech Corporation', 'cik': '0000203077'},
    'NVMI': {'name': 'Nova', 'cik': '0001407613'},
    'COHR': {'name': 'Coherent Corp', 'cik': '0001085276'}
}

# Search terms for BIS/trade restrictions
BIS_TERMS = [
    "bureau of industry and security",
    "export control",
    "export controls",
    "export restriction",
    "export restrictions",
    "export license",
    "export licenses",
    "trade restriction",
    "trade restrictions",
    "foreign direct product rule",
    "entity list",
    "denied persons list",
    "unverified list",
    "commerce control list",
    "export administration regulations"
]

CHINA_TERMS = [
    "china",
    "chinese",
    "prc",
    "people's republic of china"
]

PROXIMITY_WINDOW = 100

class BISChinaMonitor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.seen_filings = self.load_seen_filings()
        self.initialize_csv()
    
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
            
            server = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            text = msg.as_string()
            server.sendmail(GMAIL_ADDRESS, ALERT_RECIPIENTS, text)
            server.quit()
            
            print(f"  Email alert sent to {', '.join(ALERT_RECIPIENTS)}")
        except Exception as e:
            print(f"  Error sending email: {e}")
    
    def fetch_company_filings(self, cik):
        """Fetch recent filings for a specific company"""
        try:
            # Pad CIK with zeros to 10 digits
            cik_padded = cik.lstrip('0').zfill(10)
            url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
            
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            time.sleep(0.1)
            
            data = response.json()
            recent_filings = data.get('filings', {}).get('recent', {})
            
            filings = []
            for i in range(len(recent_filings.get('accessionNumber', []))):
                filing_type = recent_filings['form'][i]
                accession = recent_filings['accessionNumber'][i]
                filing_date = recent_filings['filingDate'][i]
                
                # Only check 8-K, 10-K, 10-Q filings
                if filing_type in ['8-K', '10-K', '10-Q']:
                    accession_no_dashes = accession.replace('-', '')
                    filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}/{accession_no_dashes}/{accession}.txt"
                    
                    filings.append({
                        'company_name': data.get('name'),
                        'cik': cik,
                        'filing_type': filing_type,
                        'filing_date': filing_date,
                        'filing_url': filing_url
                    })
            
            return filings[:10]  # Return 10 most recent
            
        except Exception as e:
            print(f"Error fetching filings for CIK {cik}: {e}")
            return []
    
    def fetch_filing_content(self, filing_url):
        """Fetch the actual filing document content"""
        try:
            response = self.session.get(filing_url, timeout=30)
            response.raise_for_status()
            time.sleep(0.1)
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
            print(f"Error extracting text: {e}")
            return ""
    
    def proximity_search(self, text, terms1, terms2, window=100):
        """Search for terms1 within 'window' words of terms2"""
        text_lower = text.lower()
        matches = []
        
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
    
    def process_filing(self, filing, ticker):
        """Process a single filing and check for matches"""
        if filing['filing_url'] in self.seen_filings:
            return False
        
        # ONLY process 8-K filings (material events, not routine disclosures)
        if filing['filing_type'] not in ['8-K']:
            self.seen_filings.add(filing['filing_url'])
            return False
        
        print(f"  Checking: {filing['filing_type']} from {filing['filing_date']}")
        
        content = self.fetch_filing_content(filing['filing_url'])
        if not content:
            self.seen_filings.add(filing['filing_url'])
            return False
        
        text = self.extract_text_from_html(content)
        matches = self.proximity_search(text, BIS_TERMS, CHINA_TERMS, PROXIMITY_WINDOW)
        
        if matches:
            print(f"    *** MATCH FOUND ***")
            bis_terms_found = list(set([m[0] for m in matches]))
            china_terms_found = list(set([m[1] for m in matches]))
            excerpt = matches[0][2]
            
            self.log_match(filing, bis_terms_found, china_terms_found, excerpt, ticker)
            self.seen_filings.add(filing['filing_url'])
            return True
        
        self.seen_filings.add(filing['filing_url'])
        return False
    
    def run_once(self):
        """Run a single check (for GitHub Actions)"""
        print(f"BIS/China Trade Monitor - Single Run")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
        print(f"Monitoring {len(TARGET_COMPANIES)} semiconductor companies")
        
        total_matches = 0
        
        for ticker, company_info in TARGET_COMPANIES.items():
            print(f"\n{ticker} - {company_info['name']}")
            
            filings = self.fetch_company_filings(company_info['cik'])
            print(f"  Found {len(filings)} recent filings")
            
            for filing in filings:
                if self.process_filing(filing, ticker):
                    total_matches += 1
        
        self.save_seen_filings()
        
        if total_matches > 0:
            print(f"\n*** {total_matches} MATCH(ES) FOUND ***")
        else:
            print("\nNo matches found")
        
        print("Check complete")

if __name__ == "__main__":
    monitor = BISChinaMonitor()
    monitor.run_once()