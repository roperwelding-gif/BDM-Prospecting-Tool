from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
import os
import csv
import io
from datetime import datetime, timedelta
import requests
import re
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

DB_FILE = 'prospects.db'
FIRECRAWL_API_KEY = 'fc-186f6e0ea3cc4cc29732bb15a9a1b1d9'
FIRECRAWL_BASE_URL = 'https://api.firecrawl.dev/v1'

# ─── Database ────────────────────────────────────────────────────────────────

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
        notes TEXT,
        warmth_score INTEGER DEFAULT 20,
        last_contact_date TEXT,
        email_opens INTEGER DEFAULT 0,
        reply_count INTEGER DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        prospect_id TEXT,
        title TEXT,
        description TEXT,
        due_date TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        FOREIGN KEY (prospect_id) REFERENCES prospects(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        message TEXT,
        timestamp TEXT
    )''')

    # Migrate existing prospects table if needed (add new columns)
    try:
        c.execute("ALTER TABLE prospects ADD COLUMN warmth_score INTEGER DEFAULT 20")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE prospects ADD COLUMN last_contact_date TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE prospects ADD COLUMN email_opens INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE prospects ADD COLUMN reply_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

init_db()

# ─── Firecrawl Client ────────────────────────────────────────────────────────

class FirecrawlClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = FIRECRAWL_BASE_URL
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

    def scrape_url(self, url: str, formats: List[str] = None) -> Dict:
        if formats is None:
            formats = ['markdown', 'html']
        endpoint = f'{self.base_url}/scrape'
        payload = {'url': url, 'formats': formats}
        try:
            response = requests.post(endpoint, json=payload, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error scraping {url}: {str(e)}")
            return None

    def crawl_website(self, url: str, limit: int = 10, scrape_options: Dict = None) -> Dict:
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
        endpoint = f'{self.base_url}/map'
        payload = {'url': url}
        try:
            response = requests.post(endpoint, json=payload, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error mapping {url}: {str(e)}")
            return None

# ─── Prospect Extraction (FIXED dedup) ───────────────────────────────────────

def extract_contact_info(text: str) -> Dict[str, Optional[str]]:
    contact_info = {'email': None, 'phone': None, 'linkedin': None}

    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(email_pattern, text)
    if emails:
        personal_emails = [e for e in emails if not any(skip in e.lower() for skip in ['noreply', 'no-reply', 'support', 'info@', 'hello@'])]
        contact_info['email'] = personal_emails[0] if personal_emails else emails[0]

    phone_pattern = r'\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b'
    phones = re.findall(phone_pattern, text)
    if phones:
        contact_info['phone'] = '-'.join(phones[0])

    linkedin_pattern = r'https?://(?:www\.)?linkedin\.com/in/[\w-]+'
    linkedins = re.findall(linkedin_pattern, text, re.IGNORECASE)
    if linkedins:
        contact_info['linkedin'] = linkedins[0]
    else:
        linkedin_company_pattern = r'https?://(?:www\.)?linkedin\.com/company/[\w-]+'
        company_linkedins = re.findall(linkedin_company_pattern, text, re.IGNORECASE)
        if company_linkedins:
            contact_info['linkedin'] = company_linkedins[0]

    return contact_info

def extract_linkedin_from_text(text: str) -> Optional[str]:
    linkedin_pattern = r'https?://(?:www\.)?linkedin\.com/in/[\w-]+'
    matches = re.findall(linkedin_pattern, text, re.IGNORECASE)
    if matches:
        return matches[0]
    linkedin_company_pattern = r'https?://(?:www\.)?linkedin\.com/company/[\w-]+'
    company_matches = re.findall(linkedin_company_pattern, text, re.IGNORECASE)
    if company_matches:
        return company_matches[0]
    return None

def extract_prospects_from_content(content: str, source_url: str) -> List[Dict]:
    """
    Extract prospect information from Firecrawl markdown content.
    FIXED: Better deduplication to avoid title-only ghost entries.
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

    # Skip common false positives - expanded list
    skip_names = {
        'LinkedIn', 'Apple', 'Google', 'Facebook', 'Adobe', 'MongoDB', 'Kong',
        'FoundationDB', 'Visual', 'Sciences', 'Fire', 'Darkness', 'Ring', 'Test', 'Contact',
        'Backstory', 'Leadership', 'Careers', 'Brand', 'Fintech', 'Blockchain', 'Databases',
        'Cloud', 'Distributed', 'Reliability', 'Glossary', 'Cost', 'Outages', 'Deterministic',
        'Simulation', 'Property', 'Based', 'Autonomous', 'Testing', 'Techniques', 'Catalog',
        'Blockchains', 'Acid', 'Compliance', 'Services', 'Experience', 'Problems', 'Security',
        'Manifesto', 'Stories', 'Working', 'Antithesis', 'Primer', 'Read More', 'Learn More',
        'About Us', 'Our Team', 'Join Us', 'See All', 'View All', 'Show More',
        'Chief Executive', 'Chief Technology', 'Chief Financial', 'Chief Operating',
        'Chief Marketing', 'Chief Revenue', 'Chief Product', 'Chief Information',
        'Vice President', 'General Manager', 'Managing Director'
    }

    # Title-only patterns to reject: lines that are JUST a job title
    title_only_patterns = [
        r'^(?:Chief\s+\w+\s+Officer)$',
        r'^(?:Vice\s+President(?:\s+of\s+\w+)?)$',
        r'^(?:Director\s+of\s+\w+)$',
        r'^(?:Head\s+of\s+\w+)$',
        r'^(?:Senior|Junior|Lead|Principal)\s+(?:Engineer|Developer|Designer|Architect|Manager|Consultant)$',
    ]

    # More restrictive name pattern: exactly 2-3 capitalized words
    name_pattern = r'(?:^|\n)\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*(?:\n|$|LinkedIn|Chief|VP|Director|President|Head|Lead|Engineer|Developer|Designer)'

    seen_names = set()
    seen_titles_for_source = {}
    name_matches = re.finditer(name_pattern, content, re.MULTILINE)

    raw_prospects = []

    for match in name_matches:
        name = match.group(1).strip()
        pos = match.start()

        if name in skip_names or len(name.split()) < 2:
            continue

        # Check if this "name" is actually a job title pattern
        is_title_only = False
        for tp in title_only_patterns:
            if re.match(tp, name, re.IGNORECASE):
                is_title_only = True
                break
        if is_title_only:
            continue

        # Additional check: skip if name contains title keywords as first word
        first_word = name.split()[0]
        title_first_words = ['Chief', 'Vice', 'Senior', 'Junior', 'Lead', 'Principal',
                             'Director', 'Manager', 'Head', 'General', 'Managing']
        if first_word in title_first_words:
            continue

        context_start = max(0, pos - 400)
        context_end = min(len(content), pos + 1200)
        context = content[context_start:context_end]

        # Find title in context
        title = None
        for keyword in title_keywords:
            if keyword.lower() in context.lower():
                lines = context.split('\n')
                for line in lines:
                    line_stripped = line.strip()
                    if keyword.lower() in line_stripped.lower() and len(line_stripped) < 150:
                        # Make sure this line is a title, not another person's name
                        if line_stripped != name and not re.match(r'^[A-Z][a-z]+\s+[A-Z][a-z]+$', line_stripped):
                            title = line_stripped
                            break
                        elif line_stripped != name:
                            title = line_stripped
                            break
                if title:
                    break

        # Find company in context
        company = None
        for keyword in company_keywords:
            if keyword in context:
                idx = context.find(keyword)
                start = max(0, idx - 50)
                end = min(len(context), idx + 50)
                phrase = context[start:end]
                company_match = re.search(rf'([A-Z][a-zA-Z0-9\s&]*?{keyword})', phrase)
                if company_match:
                    company = company_match.group(1).strip()
                    if len(company) < 100:
                        break

        linkedin_url = extract_linkedin_from_text(context)

        email = None
        email_pat = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pat, context)
        if emails:
            personal_emails = [e for e in emails if not any(skip in e.lower() for skip in ['noreply', 'no-reply', 'support', 'info@', 'hello@'])]
            email = personal_emails[0] if personal_emails else emails[0]

        if (title or company) and name:
            raw_prospects.append({
                'name': name,
                'title': title,
                'company': company,
                'email': email,
                'linkedin_url': linkedin_url,
                'source': source_url
            })

    # IMPROVED DEDUPLICATION:
    # 1. First pass: deduplicate by exact name (case-insensitive)
    # 2. Second pass: if two entries share the same title, keep the one with a proper name
    final_prospects = []
    seen_names_lower = set()
    seen_titles_lower = {}

    for p in raw_prospects:
        name_lower = p['name'].lower().strip()
        title_lower = (p.get('title') or '').lower().strip()

        # Skip if we already have this exact name
        if name_lower in seen_names_lower:
            continue

        # If we already have a prospect with this exact title,
        # only keep one (prefer the one with a real name, not a title-like name)
        if title_lower and title_lower in seen_titles_lower:
            existing_idx = seen_titles_lower[title_lower]
            existing = final_prospects[existing_idx]
            # Keep the one that looks more like a real person name
            existing_looks_like_title = any(kw.lower() in existing['name'].lower() for kw in title_keywords)
            new_looks_like_title = any(kw.lower() in p['name'].lower() for kw in title_keywords)
            if existing_looks_like_title and not new_looks_like_title:
                # Replace existing with new
                final_prospects[existing_idx] = p
                seen_names_lower.discard(existing['name'].lower().strip())
                seen_names_lower.add(name_lower)
            continue

        seen_names_lower.add(name_lower)
        if title_lower:
            seen_titles_lower[title_lower] = len(final_prospects)
        final_prospects.append(p)

    return final_prospects

# ─── Warmth Score Calculation ─────────────────────────────────────────────────

def calculate_warmth_score(prospect: dict) -> int:
    status_scores = {
        'lead': 20, 'contacted': 40, 'qualified': 60,
        'proposal': 80, 'won': 100, 'lost': 5
    }
    score = status_scores.get(prospect.get('status', 'lead'), 20)

    # Decay based on last contact
    last_contact = prospect.get('last_contact_date')
    if last_contact:
        try:
            last_dt = datetime.fromisoformat(last_contact)
            days_since = (datetime.now() - last_dt).days
            weeks_since = days_since // 7
            score -= weeks_since * 5
        except (ValueError, TypeError):
            pass

    # Boost from engagement
    email_opens = prospect.get('email_opens', 0) or 0
    reply_count = prospect.get('reply_count', 0) or 0
    score += email_opens * 10
    score += reply_count * 20

    return max(0, min(100, score))

# ─── Static File Serving ──────────────────────────────────────────────────────

@app.route('/logo.jpg')
def serve_logo():
    return send_from_directory('.', 'logo.jpg')

@app.route('/')
def serve_index():
    return render_template('index.html')

# ─── Health Check ─────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

# ─── Prospect CRUD ────────────────────────────────────────────────────────────

@app.route('/api/prospects', methods=['GET'])
def get_prospects():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM prospects')
    prospects = []
    for row in c.fetchall():
        p = dict(row)
        p['warmth_score'] = calculate_warmth_score(p)
        prospects.append(p)
    conn.close()
    return jsonify({'success': True, 'data': prospects})

@app.route('/api/prospects', methods=['POST'])
def add_prospect():
    data = request.json
    prospect_id = f"p_{datetime.now().timestamp()}"
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO prospects (id, name, company, title, email, status, deal_size, created_at, source, linkedin_url, notes, warmth_score, last_contact_date, email_opens, reply_count)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (prospect_id, data.get('name'), data.get('company'), data.get('title'),
               data.get('email'), data.get('status', 'lead'), data.get('deal_size', 0),
               datetime.now().isoformat(), data.get('source'), data.get('linkedin_url'),
               data.get('notes'), 20, None, 0, 0))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'id': prospect_id})

@app.route('/api/prospects/<prospect_id>', methods=['PUT'])
def update_prospect(prospect_id):
    data = request.json
    conn = get_db()
    c = conn.cursor()
    update_fields = []
    values = []
    for field in ['status', 'name', 'company', 'title', 'email', 'deal_size', 'notes',
                  'linkedin_url', 'warmth_score', 'last_contact_date', 'email_opens', 'reply_count']:
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
    c.execute('DELETE FROM tasks WHERE prospect_id = ?', (prospect_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ─── Stats ────────────────────────────────────────────────────────────────────

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
    c.execute('SELECT COUNT(*) as count FROM prospects WHERE status = ?', ('won',))
    won = c.fetchone()['count']
    c.execute('SELECT COUNT(*) as count FROM tasks WHERE status = ? AND due_date <= ?',
              ('pending', datetime.now().isoformat()))
    overdue_tasks = c.fetchone()['count']
    conn.close()
    return jsonify({'success': True, 'data': {
        'total': total, 'leads': leads, 'pipeline_value': value,
        'won': won, 'overdue_tasks': overdue_tasks
    }})

# ─── Search / Scrape / Crawl ─────────────────────────────────────────────────

@app.route('/api/search', methods=['POST'])
def search_prospects():
    try:
        data = request.json
        query = data.get('query', '')
        search_type = data.get('type', 'scrape')
        url = data.get('url', '')

        if not url and not query:
            return jsonify({'success': False, 'error': 'Query or URL required'}), 400

        firecrawl = FirecrawlClient(FIRECRAWL_API_KEY)
        prospects = []
        pages_crawled = []

        if search_type == 'scrape' and url:
            result = firecrawl.scrape_url(url)
            if result and 'data' in result:
                content = result['data'].get('markdown', '') or result['data'].get('html', '')
                page_prospects = extract_prospects_from_content(content, url)
                prospects.extend(page_prospects)
                pages_crawled.append({
                    'url': url,
                    'prospect_count': len(page_prospects)
                })

        elif search_type == 'crawl' and url:
            limit = data.get('limit', 10)
            result = firecrawl.crawl_website(url, limit=limit)
            if result and 'data' in result:
                for page in result['data']:
                    content = page.get('markdown', '') or page.get('html', '')
                    page_url = page.get('url', url)
                    page_prospects = extract_prospects_from_content(content, page_url)
                    prospects.extend(page_prospects)
                    pages_crawled.append({
                        'url': page_url,
                        'prospect_count': len(page_prospects)
                    })
            else:
                return jsonify({'success': False, 'error': 'Failed to crawl website.'}), 400

        elif search_type == 'map' and url:
            result = firecrawl.map_website(url)
            if result and 'data' in result:
                return jsonify({
                    'success': True, 'type': 'map',
                    'urls': result['data'].get('links', []),
                    'message': f'Found {len(result["data"].get("links", []))} URLs'
                })

        # Final deduplication across all pages
        unique_prospects = []
        seen = set()
        for prospect in prospects:
            identifier = prospect.get('email')
            if not identifier:
                identifier = f"{prospect.get('name', '')}_{prospect.get('company', '')}"
            if identifier and identifier not in seen:
                seen.add(identifier)
                unique_prospects.append(prospect)

        return jsonify({
            'success': True, 'prospects': unique_prospects,
            'pages': pages_crawled,
            'message': f'Found {len(unique_prospects)} unique prospects from {len(pages_crawled)} pages',
            'total_scraped': len(prospects)
        })

    except Exception as e:
        print(f"Search error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scrape', methods=['POST'])
def scrape_url():
    try:
        data = request.json
        url = data.get('url')
        if not url:
            return jsonify({'success': False, 'error': 'URL required'}), 400
        firecrawl = FirecrawlClient(FIRECRAWL_API_KEY)
        result = firecrawl.scrape_url(url)
        if not result or 'data' not in result:
            return jsonify({'success': False, 'error': 'Failed to scrape URL'}), 500
        content = result['data'].get('markdown', '') or result['data'].get('html', '')
        prospects = extract_prospects_from_content(content, url)
        return jsonify({
            'success': True, 'data': result['data'],
            'prospects': prospects, 'prospect_count': len(prospects)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawl', methods=['POST'])
def crawl_website():
    try:
        data = request.json
        url = data.get('url')
        limit = data.get('limit', 10)
        if not url:
            return jsonify({'success': False, 'error': 'URL required'}), 400
        firecrawl = FirecrawlClient(FIRECRAWL_API_KEY)
        result = firecrawl.crawl_website(url, limit=limit)
        if not result:
            return jsonify({'success': False, 'error': 'Failed to crawl website.'}), 500
        all_prospects = []
        pages = []
        if 'data' in result:
            for page in result['data']:
                content = page.get('markdown', '') or page.get('html', '')
                page_url = page.get('url', url)
                page_prospects = extract_prospects_from_content(content, page_url)
                all_prospects.extend(page_prospects)
                pages.append({
                    'url': page_url, 'prospect_count': len(page_prospects),
                    'content_length': len(content)
                })
        unique_prospects = []
        seen = set()
        for prospect in all_prospects:
            identifier = prospect.get('email') or f"{prospect.get('name')}_{prospect.get('company')}"
            if identifier not in seen:
                seen.add(identifier)
                unique_prospects.append(prospect)
        return jsonify({
            'success': True, 'prospects': unique_prospects,
            'pages_crawled': len(pages), 'page_details': pages,
            'message': f'Found {len(unique_prospects)} unique prospects across {len(pages)} pages'
        })
    except Exception as e:
        print(f"Crawl error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ─── CSV Import/Export ────────────────────────────────────────────────────────

@app.route('/api/import-csv', methods=['POST'])
def import_csv():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        file = request.files['file']
        if not file.filename.endswith('.csv'):
            return jsonify({'success': False, 'error': 'File must be CSV'}), 400

        stream = io.StringIO(file.stream.read().decode('utf-8'))
        reader = csv.DictReader(stream)

        conn = get_db()
        c = conn.cursor()
        imported = 0
        errors = 0

        for row in reader:
            try:
                prospect_id = f"p_{datetime.now().timestamp()}_{imported}"
                c.execute('''INSERT INTO prospects (id, name, company, title, email, status, deal_size, created_at, source, linkedin_url, notes, warmth_score)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (prospect_id,
                           row.get('name', row.get('Name', '')),
                           row.get('company', row.get('Company', '')),
                           row.get('title', row.get('Title', '')),
                           row.get('email', row.get('Email', '')),
                           row.get('status', row.get('Status', 'lead')),
                           float(row.get('deal_size', row.get('Deal Size', 0)) or 0),
                           datetime.now().isoformat(),
                           row.get('source', row.get('Source', '')),
                           row.get('linkedin_url', row.get('LinkedIn', '')),
                           row.get('notes', row.get('Notes', '')),
                           20))
                imported += 1
            except Exception as e:
                errors += 1
                print(f"CSV row error: {e}")

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'imported': imported, 'errors': errors})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/export-csv', methods=['GET'])
def export_csv():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM prospects')
    prospects = [dict(row) for row in c.fetchall()]
    conn.close()

    output = io.StringIO()
    if prospects:
        writer = csv.DictWriter(output, fieldnames=prospects[0].keys())
        writer.writeheader()
        writer.writerows(prospects)

    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=prospects_export.csv'}
    )

