from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime
import requests
import re
from typing import List, Dict, Optional

app = Flask(__name__)
CORS(app)

DB_FILE = 'prospects.db'
FIRECRAWL_API_KEY = 'fc-186f6e0ea3cc4cc29732bb15a9a1b1d9'
FIRECRAWL_BASE_URL = 'https://api.firecrawl.dev/v1'

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS prospects (
        id TEXT PRIMARY KEY,
        name TEXT,
        company TEXT,
        title TEXT,
        email TEXT,
        status TEXT,
        deal_size REAL,
        created_at TEXT,
        source TEXT,
        linkedin_url TEXT,
        notes TEXT
    )''')
    
    conn.commit()
    conn.close()

init_db()

class FirecrawlClient:
    """Client for interacting with Firecrawl API"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = FIRECRAWL_BASE_URL
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
    
    def scrape_url(self, url: str, formats: List[str] = None) -> Dict:
        """Scrape a single URL using Firecrawl"""
        if formats is None:
            formats = ['markdown', 'html']
        
        endpoint = f'{self.base_url}/scrape'
        payload = {
            'url': url,
            'formats': formats
        }
        
        try:
            response = requests.post(endpoint, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error scraping {url}: {str(e)}")
            return None
    
    def crawl_website(self, url: str, limit: int = 10, scrape_options: Dict = None) -> Dict:
        """Crawl a website starting from a URL"""
        endpoint = f'{self.base_url}/crawl'
        
        payload = {
            'url': url,
            'limit': limit,
            'scrapeOptions': scrape_options or {
                'formats': ['markdown'],
                'onlyMainContent': True
            }
        }
        
        try:
            response = requests.post(endpoint, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error crawling {url}: {str(e)}")
            return None
    
    def map_website(self, url: str) -> Dict:
        """Get a map of all URLs on a website"""
        endpoint = f'{self.base_url}/map'
        
        payload = {'url': url}
        
        try:
            response = requests.post(endpoint, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error mapping {url}: {str(e)}")
            return None

def extract_contact_info(text: str) -> Dict[str, Optional[str]]:
    """Extract contact information from scraped text"""
    contact_info = {
        'email': None,
        'phone': None,
        'linkedin': None
    }
    
    # Extract email
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(email_pattern, text)
    if emails:
        contact_info['email'] = emails[0]
    
    # Extract phone (US format)
    phone_pattern = r'\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b'
    phones = re.findall(phone_pattern, text)
    if phones:
        contact_info['phone'] = '-'.join(phones[0])
    
    # Extract LinkedIn URL
    linkedin_pattern = r'(?:https?://)?(?:www\.)?linkedin\.com/(?:in|company)/[\w-]+'
    linkedins = re.findall(linkedin_pattern, text, re.IGNORECASE)
    if linkedins:
        contact_info['linkedin'] = linkedins[0]
    
    return contact_info

def extract_prospects_from_content(content: str, source_url: str) -> List[Dict]:
    """Extract potential prospect information from crawled content"""
    prospects = []
    
    # Split content into sections (simple approach)
    # Look for common patterns: Name + Title + Company
    lines = content.split('\n')
    
    current_prospect = {}
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            if current_prospect and 'name' in current_prospect:
                prospects.append(current_prospect)
                current_prospect = {}
            continue
        
        # Try to identify name (capitalized words)
        name_pattern = r'^([A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)?)'
        name_match = re.match(name_pattern, line)
        if name_match and 'name' not in current_prospect:
            current_prospect['name'] = name_match.group(1)
        
        # Look for title indicators
        title_keywords = ['CEO', 'CTO', 'CFO', 'VP', 'Director', 'Manager', 'Founder', 'President', 'Head of']
        for keyword in title_keywords:
            if keyword.lower() in line.lower() and 'title' not in current_prospect:
                current_prospect['title'] = line
                break
        
        # Look for company indicators
        company_keywords = ['at ', 'Inc.', 'LLC', 'Corp', 'Ltd', 'Company']
        for keyword in company_keywords:
            if keyword in line and 'company' not in current_prospect:
                # Extract company name
                company_match = re.search(r'at\s+([A-Z][\w\s&.,]+?)(?:\s*[|•\n]|$)', line)
                if company_match:
                    current_prospect['company'] = company_match.group(1).strip()
                else:
                    current_prospect['company'] = line
                break
    
    # Add last prospect if exists
    if current_prospect and 'name' in current_prospect:
        prospects.append(current_prospect)
    
    # Add source URL and extract contact info from content
    contact_info = extract_contact_info(content)
    for prospect in prospects:
        prospect['source'] = source_url
        if contact_info['email']:
            prospect['email'] = contact_info['email']
        if contact_info['linkedin']:
            prospect['linkedin_url'] = contact_info['linkedin']
    
    return prospects

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

@app.route('/api/prospects', methods=['GET'])
def get_prospects():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM prospects')
    prospects = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': prospects})

@app.route('/api/prospects', methods=['POST'])
def add_prospect():
    data = request.json
    prospect_id = f"p_{datetime.now().timestamp()}"
    
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO prospects (id, name, company, title, email, status, deal_size, created_at, source, linkedin_url, notes)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (prospect_id, data.get('name'), data.get('company'), data.get('title'), 
               data.get('email'), data.get('status', 'lead'), data.get('deal_size', 0),
               datetime.now().isoformat(), data.get('source'), data.get('linkedin_url'), 
               data.get('notes')))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'id': prospect_id})

@app.route('/api/prospects/<prospect_id>', methods=['PUT'])
def update_prospect(prospect_id):
    data = request.json
    
    conn = get_db()
    c = conn.cursor()
    
    # Build dynamic update query based on provided fields
    update_fields = []
    values = []
    
    for field in ['status', 'name', 'company', 'title', 'email', 'deal_size', 'notes', 'linkedin_url']:
        if field in data:
            update_fields.append(f"{field} = ?")
            values.append(data[field])
    
    if update_fields:
        values.append(prospect_id)
        query = f"UPDATE prospects SET {', '.join(update_fields)} WHERE id = ?"
        c.execute(query, values)
        conn.commit()
    
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/prospects/<prospect_id>', methods=['DELETE'])
def delete_prospect(prospect_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM prospects WHERE id = ?', (prospect_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*) as total FROM prospects')
    total = c.fetchone()['total']
    
    c.execute('SELECT COUNT(*) as count FROM prospects WHERE status = ?', ('lead',))
    leads = c.fetchone()['count']
    
    c.execute('SELECT SUM(deal_size) as value FROM prospects')
    value = c.fetchone()['value'] or 0
    
    conn.close()
    
    return jsonify({'success': True, 'data': {
        'total': total,
        'leads': leads,
        'pipeline_value': value
    }})

@app.route('/api/search', methods=['POST'])
def search_prospects():
    """Search for prospects using Firecrawl web scraping"""
    try:
        data = request.json
        query = data.get('query', '')
        search_type = data.get('type', 'scrape')  # 'scrape', 'crawl', or 'map'
        url = data.get('url', '')
        
        if not url and not query:
            return jsonify({'success': False, 'error': 'Query or URL required'}), 400
        
        firecrawl = FirecrawlClient(FIRECRAWL_API_KEY)
        prospects = []
        
        if search_type == 'scrape' and url:
            # Scrape a single page
            result = firecrawl.scrape_url(url)
            if result and 'data' in result:
                content = result['data'].get('markdown', '') or result['data'].get('html', '')
                prospects = extract_prospects_from_content(content, url)
        
        elif search_type == 'crawl' and url:
            # Crawl entire website
            limit = data.get('limit', 10)
            result = firecrawl.crawl_website(url, limit=limit)
            
            if result and 'data' in result:
                for page in result['data']:
                    content = page.get('markdown', '') or page.get('html', '')
                    page_prospects = extract_prospects_from_content(content, page.get('url', url))
                    prospects.extend(page_prospects)
        
        elif search_type == 'map' and url:
            # Map the website structure
            result = firecrawl.map_website(url)
            
            if result and 'data' in result:
                # Return the URL map for the user to select specific pages
                return jsonify({
                    'success': True,
                    'type': 'map',
                    'urls': result['data'].get('links', []),
                    'message': f'Found {len(result["data"].get("links", []))} URLs'
                })
        
        # Remove duplicates based on email or name+company
        unique_prospects = []
        seen = set()
        
        for prospect in prospects:
            # Create identifier
            identifier = prospect.get('email') or f"{prospect.get('name', '')}_{prospect.get('company', '')}"
            if identifier and identifier not in seen:
                seen.add(identifier)
                unique_prospects.append(prospect)
        
        return jsonify({
            'success': True,
            'prospects': unique_prospects,
            'message': f'Found {len(unique_prospects)} unique prospects',
            'total_scraped': len(prospects)
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scrape', methods=['POST'])
def scrape_url():
    """Dedicated endpoint for scraping a specific URL"""
    try:
        data = request.json
        url = data.get('url')
        
        if not url:
            return jsonify({'success': False, 'error': 'URL required'}), 400
        
        firecrawl = FirecrawlClient(FIRECRAWL_API_KEY)
        result = firecrawl.scrape_url(url)
        
        if not result or 'data' not in result:
            return jsonify({'success': False, 'error': 'Failed to scrape URL'}), 500
        
        return jsonify({
            'success': True,
            'data': result['data']
        })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawl', methods=['POST'])
def crawl_website():
    """Dedicated endpoint for crawling a website"""
    try:
        data = request.json
        url = data.get('url')
        limit = data.get('limit', 10)
        
        if not url:
            return jsonify({'success': False, 'error': 'URL required'}), 400
        
        firecrawl = FirecrawlClient(FIRECRAWL_API_KEY)
        result = firecrawl.crawl_website(url, limit=limit)
        
        if not result:
            return jsonify({'success': False, 'error': 'Failed to crawl website'}), 500
        
        return jsonify({
            'success': True,
            'data': result
        })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
