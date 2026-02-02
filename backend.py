from flask import Flask, request, jsonify, send_from_directory, render_template, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
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
app.secret_key = os.environ.get('SECRET_KEY', 'originOS-bdm-secret-key-change-in-prod-2024')
CORS(app, supports_credentials=True)
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
        phone TEXT,
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
    # Add phone column if upgrading from older schema
    try:
        c.execute('ALTER TABLE prospects ADD COLUMN phone TEXT')
    except:
        pass  # Column already exists

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

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        display_name TEXT,
        avatar TEXT DEFAULT 'avatar-default',
        signature TEXT DEFAULT '',
        created_at TEXT,
        last_active TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS forum_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS forum_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT,
        FOREIGN KEY (post_id) REFERENCES forum_posts(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')

    # The Sauce - Signal-based trigger alerts (cached daily)
    c.execute('''CREATE TABLE IF NOT EXISTS sauce_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_type TEXT,
        company TEXT,
        headline TEXT,
        summary TEXT,
        source_url TEXT,
        trigger_keywords TEXT,
        created_at TEXT,
        date_key TEXT
    )''')

    # Questing Engine - XP tracking
    c.execute('''CREATE TABLE IF NOT EXISTS xp_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        xp_earned INTEGER,
        detail TEXT,
        created_at TEXT
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

# ─── Auth Helpers ─────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    if 'user_id' not in session:
        return None
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, username, email, display_name, avatar, signature FROM users WHERE id = ?', (session['user_id'],))
    user = c.fetchone()
    conn.close()
    return dict(user) if user else None

AVATAR_OPTIONS = [
    'avatar-default', 'avatar-hacker', 'avatar-ghost', 'avatar-skull',
    'avatar-robot', 'avatar-alien', 'avatar-ninja', 'avatar-wizard',
    'avatar-dragon', 'avatar-phoenix', 'avatar-wolf', 'avatar-eagle'
]

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
        """Firecrawl v1 crawl is async - submit job, then poll for results."""
        import time
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
            # Step 1: Submit the crawl job
            response = requests.post(endpoint, json=payload, headers=self.headers, timeout=30)
            response.raise_for_status()
            job_data = response.json()
            print(f"Crawl job response: {job_data}")

            # If the API returned data directly (v0 style), return as-is
            if 'data' in job_data and isinstance(job_data['data'], list):
                return job_data

            # Step 2: Poll for results using the job ID
            job_id = job_data.get('id') or job_data.get('jobId')
            if not job_id:
                # Try checking if success=true with a different structure
                if job_data.get('success') and 'url' in job_data:
                    # v1 returns a status check URL
                    check_url = job_data.get('url', f'{self.base_url}/crawl/{job_id}')
                else:
                    print(f"No job ID in crawl response: {job_data}")
                    return None

            check_url = f'{self.base_url}/crawl/{job_id}'
            max_attempts = 30  # Poll for up to ~60 seconds
            for attempt in range(max_attempts):
                time.sleep(2)
                status_response = requests.get(check_url, headers=self.headers, timeout=15)
                status_response.raise_for_status()
                status_data = status_response.json()
                print(f"Crawl poll attempt {attempt + 1}: status={status_data.get('status')}")

                status = status_data.get('status', '')
                if status == 'completed':
                    return status_data
                elif status == 'failed':
                    print(f"Crawl job failed: {status_data}")
                    return None
                # Otherwise still 'scraping' / 'processing', keep polling

            print("Crawl job timed out after polling")
            return None

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
    Uses tight context windows to avoid mixing LinkedIn/title between contacts.
    """
    prospects = []

    if not content or len(content.strip()) < 20:
        return prospects

    # Clean markdown artifacts but PRESERVE LinkedIn URLs
    # Convert markdown links to plain text + url: [text](url) -> text url
    content = re.sub(r'\[([^\]]*)\]\((https?://(?:www\.)?linkedin\.com/[^)]+)\)', r'\1 \2', content)
    content = re.sub(r'\[([^\]]*)\]\([^)]+\)', r'\1', content)
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
        'Controller', 'Partner', 'Analyst', 'Coordinator', 'Specialist',
        'Recruiter', 'Advisor', 'Strategist'
    ]

    company_keywords = ['Inc', 'Corp', 'LLC', 'Ltd', 'Company', 'Co.', 'Technologies', 'Solutions', 'Services']

    # Skip common false positives
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
        'Vice President', 'General Manager', 'Managing Director', 'Privacy Policy',
        'Terms Of', 'All Rights', 'Follow Us', 'Get Started', 'Sign Up', 'Log In'
    }

    # Title-only patterns to reject
    title_only_patterns = [
        r'^(?:Chief\s+\w+\s+Officer)$',
        r'^(?:Vice\s+President(?:\s+of\s+\w+)?)$',
        r'^(?:Director\s+of\s+\w+)$',
        r'^(?:Head\s+of\s+\w+)$',
        r'^(?:Senior|Junior|Lead|Principal)\s+(?:Engineer|Developer|Designer|Architect|Manager|Consultant)$',
    ]

    # Name pattern: 2-3 capitalized words separated by single spaces, at start of line.
    # Uses [^\S\n]* for leading indent (no newline), single space between name words.
    # Downstream filters (skip_names, title_only_patterns, title_first_words,
    # and the title-or-company gate) handle false positives.
    name_pattern = r'(?:^|\n)[^\S\n]*([A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)?)\b'

    title_first_words = ['Chief', 'Vice', 'Senior', 'Junior', 'Lead', 'Principal',
                         'Director', 'Manager', 'Head', 'General', 'Managing']

    # First, find ALL name matches and pre-filter to identify real person names
    all_matches = list(re.finditer(name_pattern, content, re.MULTILINE))

    def is_real_person_name(name_str):
        """Check if a matched string looks like a real person name (not a title or nav text)."""
        if name_str in skip_names or len(name_str.split()) < 2:
            return False
        # Also check if ANY individual word is a known skip word
        for word in name_str.split():
            if word in skip_names:
                return False
        for tp in title_only_patterns:
            if re.match(tp, name_str, re.IGNORECASE):
                return False
        if name_str.split()[0] in title_first_words:
            return False
        return True

    # Build filtered list of real person name matches and their positions
    person_matches = []
    person_positions = []
    for match in all_matches:
        name = match.group(1).strip()
        if is_real_person_name(name):
            person_matches.append(match)
            person_positions.append(match.start())

    raw_prospects = []

    for i, match in enumerate(person_matches):
        name = match.group(1).strip()
        pos = match.start()

        # CONTEXT: from this person's name to the NEXT person's name (or 600 chars max)
        # Only real person names act as boundaries, not title lines like "Chief Executive Officer"
        if i + 1 < len(person_positions):
            next_person_pos = person_positions[i + 1]
            context_end = min(next_person_pos, pos + 600)
        else:
            context_end = min(len(content), pos + 600)

        # Small lookback for company info that may appear just before the name
        lookback_start = max(0, pos - 100)
        context = content[lookback_start:context_end]
        # The name-relative portion (after the name) for title/linkedin/email
        after_name = content[pos:context_end]

        # Find title: look in the lines immediately following the name
        title = None
        after_lines = after_name.split('\n')
        # Check the first several lines after the name (title may be a few lines down)
        for line in after_lines[:8]:
            line_stripped = line.strip()
            if not line_stripped or line_stripped == name:
                continue
            # Skip lines that are just LinkedIn text or URLs
            if line_stripped.startswith('LinkedIn') or line_stripped.startswith('http'):
                continue
            for keyword in title_keywords:
                if keyword.lower() in line_stripped.lower() and len(line_stripped) < 150:
                    # Make sure this is a title line, not another person's name
                    if not re.match(r'^[A-Z][a-z]+ [A-Z][a-z]+$', line_stripped):
                        title = line_stripped
                        break
            if title:
                break

        # Find company: check context around the name
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

        # LinkedIn: only look in the tight window after the name
        linkedin_url = extract_linkedin_from_text(after_name)

        # Email: only look in the tight window after the name
        email = None
        email_pat = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pat, after_name)
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

    # DEDUPLICATION:
    final_prospects = []
    seen_names_lower = set()
    seen_titles_lower = {}

    for p in raw_prospects:
        name_lower = p['name'].lower().strip()
        title_lower = (p.get('title') or '').lower().strip()

        if name_lower in seen_names_lower:
            continue

        if title_lower and title_lower in seen_titles_lower:
            existing_idx = seen_titles_lower[title_lower]
            existing = final_prospects[existing_idx]
            existing_looks_like_title = any(kw.lower() in existing['name'].lower() for kw in title_keywords)
            new_looks_like_title = any(kw.lower() in p['name'].lower() for kw in title_keywords)
            if existing_looks_like_title and not new_looks_like_title:
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
    c.execute('''INSERT INTO prospects (id, name, company, title, email, phone, status, deal_size, created_at, source, linkedin_url, notes, warmth_score, last_contact_date, email_opens, reply_count)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (prospect_id, data.get('name'), data.get('company'), data.get('title'),
               data.get('email'), data.get('phone'), data.get('status', 'lead'), data.get('deal_size', 0),
               datetime.now().isoformat(), data.get('source'), data.get('linkedin_url'),
               data.get('notes'), 20, None, 0, 0))
    conn.commit()
    conn.close()
    award_xp('prospect_added', data.get('name', ''))
    return jsonify({'success': True, 'id': prospect_id})

@app.route('/api/prospects/<prospect_id>', methods=['PUT'])
def update_prospect(prospect_id):
    data = request.json
    conn = get_db()
    c = conn.cursor()

    # Check old status for XP on status changes
    old_status = None
    if 'status' in data:
        c.execute('SELECT status FROM prospects WHERE id = ?', (prospect_id,))
        row = c.fetchone()
        if row:
            old_status = row['status']

    update_fields = []
    values = []
    for field in ['status', 'name', 'company', 'title', 'email', 'phone', 'deal_size', 'notes',
                  'linkedin_url', 'warmth_score', 'last_contact_date', 'email_opens', 'reply_count']:
        if field in data:
            update_fields.append(f"{field} = ?")
            values.append(data[field])
    if update_fields:
        values.append(prospect_id)
        query = f"UPDATE prospects SET {', '.join(update_fields)} WHERE id = ?"
        c.execute(query, values)
        conn.commit()

    # Award XP for status progressions
    if old_status and 'status' in data and old_status != data['status']:
        new_status = data['status']
        if new_status == 'contacted':
            award_xp('status_lead_to_contacted', f'{prospect_id}')
        elif new_status == 'qualified':
            award_xp('status_to_qualified', f'{prospect_id}')
        elif new_status == 'proposal':
            award_xp('status_to_proposal', f'{prospect_id}')
        elif new_status == 'won':
            award_xp('status_to_won', f'{prospect_id}')

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

        award_xp('scrape_ran', url)

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
    award_xp('task_added', data.get('title', ''))
    return jsonify({'success': True, 'id': task_id})

@app.route('/api/tasks/<task_id>', methods=['PUT'])
def update_task(task_id):
    data = request.json
    conn = get_db()
    c = conn.cursor()

    # Check if completing a task for XP
    if data.get('status') == 'completed':
        c.execute('SELECT status FROM tasks WHERE id = ?', (task_id,))
        old = c.fetchone()
        if old and old['status'] != 'completed':
            award_xp('task_completed', task_id)

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

# ─── Auth Endpoints ──────────────────────────────────────────────────────────

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    username = (data.get('username', '') or '').strip()
    email = (data.get('email', '') or '').strip().lower()
    password = data.get('password', '')
    display_name = (data.get('display_name', '') or username).strip()
    avatar = data.get('avatar', 'avatar-default')
    signature = (data.get('signature', '') or '').strip()

    if not username or not email or not password:
        return jsonify({'success': False, 'error': 'Username, email, and password are required'}), 400
    if len(password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400
    if len(username) < 3 or len(username) > 30:
        return jsonify({'success': False, 'error': 'Username must be 3-30 characters'}), 400
    if avatar not in AVATAR_OPTIONS:
        avatar = 'avatar-default'
    if len(signature) > 200:
        signature = signature[:200]

    conn = get_db()
    c = conn.cursor()
    try:
        c.execute('''INSERT INTO users (username, email, password_hash, display_name, avatar, signature, created_at, last_active)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (username, email, generate_password_hash(password), display_name, avatar, signature,
                   datetime.now().isoformat(), datetime.now().isoformat()))
        conn.commit()
        user_id = c.lastrowid
        session['user_id'] = user_id
        session['username'] = username
        conn.close()
        return jsonify({'success': True, 'user': {
            'id': user_id, 'username': username, 'email': email,
            'display_name': display_name, 'avatar': avatar, 'signature': signature
        }})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'error': 'Username or email already taken'}), 409

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    username = (data.get('username', '') or '').strip()
    password = data.get('password', '')

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE username = ? OR email = ?', (username, username))
    user = c.fetchone()

    if user and check_password_hash(user['password_hash'], password):
        c.execute('UPDATE users SET last_active = ? WHERE id = ?', (datetime.now().isoformat(), user['id']))
        conn.commit()
        conn.close()
        session['user_id'] = user['id']
        session['username'] = user['username']
        return jsonify({'success': True, 'user': {
            'id': user['id'], 'username': user['username'], 'email': user['email'],
            'display_name': user['display_name'], 'avatar': user['avatar'], 'signature': user['signature']
        }})
    conn.close()
    return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/auth/me', methods=['GET'])
def get_me():
    user = get_current_user()
    if user:
        return jsonify({'success': True, 'user': user})
    return jsonify({'success': False, 'user': None})

@app.route('/api/auth/profile', methods=['PUT'])
@login_required
def update_profile():
    data = request.json
    avatar = data.get('avatar', None)
    signature = data.get('signature', None)
    display_name = data.get('display_name', None)

    conn = get_db()
    c = conn.cursor()
    if avatar and avatar in AVATAR_OPTIONS:
        c.execute('UPDATE users SET avatar = ? WHERE id = ?', (avatar, session['user_id']))
    if signature is not None:
        c.execute('UPDATE users SET signature = ? WHERE id = ?', (signature[:200], session['user_id']))
    if display_name:
        c.execute('UPDATE users SET display_name = ? WHERE id = ?', (display_name.strip()[:50], session['user_id']))
    conn.commit()
    conn.close()
    user = get_current_user()
    return jsonify({'success': True, 'user': user})

@app.route('/api/auth/avatars', methods=['GET'])
def get_avatars():
    return jsonify({'success': True, 'avatars': AVATAR_OPTIONS})

# ─── Forum Endpoints ─────────────────────────────────────────────────────────

@app.route('/api/forum/posts', methods=['GET'])
def get_forum_posts():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    offset = (page - 1) * per_page

    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT fp.*, u.username, u.display_name, u.avatar,
                 (SELECT COUNT(*) FROM forum_comments WHERE post_id = fp.id) as comment_count
                 FROM forum_posts fp
                 JOIN users u ON fp.user_id = u.id
                 ORDER BY fp.created_at DESC
                 LIMIT ? OFFSET ?''', (per_page, offset))
    posts = [dict(row) for row in c.fetchall()]

    c.execute('SELECT COUNT(*) as total FROM forum_posts')
    total = c.fetchone()['total']
    conn.close()
    return jsonify({'success': True, 'data': posts, 'total': total, 'page': page})

@app.route('/api/forum/posts', methods=['POST'])
@login_required
def create_forum_post():
    data = request.json
    title = (data.get('title', '') or '').strip()
    body = (data.get('body', '') or '').strip()

    if not title or not body:
        return jsonify({'success': False, 'error': 'Title and body are required'}), 400
    if len(title) > 200:
        return jsonify({'success': False, 'error': 'Title too long (200 chars max)'}), 400

    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('INSERT INTO forum_posts (user_id, title, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
              (session['user_id'], title, body, now, now))
    conn.commit()
    post_id = c.lastrowid
    conn.close()
    return jsonify({'success': True, 'id': post_id})

@app.route('/api/forum/posts/<int:post_id>', methods=['GET'])
def get_forum_post(post_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT fp.*, u.username, u.display_name, u.avatar, u.signature
                 FROM forum_posts fp JOIN users u ON fp.user_id = u.id
                 WHERE fp.id = ?''', (post_id,))
    post = c.fetchone()
    if not post:
        conn.close()
        return jsonify({'success': False, 'error': 'Post not found'}), 404

    c.execute('''SELECT fc.*, u.username, u.display_name, u.avatar, u.signature
                 FROM forum_comments fc JOIN users u ON fc.user_id = u.id
                 WHERE fc.post_id = ?
                 ORDER BY fc.created_at ASC''', (post_id,))
    comments = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'post': dict(post), 'comments': comments})

@app.route('/api/forum/posts/<int:post_id>/comments', methods=['POST'])
@login_required
def create_forum_comment(post_id):
    data = request.json
    body = (data.get('body', '') or '').strip()

    if not body:
        return jsonify({'success': False, 'error': 'Comment body is required'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id FROM forum_posts WHERE id = ?', (post_id,))
    if not c.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'Post not found'}), 404

    c.execute('INSERT INTO forum_comments (post_id, user_id, body, created_at) VALUES (?, ?, ?, ?)',
              (post_id, session['user_id'], body, datetime.now().isoformat()))
    conn.commit()
    comment_id = c.lastrowid
    conn.close()
    return jsonify({'success': True, 'id': comment_id})

# ─── News API ────────────────────────────────────────────────────────────────

@app.route('/api/news', methods=['GET'])
def get_news():
    category = request.args.get('category', 'business')
    try:
        gnews_category = 'business' if category == 'financial' else 'general'
        url = f'https://gnews.io/api/v4/top-headlines?category={gnews_category}&lang=en&max=8&apikey=demo'
        response = requests.get(url, timeout=10)
        data = response.json()
        articles = []
        if 'articles' in data:
            for article in data.get('articles', []):
                articles.append({
                    'source': article.get('source', {}).get('name', 'Unknown'),
                    'title': article.get('title', ''),
                    'url': article.get('url', ''),
                    'time': article.get('publishedAt', ''),
                    'description': article.get('description', '')
                })
        return jsonify({'success': True, 'articles': articles})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'articles': []})

# ─── The Sauce - Signal-Based Buy Signals ─────────────────────────────────────

SIGNAL_KEYWORDS = {
    'funding': ['raised', 'raises', 'funding', 'funded', 'series a', 'series b', 'series c', 'series d', 'seed', 'round', 'capital', 'investment', 'valuation', '$'],
    'acquisition': ['acquisition', 'acquired', 'acquires', 'merger', 'merged', 'merges', 'deal', 'buyout', 'takeover'],
    'leadership': ['new hire', 'appointed', 'appoints', 'ceo', 'cto', 'cfo', 'vp', 'joined', 'joins', 'leadership', 'executive', 'hire', 'hires'],
    'expansion': ['expansion', 'expands', 'new office', 'hiring', 'growth', 'scale', 'launch', 'launches', 'opens', 'ipo', 'unicorn'],
}

def classify_signal(text):
    """Classify a headline/summary into a signal type based on keyword matching."""
    text_lower = text.lower()
    scores = {}
    for signal_type, keywords in SIGNAL_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[signal_type] = score
    if scores:
        return max(scores, key=scores.get)
    return 'funding'  # Default for VC news

def extract_trigger_words(text):
    """Find which trigger keywords appear in the text."""
    text_lower = text.lower()
    found = []
    all_keywords = []
    for keywords in SIGNAL_KEYWORDS.values():
        all_keywords.extend(keywords)
    for kw in all_keywords:
        if kw in text_lower and kw not in found and kw != '$':
            found.append(kw)
    return found[:4]  # Limit to 4 keywords

def fetch_sauce_alerts():
    """Fetch fresh buy-signal alerts by scraping Crunchbase News via Firecrawl."""
    alerts = []
    firecrawl = FirecrawlClient(FIRECRAWL_API_KEY)

    # Scrape multiple signal sources
    sources = [
        {'url': 'https://news.crunchbase.com/venture/', 'default_signal': 'funding'},
        {'url': 'https://news.crunchbase.com/ma/', 'default_signal': 'acquisition'},
    ]

    for source in sources:
        try:
            result = firecrawl.scrape_url(source['url'])
            if not result or 'data' not in result:
                continue

            markdown = result['data'].get('markdown', '')
            if not markdown:
                continue

            # Parse headlines: look for ## [Title](URL) pattern from Crunchbase News
            lines = markdown.split('\n')
            current_headline = None
            current_url = None
            current_summary = None

            for line in lines:
                line = line.strip()
                # Match ## [Headline](URL) pattern
                headline_match = re.match(r'^##\s*\[(.+?)\]\((.+?)\)', line)
                if headline_match:
                    # Save previous article if exists
                    if current_headline:
                        combined_text = current_headline + ' ' + (current_summary or '')
                        signal_type = classify_signal(combined_text)
                        trigger_words = extract_trigger_words(combined_text)
                        alerts.append({
                            'signal_type': signal_type,
                            'company': 'Crunchbase News',
                            'headline': current_headline,
                            'summary': (current_summary or '')[:200],
                            'source_url': current_url,
                            'trigger_keywords': ', '.join(trigger_words) if trigger_words else signal_type,
                        })

                    current_headline = headline_match.group(1)
                    current_url = headline_match.group(2)
                    current_summary = None
                elif current_headline and not current_summary and line and len(line) > 30 and not line.startswith('#') and not line.startswith('[') and not line.startswith('!') and not line.startswith('-'):
                    current_summary = line

            # Don't forget the last one
            if current_headline:
                combined_text = current_headline + ' ' + (current_summary or '')
                signal_type = classify_signal(combined_text)
                trigger_words = extract_trigger_words(combined_text)
                alerts.append({
                    'signal_type': signal_type,
                    'company': 'Crunchbase News',
                    'headline': current_headline,
                    'summary': (current_summary or '')[:200],
                    'source_url': current_url,
                    'trigger_keywords': ', '.join(trigger_words) if trigger_words else signal_type,
                })

        except Exception as e:
            print(f"Sauce fetch error for {source['url']}: {e}")
            continue

    return alerts[:12]  # Cap at 12 alerts

@app.route('/api/sauce', methods=['GET'])
def get_sauce():
    """Get today's buy-signal alerts. Returns cached if available, fetches fresh if not."""
    today = datetime.now().strftime('%Y-%m-%d')
    force_refresh = request.args.get('refresh') == '1'
    conn = get_db()
    c = conn.cursor()

    # Check cache (skip if force refresh)
    if not force_refresh:
        c.execute('SELECT * FROM sauce_alerts WHERE date_key = ? ORDER BY id DESC', (today,))
        cached = c.fetchall()

        if cached and len(cached) > 0:
            conn.close()
            return jsonify({
                'success': True,
                'alerts': [dict(row) for row in cached],
                'cached': True
            })

    # Clear old cache on refresh
    if force_refresh:
        c.execute('DELETE FROM sauce_alerts WHERE date_key = ?', (today,))
        conn.commit()

    # Fetch fresh
    alerts = fetch_sauce_alerts()

    # Store in cache
    for alert in alerts:
        c.execute('''INSERT INTO sauce_alerts (signal_type, company, headline, summary, source_url, trigger_keywords, created_at, date_key)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (alert['signal_type'], alert['company'], alert['headline'],
                   alert['summary'], alert['source_url'], alert['trigger_keywords'],
                   datetime.now().isoformat(), today))
    conn.commit()
    conn.close()

    return jsonify({
        'success': True,
        'alerts': alerts,
        'cached': False
    })

# ─── Email Guessing ──────────────────────────────────────────────────────────

def guess_email(name: str, company_domain: str) -> list:
    """Generate common email format guesses from name + domain."""
    if not name or not company_domain:
        return []
    parts = name.strip().lower().split()
    if len(parts) < 2:
        return []
    first = parts[0]
    last = parts[-1]
    # Clean domain (remove http/www)
    domain = company_domain.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
    guesses = [
        f"{first}.{last}@{domain}",
        f"{first[0]}{last}@{domain}",
        f"{first}@{domain}",
        f"{first}{last}@{domain}",
        f"{first}_{last}@{domain}",
        f"{first[0]}.{last}@{domain}",
        f"{last}.{first}@{domain}",
    ]
    return guesses

@app.route('/api/guess-email', methods=['POST'])
def guess_email_route():
    data = request.json
    name = data.get('name', '')
    company = data.get('company', '')
    source_url = data.get('source_url', '')
    # Try to derive domain from source URL, then company name
    domain = ''
    if source_url:
        domain = source_url.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]
    elif company:
        # Simple company-to-domain guess
        clean = company.lower().replace(' ', '').replace(',', '').replace('.', '').replace('inc', '').replace('llc', '').replace('corp', '').replace('ltd', '')
        domain = f"{clean}.com"
    guesses = guess_email(name, domain)
    return jsonify({'success': True, 'guesses': guesses, 'domain': domain})

# ─── Duplicate Detection ─────────────────────────────────────────────────────

@app.route('/api/prospects/check-duplicate', methods=['POST'])
def check_duplicate():
    """Check if a prospect already exists by name/email/company similarity."""
    data = request.json
    name = data.get('name', '').strip().lower()
    email = data.get('email', '').strip().lower()
    company = data.get('company', '').strip().lower()

    conn = get_db()
    c = conn.cursor()
    duplicates = []

    # Exact email match
    if email:
        c.execute('SELECT id, name, company, email FROM prospects WHERE LOWER(email) = ?', (email,))
        for row in c.fetchall():
            duplicates.append({**dict(row), 'match_type': 'exact_email'})

    # Name + company match
    if name and company:
        c.execute('SELECT id, name, company, email FROM prospects WHERE LOWER(name) = ? AND LOWER(company) = ?', (name, company))
        for row in c.fetchall():
            if not any(d['id'] == row['id'] for d in duplicates):
                duplicates.append({**dict(row), 'match_type': 'exact_name_company'})

    # Fuzzy name match (same company)
    if name and company:
        c.execute('SELECT id, name, company, email FROM prospects WHERE LOWER(company) = ?', (company,))
        for row in c.fetchall():
            if not any(d['id'] == row['id'] for d in duplicates):
                existing_name = row['name'].lower()
                # Check if names share a significant portion
                name_parts = set(name.split())
                existing_parts = set(existing_name.split())
                overlap = name_parts & existing_parts
                if len(overlap) >= 1 and (len(overlap) / max(len(name_parts), len(existing_parts))) > 0.5:
                    duplicates.append({**dict(row), 'match_type': 'fuzzy_name'})

    conn.close()
    return jsonify({'success': True, 'duplicates': duplicates})

@app.route('/api/prospects/merge', methods=['POST'])
def merge_prospects():
    """Merge two prospects: keep target, delete source, combine notes."""
    data = request.json
    keep_id = data.get('keep_id')
    merge_id = data.get('merge_id')

    if not keep_id or not merge_id:
        return jsonify({'success': False, 'error': 'Both keep_id and merge_id required'}), 400

    conn = get_db()
    c = conn.cursor()

    c.execute('SELECT * FROM prospects WHERE id = ?', (keep_id,))
    keep = c.fetchone()
    c.execute('SELECT * FROM prospects WHERE id = ?', (merge_id,))
    merge = c.fetchone()

    if not keep or not merge:
        conn.close()
        return jsonify({'success': False, 'error': 'Prospect not found'}), 404

    keep = dict(keep)
    merge = dict(merge)

    # Fill in missing fields from merge into keep
    updates = {}
    for field in ['email', 'phone', 'title', 'linkedin_url', 'source']:
        if not keep.get(field) and merge.get(field):
            updates[field] = merge[field]

    # Combine notes
    merged_notes = (keep.get('notes') or '')
    if merge.get('notes'):
        merged_notes = f"{merged_notes}\n[Merged] {merge['notes']}".strip()
    updates['notes'] = merged_notes

    # Use higher deal size
    if (merge.get('deal_size') or 0) > (keep.get('deal_size') or 0):
        updates['deal_size'] = merge['deal_size']

    # Apply updates
    if updates:
        set_clause = ', '.join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [keep_id]
        c.execute(f"UPDATE prospects SET {set_clause} WHERE id = ?", values)

    # Move tasks from merge to keep
    c.execute('UPDATE tasks SET prospect_id = ? WHERE prospect_id = ?', (keep_id, merge_id))

    # Delete merged prospect
    c.execute('DELETE FROM prospects WHERE id = ?', (merge_id,))
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'message': 'Prospects merged successfully'})