# ─── Tasks / Reminders ───────────────────────────────────────────────────────

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    prospect_id = request.args.get('prospect_id')
    conn = get_db()
    c = conn.cursor()
    if prospect_id:
        c.execute('SELECT * FROM tasks WHERE prospect_id = ? ORDER BY due_date ASC', (prospect_id,))
    else:
        c.execute('SELECT * FROM tasks ORDER BY due_date ASC')
    tasks = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': tasks})

@app.route('/api/tasks', methods=['POST'])
def add_task():
    data = request.json
    task_id = f"t_{datetime.now().timestamp()}"
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO tasks (id, prospect_id, title, description, due_date, status, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (task_id, data.get('prospect_id'), data.get('title'),
               data.get('description', ''), data.get('due_date'),
               'pending', datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'id': task_id})

@app.route('/api/tasks/<task_id>', methods=['PUT'])
def update_task(task_id):
    data = request.json
    conn = get_db()
    c = conn.cursor()
    update_fields = []
    values = []
    for field in ['title', 'description', 'due_date', 'status']:
        if field in data:
            update_fields.append(f"{field} = ?")
            values.append(data[field])
    if update_fields:
        values.append(task_id)
        query = f"UPDATE tasks SET {', '.join(update_fields)} WHERE id = ?"
        c.execute(query, values)
        conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ─── AI Icebreaker ────────────────────────────────────────────────────────────

