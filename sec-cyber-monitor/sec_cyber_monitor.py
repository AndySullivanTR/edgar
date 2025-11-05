#!/usr/bin/env python3
"""
SEC EDGAR Cybersecurity Incident Monitor
Monitors SEC filings for mentions of nation-state cyber attacks
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
from urllib.parse import urljoin
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configuration
RSS_FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&CIK=&type=&company=&dateb=&owner=include&start=0&count=100&output=atom"
CHECK_INTERVAL = 300  # 5 minutes in seconds
OUTPUT_DIR = Path(r"C:\Users\8010317\projects\government-bots\SEC-cyber")
CSV_FILE = OUTPUT_DIR / "cyber_incidents.csv"
SEEN_FILE = OUTPUT_DIR / "seen_filings.json"
USER_AGENT = "Andy Sullivan andy.sullivan@thomsonreuters.com"

# Email configuration
GMAIL_ADDRESS = "andy.sullivan@gmail.com"
GMAIL_APP_PASSWORD = "awgoydeecxhksgwt"
ALERT_RECIPIENTS = ["andy.sullivan@thomsonreuters.com", "chris.sanders@thomsonreuters.com"]

# Search terms
CYBER_TERMS = [
    "cybersecurity incident",
    "cyber incident", 
    "cyber attack",
    "cyberattack",
    "data breach",
    "security breach",
    "unauthorized access",
    "network intrusion",
    "security intrusion",
    "malicious actor",
    "threat actor"
]

NATION_STATE_TERMS = [
    "nation-state",
    "nation state",
    "state-sponsored",
    "state sponsored",
    "foreign government",
    "foreign actor",
    "foreign threat",
    "advanced persistent threat",
    "apt ",
    "apt,",
    "apt."
]

PROXIMITY_WINDOW = 100  # words

class SECCyberMonitor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.seen_filings = self.load_seen_filings()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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
                    'cik',
                    'filing_type',
                    'filing_date',
                    'filing_url',
                    'page_number',
                    'cyber_terms_found',
                    'nation_state_terms_found',
                    'excerpt'
                ])
    
    def send_email(self, filing, excerpt, cyber_terms, nation_terms):
        """Send email alert when a match is found"""
        try:
            msg = MIMEMultipart()
            msg['From'] = GMAIL_ADDRESS
            msg['To'] = ', '.join(ALERT_RECIPIENTS)
            msg['Subject'] = f"SEC Cyber Alert: {filing['company_name']} - {filing['filing_type']}"
            
            body = f"""
A potential nation-state cyber incident disclosure has been detected:

Company: {filing['company_name']}
Filing Type: {filing['filing_type']}
Filing Date: {filing['filing_date']}
Filing URL: {filing['filing_url']}

Cyber Terms Found: {', '.join(cyber_terms)}
Nation-State Terms Found: {', '.join(nation_terms)}

Excerpt:
{excerpt}

