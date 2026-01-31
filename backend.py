from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime
import requests
import re
from typing import List, Dict, Optional
import asyncio
from urllib.parse import urljoin, urlparse

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
            response = requests.post(endpoint, json=payload, headers=self.headers, timeout=30)
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
            response = requests.post(endpoint, json=payload, headers=self.headers, timeout=60)
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
            response = requests.post(endpoint, json=payload, headers=self.headers, timeout=30)
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
    
    # Extract email - be more strict
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(email_pattern, text)
    if emails:
        # Filter out common non-personal emails
        personal_emails = [e for e in emails if not any(skip in e.lower() for skip in ['noreply', 'no-reply', 'support', 'info@', 'hello@'])]
        contact_info['email'] = personal_emails[0] if personal_emails else emails[0]
    
    # Extract phone (US format)
    phone_pattern = r'\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b'
    phones = re.findall(phone_pattern, text)
    if phones:
        contact_info['phone'] = '-'.join(phones[0])
    
    # Extract LinkedIn URL - look for personal profiles first
    linkedin_pattern = r'https?://(?:www\.)?linkedin\.com/in/[\w-]+'
    linkedins = re.findall(linkedin_pattern, text, re.IGNORECASE)
    if linkedins:
        contact_info['linkedin'] = linkedins[0]
    else:
        # Fallback to company profiles
        linkedin_company_pattern = r'https?://(?:www\.)?linkedin\.com/company/[\w-]+'
        company_linkedins = re.findall(linkedin_company_pattern, text, re.IGNORECASE)
        if company_linkedins:
            contact_info['linkedin'] = company_linkedins[0]
    
    return contact_info

def extract_linkedin_from_text(text: str) -> Optional[str]:
    """Extract LinkedIn profile URL from specific text block"""
    # Look for personal LinkedIn profiles
    linkedin_pattern = r'https?://(?:www\.)?linkedin\.com/in/[\w-]+'
    matches = re.findall(linkedin_pattern, text, re.IGNORECASE)
    if matches:
        return matches[0]
    
    # Fallback to company
    linkedin_company_pattern = r'https?://(?:www\.)?linkedin\.com/company/[\w-]+'
    company_matches = re.findall(linkedin_company_pattern, text, re.IGNORECASE)
    if company_matches:
        return company_matches[0]
    
    return None