# ─── XP / Questing Engine ────────────────────────────────────────────────────

XP_ACTIONS = {
    'prospect_added': 10,
    'scrape_ran': 15,
    'task_added': 5,
    'task_completed': 20,
    'status_lead_to_contacted': 10,
    'status_to_qualified': 15,
    'status_to_proposal': 20,
    'status_to_won': 50,
    'forum_post': 10,
    'forum_comment': 5,
}

LEVEL_THRESHOLDS = [
    (0, 'Rookie', 'bronze'),
    (100, 'Scout', 'bronze'),
    (300, 'Hunter', 'bronze'),
    (600, 'Closer', 'silver'),
    (1000, 'Rainmaker', 'silver'),
    (1500, 'Dealmaker', 'gold'),
    (2500, 'Kingpin', 'gold'),
    (4000, 'Legend', 'diamond'),
    (6000, 'Origin Master', 'diamond'),
]

def get_level_info(total_xp):
    """Get current level name and tier based on total XP."""
    level_name = 'Rookie'
    tier = 'bronze'
    level_num = 1
    next_threshold = 100
    for i, (threshold, name, t) in enumerate(LEVEL_THRESHOLDS):
        if total_xp >= threshold:
            level_name = name
            tier = t
            level_num = i + 1
            next_threshold = LEVEL_THRESHOLDS[i + 1][0] if i + 1 < len(LEVEL_THRESHOLDS) else threshold + 2000
    return {
        'level': level_num,
        'name': level_name,
        'tier': tier,
        'total_xp': total_xp,
        'next_level_xp': next_threshold,
        'progress': min(100, int((total_xp / next_threshold) * 100)) if next_threshold > 0 else 100
    }