---
This is an automated alert from the SEC Cyber Monitor.
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
    
    def get_last_check_time(self):
        """Get timestamp of most recent check from CSV (Option C for overnight gaps)"""
        if not CSV_FILE.exists():
            return None
        
        try:
            with open(CSV_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                if rows:
                    # Get the most recent timestamp
                    last_timestamp = rows[-1]['timestamp']
                    return datetime.fromisoformat(last_timestamp)
        except Exception as e:
            print(f"Error reading last check time: {e}")
        
        return None
    
    def fetch_rss_feed(self, start=0):
        """Fetch and parse the SEC RSS feed"""
        try:
            url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&CIK=&type=&company=&dateb=&owner=include&start={start}&count=100&output=atom"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            time.sleep(0.1)  # Be nice to SEC servers
            return response.content
        except Exception as e:
            print(f"Error fetching RSS feed: {e}")
            return None
    
    def parse_rss_feed(self, xml_content):
        """Parse RSS feed and extract filing information"""
        filings = []
        try:
            root = ET.fromstring(xml_content)
            # Handle Atom namespace
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            
            for entry in root.findall('atom:entry', ns):
                title = entry.find('atom:title', ns)
                link = entry.find('atom:link', ns)
                updated = entry.find('atom:updated', ns)
                
                if title is not None and link is not None:
                    title_text = title.text
                    # Parse title: "8-K - COMPANY NAME (CIK)"
                    match = re.search(r'^([\w\-/]+)\s+-\s+(.+?)\s+\((\d+)\)', title_text)
                    if match:
                        filing_type = match.group(1)
                        company_name = match.group(2)
                        cik = match.group(3)
                        filing_url = link.get('href')
                        filing_date = updated.text if updated is not None else ''
                        
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
            # Handle inline XBRL URLs (/ix?doc=...)
            url = filing_url
            if '/ix?doc=' in filing_url:
                # Extract the actual document path
                doc_path = filing_url.split('/ix?doc=')[1]
                # Build the direct URL
                url = f"https://www.sec.gov{doc_path}"
            
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            time.sleep(0.1)  # Rate limiting
            return response.text
        except Exception as e:
            print(f"Error fetching filing content from {filing_url}: {e}")
            return None
    
    def extract_text_from_html(self, html_content):
        """Extract text from HTML filing"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            text = soup.get_text(separator=' ', strip=True)
            return text
        except Exception as e:
            print(f"Error extracting text from HTML: {e}")
            return ""
    
    def find_page_number(self, html_content, text, position):
        """
        Try to determine page number near a position in the text
        Looks for common page indicators in SEC filings
        """
        # Strategy 1: Look in the extracted text around the position
        search_start = max(0, position - 3000)
        search_end = min(len(text), position + 500)
        search_text = text[search_start:search_end]
        
        # Get a snippet to search in raw HTML too
        text_snippet = text[max(0, position - 100):min(len(text), position + 100)]
        text_snippet = text_snippet.strip()[:50]  # First 50 chars for searching
        
        # Find this snippet in HTML to get the HTML position
        html_lower = html_content.lower()
        text_lower = text.lower()
        
        # Common patterns in SEC filings
        patterns = [
            (r'<[^>]*>\s*(\d+)\s*<', 'HTML tag with number'),  # Number between tags
            (r'page\s+(\d+)', 'Page indicator'),
            (r'^\s*(\d+)\s*$', 'Standalone number on line'),
            (r'\|\s*(\d+)\s*\|', 'Number between pipes'),
            (r'(?:^|\n)\s*(\d+)\s*(?:\n|$)', 'Number on own line'),
        ]
        
        # Try to find in nearby text first
        page_num = None
        for pattern, desc in patterns:
            matches = list(re.finditer(pattern, search_text, re.MULTILINE | re.IGNORECASE))
            if matches:
                # Look for numbers that could be page numbers (typically 1-300 for most filings)
                for match in reversed(matches):  # Start from closest to position
                    num = match.group(1)
                    if num.isdigit() and 1 <= int(num) <= 300:
                        page_num = num
                        break
                if page_num:
                    break
        
        # Strategy 2: If no page found in text, try searching HTML near the match
        if not page_num and text_snippet:
            # Find where our text appears in HTML
            snippet_clean = re.sub(r'\s+', ' ', text_snippet.lower())
            html_clean = re.sub(r'\s+', ' ', html_lower)
            
            html_pos = html_clean.find(snippet_clean[:40])
            if html_pos != -1:
                # Search backwards in HTML for page indicators
                html_search = html_content[max(0, html_pos - 2000):html_pos]
                
                # Look for common HTML page markers
                html_patterns = [
                    r'<[^>]*page[^>]*>.*?(\d+).*?</',
                    r'<[^>]*>\s*(\d+)\s*</[^>]*>',
                    r'style="[^"]*page[^"]*"[^>]*>(\d+)',
                ]
                
                for pattern in html_patterns:
                    matches = list(re.finditer(pattern, html_search, re.IGNORECASE | re.DOTALL))
                    if matches:
                        for match in reversed(matches):
                            num = match.group(1)
                            if num.isdigit() and 1 <= int(num) <= 300:
                                page_num = num
                                break
                        if page_num:
                            break
        
        return page_num
    
    def proximity_search(self, html_content, text, terms1, terms2, window=100):
        """
        Search for terms1 within 'window' words of terms2
        Returns list of (term1, term2, excerpt, page_num) tuples
        """
        text_lower = text.lower()
        matches = []
        
        # Find all positions of both term sets
        cyber_positions = []
        for term in terms1:
            term_lower = term.lower()
            start = 0
            while True:
                pos = text_lower.find(term_lower, start)
                if pos == -1:
                    break
                cyber_positions.append((pos, term))
                start = pos + 1
        
        nation_positions = []
        for term in terms2:
            term_lower = term.lower()
            start = 0
            while True:
                pos = text_lower.find(term_lower, start)
                if pos == -1:
                    break
                nation_positions.append((pos, term))
                start = pos + 1
        
        # Check for proximity
        for cyber_pos, cyber_term in cyber_positions:
            for nation_pos, nation_term in nation_positions:
                # Calculate word distance (rough approximation)
                char_distance = abs(cyber_pos - nation_pos)
                word_distance = char_distance / 6  # Average word length + space
                
                if word_distance <= window:
                    # Extract excerpt around the match
                    start_pos = max(0, min(cyber_pos, nation_pos) - 200)
                    end_pos = min(len(text), max(cyber_pos, nation_pos) + 200)
                    excerpt = text[start_pos:end_pos].strip()
                    
                    # Try to find page number
                    match_pos = min(cyber_pos, nation_pos)
                    page_num = self.find_page_number(html_content, text, match_pos)
                    
                    matches.append((cyber_term, nation_term, excerpt, page_num))
        
        return matches
    
    def log_match(self, filing, cyber_terms, nation_terms, excerpt, page_num):
        """Log a matching filing to CSV and send email alert"""
        with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().isoformat(),
                filing['company_name'],
                filing['cik'],
                filing['filing_type'],
                filing['filing_date'],
                filing['filing_url'],
                page_num if page_num else 'N/A',
                ', '.join(set(cyber_terms)),
                ', '.join(set(nation_terms)),
                excerpt[:500]  # Limit excerpt length
            ])
        
        # Send email alert
        self.send_email(filing, excerpt, cyber_terms, nation_terms)
    
    def process_filing(self, filing):
        """Process a single filing and check for matches"""
        if filing['filing_url'] in self.seen_filings:
            return False
        
        print(f"Checking: {filing['company_name']} - {filing['filing_type']}")
        
        content = self.fetch_filing_content(filing['filing_url'])
        if not content:
            self.seen_filings.add(filing['filing_url'])
            return False
        
        text = self.extract_text_from_html(content)
        
        matches = self.proximity_search(content, text, CYBER_TERMS, NATION_STATE_TERMS, PROXIMITY_WINDOW)
        
        if matches:
            print(f"  *** MATCH FOUND: {filing['company_name']} ***")
            # Combine all unique terms found
            cyber_terms_found = list(set([m[0] for m in matches]))
            nation_terms_found = list(set([m[1] for m in matches]))
            # Use the first match's excerpt and page number
            excerpt = matches[0][2]
            page_num = matches[0][3]
            
            if page_num:
                print(f"  Page: {page_num}")
            
            self.log_match(filing, cyber_terms_found, nation_terms_found, excerpt, page_num)
            self.seen_filings.add(filing['filing_url'])
            return True
        
        self.seen_filings.add(filing['filing_url'])
        return False
    
    def run(self):
        """Main monitoring loop"""
        print(f"Starting SEC Cybersecurity Incident Monitor")
        print(f"Output directory: {OUTPUT_DIR}")
        print(f"Checking every {CHECK_INTERVAL/60} minutes")
        print(f"Email alerts will be sent to: {', '.join(ALERT_RECIPIENTS)}")
        print(f"Press Ctrl+C to stop\n")
        
        try:
            while True:
                print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking for new filings...")
                
                # Check if we've been offline (Option C)
                last_check = self.get_last_check_time()
                if last_check:
                    time_since_last = datetime.now() - last_check
                    if time_since_last.total_seconds() > 7200:  # More than 2 hours
                        print(f"  Detected {time_since_last.total_seconds()/3600:.1f} hour gap since last check - doing deep scan")
                
                # Fetch filings with pagination (Option B)
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
                
                print(f"Found {len(all_filings)} new filings to process")
                
                matches_found = 0
                for filing in all_filings:
                    if self.process_filing(filing):
                        matches_found += 1
                
                if matches_found > 0:
                    print(f"\n*** {matches_found} MATCH(ES) FOUND THIS CYCLE ***")
                
                self.save_seen_filings()
                
                print(f"Next check in {CHECK_INTERVAL/60} minutes...")
                time.sleep(CHECK_INTERVAL)
                
        except KeyboardInterrupt:
            print("\n\nMonitoring stopped by user")
            self.save_seen_filings()

if __name__ == "__main__":
    monitor = SECCyberMonitor()
    monitor.run()