def extract_prospects_from_content(content: str, source_url: str) -> List[Dict]:
    """
    Extract prospect information from Firecrawl markdown content.
    Uses smarter name detection to avoid false positives.
    """
    prospects = []
    
    if not content or len(content.strip()) < 20:
        return prospects
    
    # Clean markdown artifacts but PRESERVE LinkedIn URLs
    content = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', content)
    content = re.sub(r'\*\*([^*]+)\*\*', r'\1', content)
    content = re.sub(r'\*([^*]+)\*', r'\1', content)
    content = re.sub(r'__([^_]+)__', r'\1', content)
    content = re.sub(r'_([^_]+)_', r'\1', content)
    
    title_keywords = [
        'CEO', 'CTO', 'CFO', 'COO', 'CMO', 'VP', 'Director', 'Manager', 
        'Head of', 'Lead', 'Engineer', 'Developer', 'Designer', 'Founder',
        'President', 'Chief', 'Officer', 'Executive', 'Consultant',
        'Architect', 'Principal', 'Senior', 'Junior', 'Associate', 'Co-founder',
        'Controller'
    ]
    
    company_keywords = ['Inc', 'Corp', 'LLC', 'Ltd', 'Company', 'Co.', 'Technologies', 'Solutions', 'Services']
    
    # More restrictive name pattern: exactly 2-3 words, each 2+ letters, in sentence context
    # Must be preceded by newline/start and followed by newline/title/LinkedIn
    name_pattern = r'(?:^|\n)\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*(?:\n|$|LinkedIn|Chief|VP|Director|President|Head|Lead|Engineer|Developer|Designer)'
    
    seen_prospects = set()
    name_matches = re.finditer(name_pattern, content, re.MULTILINE)
    
    for match in name_matches:
        name = match.group(1).strip()
        pos = match.start()
        
        # Skip common false positives
        skip_names = ['LinkedIn', 'Apple', 'Google', 'Facebook', 'Adobe', 'E&Y', 'CMU', 'MongoDB', 'Kong', 
                      'FoundationDB', 'Visual', 'Sciences', 'Fire', 'Darkness', 'Ring', 'Test', 'Contact',
                      'Backstory', 'Leadership', 'Careers', 'Brand', 'Fintech', 'Blockchain', 'Databases',
                      'Cloud', 'Distributed', 'Reliability', 'Glossary', 'Cost', 'Outages', 'Deterministic',
                      'Simulation', 'Property', 'Based', 'Autonomous', 'Testing', 'Techniques', 'Catalog',
                      'Blockchains', 'Acid', 'Compliance', 'Services', 'Experience', 'Problems', 'Security',
                      'Manifesto', 'Stories', 'Working', 'Antithesis', 'Primer', 'Catalog']
        
        if name in skip_names or len(name.split()) < 2:
            continue
        
        # Get context window: 400 chars before and 1200 chars after
        context_start = max(0, pos - 400)
        context_end = min(len(content), pos + 1200)
        context = content[context_start:context_end]
        
        # Find title in context
        title = None
        for keyword in title_keywords:
            if keyword.lower() in context.lower():
                # Find the line with the keyword
                lines = context.split('\n')
                for line in lines:
                    if keyword.lower() in line.lower() and len(line) < 150:
                        title = line.strip()
                        break
                if title:
                    break
        
        # Find company in context - look for keywords specifically
        company = None
        for keyword in company_keywords:
            if keyword in context:
                # Find company phrase
                idx = context.find(keyword)
                start = max(0, idx - 50)
                end = min(len(context), idx + 50)
                phrase = context[start:end]
                
                # Extract clean company name
                company_match = re.search(rf'([A-Z][a-zA-Z0-9\s&]*?{keyword})', phrase)
                if company_match:
                    company = company_match.group(1).strip()
                    if len(company) < 100:
                        break
        
        # Find LinkedIn
        linkedin_url = extract_linkedin_from_text(context)
        
        # Find email
        email = None
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, context)
        if emails:
            personal_emails = [e for e in emails if not any(skip in e.lower() for skip in ['noreply', 'no-reply', 'support', 'info@', 'hello@'])]
            email = personal_emails[0] if personal_emails else emails[0]
        
        # Only add if we have title or company
        if (title or company) and name:
            prospect = {
                'name': name,
                'title': title,
                'company': company,
                'email': email,
                'linkedin_url': linkedin_url,
                'source': source_url
            }
            
            # Deduplicate
            prospect_key = f"{prospect['name']}_{prospect.get('title', '')}_{prospect.get('company', '')}"
            if prospect_key not in seen_prospects:
                seen_prospects.add(prospect_key)
                prospects.append(prospect)
    
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
        pages_crawled = []
        
        if search_type == 'scrape' and url:
            # Scrape a single page
            result = firecrawl.scrape_url(url)
            if result and 'data' in result:
                content = result['data'].get('markdown', '') or result['data'].get('html', '')
                page_prospects = extract_prospects_from_content(content, url)
                prospects.extend(page_prospects)
                
                # Store raw content for display
                pages_crawled.append({
                    'url': url,
                    'content': content[:5000],  # Truncate for display
                    'prospect_count': len(page_prospects)
                })
        
        elif search_type == 'crawl' and url:
            # Crawl entire website with improved error handling
            limit = data.get('limit', 10)
            result = firecrawl.crawl_website(url, limit=limit)
            
            if result and 'data' in result:
                for page in result['data']:
                    content = page.get('markdown', '') or page.get('html', '')
                    page_url = page.get('url', url)
                    page_prospects = extract_prospects_from_content(content, page_url)
                    prospects.extend(page_prospects)
                    
                    # Store page info
                    pages_crawled.append({
                        'url': page_url,
                        'content': content[:3000],
                        'prospect_count': len(page_prospects)
                    })
            else:
                return jsonify({
                    'success': False, 
                    'error': 'Failed to crawl website. Check the URL and try again.'
                }), 400
        
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
            # Create identifier - prefer email
            identifier = prospect.get('email')
            if not identifier:
                identifier = f"{prospect.get('name', '')}_{prospect.get('company', '')}"
            
            if identifier and identifier not in seen:
                seen.add(identifier)
                unique_prospects.append(prospect)
        
        return jsonify({
            'success': True,
            'prospects': unique_prospects,
            'pages': pages_crawled,
            'message': f'Found {len(unique_prospects)} unique prospects from {len(pages_crawled)} pages',
            'total_scraped': len(prospects)
        })

    except Exception as e:
        print(f"Search error: {str(e)}")
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
        
        # Extract prospects from the scraped content
        content = result['data'].get('markdown', '') or result['data'].get('html', '')
        prospects = extract_prospects_from_content(content, url)
        
        return jsonify({
            'success': True,
            'data': result['data'],
            'prospects': prospects,
            'prospect_count': len(prospects)
        })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawl', methods=['POST'])
def crawl_website():
    """Dedicated endpoint for crawling a website with better error handling"""
    try:
        data = request.json
        url = data.get('url')
        limit = data.get('limit', 10)
        
        if not url:
            return jsonify({'success': False, 'error': 'URL required'}), 400
        
        firecrawl = FirecrawlClient(FIRECRAWL_API_KEY)
        result = firecrawl.crawl_website(url, limit=limit)
        
        if not result:
            return jsonify({
                'success': False, 
                'error': 'Failed to crawl website. The API may be rate limited or the URL may be invalid.'
            }), 500
        
        # Extract prospects from all pages
        all_prospects = []
        pages = []
        
        if 'data' in result:
            for page in result['data']:
                content = page.get('markdown', '') or page.get('html', '')
                page_url = page.get('url', url)
                page_prospects = extract_prospects_from_content(content, page_url)
                all_prospects.extend(page_prospects)
                
                pages.append({
                    'url': page_url,
                    'prospect_count': len(page_prospects),
                    'content_length': len(content)
                })
        
        # Deduplicate
        unique_prospects = []
        seen = set()
        for prospect in all_prospects:
            identifier = prospect.get('email') or f"{prospect.get('name')}_{prospect.get('company')}"
            if identifier not in seen:
                seen.add(identifier)
                unique_prospects.append(prospect)
        
        return jsonify({
            'success': True,
            'prospects': unique_prospects,
            'pages_crawled': len(pages),
            'page_details': pages,
            'message': f'Found {len(unique_prospects)} unique prospects across {len(pages)} pages'
        })
    
    except Exception as e:
        print(f"Crawl error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)