def award_xp(action, detail=''):
    """Award XP for an action and return XP earned."""
    xp = XP_ACTIONS.get(action, 0)
    if xp > 0:
        conn = get_db()
        c = conn.cursor()
        c.execute('INSERT INTO xp_log (action, xp_earned, detail, created_at) VALUES (?, ?, ?, ?)',
                  (action, xp, detail, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    return xp

@app.route('/api/xp', methods=['GET'])
def get_xp():
    """Get total XP and level info."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT COALESCE(SUM(xp_earned), 0) as total FROM xp_log')
    total_xp = c.fetchone()['total']
    c.execute('SELECT * FROM xp_log ORDER BY id DESC LIMIT 10')
    recent = [dict(row) for row in c.fetchall()]
    conn.close()
    level_info = get_level_info(total_xp)
    level_info['recent_actions'] = recent
    return jsonify({'success': True, **level_info})

@app.route('/api/xp/award', methods=['POST'])
def award_xp_route():
    data = request.json
    action = data.get('action', '')
    detail = data.get('detail', '')
    xp = award_xp(action, detail)
    # Return updated level info
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT COALESCE(SUM(xp_earned), 0) as total FROM xp_log')
    total_xp = c.fetchone()['total']
    conn.close()
    level_info = get_level_info(total_xp)
    return jsonify({'success': True, 'xp_earned': xp, **level_info})

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