@app.route('/api/icebreaker', methods=['POST'])
def generate_icebreaker():
    try:
        data = request.json
        prospect_name = data.get('name', '')
        prospect_company = data.get('company', '')
        prospect_title = data.get('title', '')
        source_url = data.get('source', '')

        # Try to scrape the source URL for recent content
        content_snippet = ''
        if source_url:
            firecrawl = FirecrawlClient(FIRECRAWL_API_KEY)
            result = firecrawl.scrape_url(source_url, formats=['markdown'])
            if result and 'data' in result:
                raw_content = result['data'].get('markdown', '')
                # Extract interesting snippets - look for news, blog, recent mentions
                sentences = re.split(r'[.!?]\s', raw_content[:3000])
                interesting = []
                interest_keywords = ['launched', 'announced', 'raised', 'expanded', 'hired',
                                     'partnership', 'award', 'recognition', 'growth', 'innovation',
                                     'new', 'latest', 'recently', 'proud', 'excited', 'milestone']
                for s in sentences:
                    if any(kw in s.lower() for kw in interest_keywords) and len(s) > 30:
                        interesting.append(s.strip())
                if interesting:
                    content_snippet = interesting[0]

        # Generate icebreaker based on available info
        icebreakers = []

        if content_snippet:
            # Content-based icebreaker
            icebreakers.append(
                f"I noticed {prospect_company} recently {content_snippet[:100].lower()}... "
                f"As {prospect_title}, you must be playing a key role in that. I'd love to connect."
            )

        if prospect_title and prospect_company:
            icebreakers.append(
                f"Hi {prospect_name.split()[0] if prospect_name else 'there'}, "
                f"I've been following {prospect_company}'s work and your role as {prospect_title} "
                f"caught my attention. Would love to explore how we might collaborate."
            )

        if prospect_company:
            icebreakers.append(
                f"Hey {prospect_name.split()[0] if prospect_name else 'there'}, "
                f"I came across {prospect_company} while researching industry leaders in your space. "
                f"Your team's approach stood out - would you be open to a brief conversation?"
            )

        # Default fallback
        if not icebreakers:
            icebreakers.append(
                f"Hi {prospect_name.split()[0] if prospect_name else 'there'}, "
                f"I'd love to connect and learn more about what you're working on. "
                f"Do you have a few minutes for a quick chat this week?"
            )

        return jsonify({
            'success': True,
            'icebreakers': icebreakers,
            'source_context': content_snippet[:200] if content_snippet else None
        })

    except Exception as e:
        print(f"Icebreaker error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ─── Chat Messages (REST fallback) ───────────────────────────────────────────

@app.route('/api/chat/messages', methods=['GET'])
def get_chat_messages():
    limit = request.args.get('limit', 50, type=int)
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM chat_messages ORDER BY timestamp DESC LIMIT ?', (limit,))
    messages = [dict(row) for row in c.fetchall()]
    conn.close()
    messages.reverse()
    return jsonify({'success': True, 'data': messages})

@app.route('/api/chat/messages', methods=['POST'])
def post_chat_message():
    data = request.json
    conn = get_db()
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    c.execute('INSERT INTO chat_messages (username, message, timestamp) VALUES (?, ?, ?)',
              (data.get('username', 'Anonymous'), data.get('message', ''), timestamp))
    conn.commit()
    msg_id = c.lastrowid
    conn.close()

    msg = {
        'id': msg_id,
        'username': data.get('username', 'Anonymous'),
        'message': data.get('message', ''),
        'timestamp': timestamp
    }
    # Broadcast via SocketIO
    socketio.emit('new_message', msg, namespace='/chat')
    return jsonify({'success': True, 'data': msg})

# ─── SocketIO Events ─────────────────────────────────────────────────────────

online_users = {}

@socketio.on('connect', namespace='/chat')
def handle_connect():
    print(f'Client connected: {request.sid}')

@socketio.on('disconnect', namespace='/chat')
def handle_disconnect():
    if request.sid in online_users:
        username = online_users.pop(request.sid)
        emit('user_left', {'username': username, 'online_users': list(online_users.values())},
             namespace='/chat', broadcast=True)
    print(f'Client disconnected: {request.sid}')

@socketio.on('set_username', namespace='/chat')
def handle_set_username(data):
    username = data.get('username', 'Anonymous')
    online_users[request.sid] = username
    emit('user_joined', {'username': username, 'online_users': list(online_users.values())},
         namespace='/chat', broadcast=True)

@socketio.on('send_message', namespace='/chat')
def handle_send_message(data):
    username = online_users.get(request.sid, data.get('username', 'Anonymous'))
    message = data.get('message', '')
    timestamp = datetime.now().isoformat()

    # Save to DB
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO chat_messages (username, message, timestamp) VALUES (?, ?, ?)',
              (username, message, timestamp))
    conn.commit()
    msg_id = c.lastrowid
    conn.close()

    emit('new_message', {
        'id': msg_id, 'username': username,
        'message': message, 'timestamp': timestamp
    }, namespace='/chat', broadcast=True)

# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)
