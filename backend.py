from flask import Flask, request, jsonify, send_from_directory, render_template, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from dotenv import load_dotenv
import sqlite3
import os
import csv
import io
from datetime import datetime, timedelta
import requests
import re
import time as _time
import random
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse

load_dotenv()

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.environ.get('SECRET_KEY', '')
if not app.secret_key:
    raise RuntimeError('SECRET_KEY environment variable is required. Copy .env.example to .env and set it.')
CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
limiter = Limiter(app=app, key_func=get_remote_address, default_limits=["200 per hour"])

DB_FILE = 'prospects.db'
FIRECRAWL_API_KEY = os.environ.get('FIRECRAWL_API_KEY', '')
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
    # Safe column migration helper
    def column_exists(cursor, table, column):
        cursor.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cursor.fetchall())

    # Add phone column if upgrading from older schema
    if not column_exists(c, 'prospects', 'phone'):
        c.execute('ALTER TABLE prospects ADD COLUMN phone TEXT')

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

    # ─── Accounts (company normalization) ─────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        website TEXT,
        industry TEXT,
        employee_count INTEGER,
        headquarters_location TEXT,
        created_at TEXT,
        updated_at TEXT
    )''')

    # ─── Activity Timeline ────────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prospect_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        description TEXT,
        metadata TEXT,
        created_at TEXT,
        FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE CASCADE
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_activity_prospect ON activity_log(prospect_id, created_at)')

    # ─── User Stock Symbols ───────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS user_stocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL UNIQUE,
        added_at TEXT
    )''')
    # Seed defaults if empty
    c.execute('SELECT COUNT(*) as cnt FROM user_stocks')
    if c.fetchone()['cnt'] == 0:
        defaults = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'JPM', 'V', 'SPY', 'QQQ', 'NFLX', 'AMD']
        for sym in defaults:
            c.execute('INSERT OR IGNORE INTO user_stocks (symbol, added_at) VALUES (?, ?)', (sym, datetime.now().isoformat()))

    # ─── Email Sequences ──────────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS email_sequences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS sequence_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sequence_id INTEGER NOT NULL,
        step_number INTEGER NOT NULL,
        day_offset INTEGER NOT NULL,
        subject_template TEXT,
        body_template TEXT,
        step_type TEXT DEFAULT 'email',
        FOREIGN KEY (sequence_id) REFERENCES email_sequences(id) ON DELETE CASCADE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS prospect_sequences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        prospect_id TEXT NOT NULL,
        sequence_id INTEGER NOT NULL,
        enrolled_at TEXT,
        current_step INTEGER DEFAULT 1,
        status TEXT DEFAULT 'active',
        completed_at TEXT,
        FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE CASCADE,
        FOREIGN KEY (sequence_id) REFERENCES email_sequences(id) ON DELETE CASCADE
    )''')

    # ─── Gamification: Streaks & Challenges ───────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS streaks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        current_streak INTEGER DEFAULT 0,
        longest_streak INTEGER DEFAULT 0,
        last_active_date TEXT,
        updated_at TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS challenges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        challenge_type TEXT NOT NULL,
        target_action TEXT NOT NULL,
        target_count INTEGER NOT NULL,
        xp_reward INTEGER NOT NULL,
        is_active INTEGER DEFAULT 1,
        start_date TEXT,
        end_date TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS challenge_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        challenge_id INTEGER NOT NULL,
        user_id INTEGER,
        current_count INTEGER DEFAULT 0,
        completed INTEGER DEFAULT 0,
        completed_at TEXT,
        date_key TEXT,
        FOREIGN KEY (challenge_id) REFERENCES challenges(id) ON DELETE CASCADE
    )''')

    # Seed challenges — replace old set with full 20 if needed
    c.execute('SELECT COUNT(*) as cnt FROM challenges')
    if c.fetchone()['cnt'] < 20:
        c.execute('DELETE FROM challenges')
        all_challenges = [
            # Daily challenges (12)
            ('Add 2 Prospects', 'Add 2 new prospects to your pipeline', 'daily', 'prospect_added', 2, 10),
            ('Add 5 Prospects', 'Add 5 new prospects today', 'daily', 'prospect_added', 5, 25),
            ('Contact 3 Prospects', 'Move 3 leads to contacted status', 'daily', 'status_lead_to_contacted', 3, 20),
            ('Contact 5 Prospects', 'Reach out to 5 prospects today', 'daily', 'status_lead_to_contacted', 5, 30),
            ('Complete 3 Tasks', 'Finish 3 tasks today', 'daily', 'task_completed', 3, 15),
            ('Complete 5 Tasks', 'Knock out 5 tasks in one day', 'daily', 'task_completed', 5, 30),
            ('Create 3 Tasks', 'Plan your day with 3 new tasks', 'daily', 'task_added', 3, 10),
            ('Qualify a Lead', 'Move a prospect to qualified status', 'daily', 'status_to_qualified', 1, 15),
            ('Send a Proposal', 'Advance a prospect to proposal stage', 'daily', 'status_to_proposal', 1, 20),
            ('Run a Scrape', 'Use the AI scraper to discover prospects', 'daily', 'scrape_ran', 1, 15),
            ('Forum Contributor', 'Share knowledge with a forum post', 'daily', 'forum_post', 1, 10),
            ('Join the Discussion', 'Comment on a forum post', 'daily', 'forum_comment', 2, 10),
            # Weekly challenges (8)
            ('Close a Deal', 'Win a deal this week', 'weekly', 'status_to_won', 1, 100),
            ('Closer Streak', 'Win 3 deals this week', 'weekly', 'status_to_won', 3, 200),
            ('Pipeline Builder', 'Add 10 prospects this week', 'weekly', 'prospect_added', 10, 50),
            ('Prospecting Machine', 'Add 20 prospects this week', 'weekly', 'prospect_added', 20, 80),
            ('Task Master', 'Complete 10 tasks this week', 'weekly', 'task_completed', 10, 40),
            ('Outreach Blitz', 'Contact 10 prospects this week', 'weekly', 'status_lead_to_contacted', 10, 60),
            ('Qualification Expert', 'Qualify 5 leads this week', 'weekly', 'status_to_qualified', 5, 50),
            ('Proposal Push', 'Send 3 proposals this week', 'weekly', 'status_to_proposal', 3, 75),
        ]
        for title, desc, ctype, action, count, xp in all_challenges:
            c.execute('INSERT INTO challenges (title, description, challenge_type, target_action, target_count, xp_reward, is_active) VALUES (?,?,?,?,?,?,0)',
                      (title, desc, ctype, action, count, xp))

    # ─── Forum Reports ────────────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS forum_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER,
        comment_id INTEGER,
        reporter_user_id INTEGER NOT NULL,
        reason TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        FOREIGN KEY (post_id) REFERENCES forum_posts(id) ON DELETE CASCADE,
        FOREIGN KEY (comment_id) REFERENCES forum_comments(id) ON DELETE CASCADE,
        FOREIGN KEY (reporter_user_id) REFERENCES users(id)
    )''')

    # Migrate existing tables (add new columns)
    migrations = [
        ('prospects', 'warmth_score', 'INTEGER DEFAULT 20'),
        ('prospects', 'last_contact_date', 'TEXT'),
        ('prospects', 'email_opens', 'INTEGER DEFAULT 0'),
        ('prospects', 'reply_count', 'INTEGER DEFAULT 0'),
        ('prospects', 'status_updated_at', 'TEXT'),
        ('prospects', 'account_id', 'INTEGER'),
        ('tasks', 'priority', "TEXT DEFAULT 'medium'"),
        ('tasks', 'category', "TEXT DEFAULT 'general'"),
        ('users', 'role', "TEXT DEFAULT 'user'"),
        ('forum_posts', 'is_reported', 'INTEGER DEFAULT 0'),
        ('forum_comments', 'is_reported', 'INTEGER DEFAULT 0'),
        ('xp_log', 'user_id', 'INTEGER'),
    ]
    for table, col, col_type in migrations:
        if not column_exists(c, table, col):
            c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}')

    conn.commit()
    conn.close()

init_db()

# ─── Challenge Rotation ──────────────────────────────────────────────────────

def rotate_challenges():
    """Pick a fresh set of active challenges: 4 daily + 2 weekly = 6 total.
    Rotates once per calendar day using a date-based seed for consistency."""
    conn = get_db()
    c = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')

    # Check if we already rotated today
    c.execute("SELECT COUNT(*) as cnt FROM challenges WHERE is_active = 1 AND start_date = ?", (today,))
    if c.fetchone()['cnt'] > 0:
        conn.close()
        return  # Already rotated today

    # Get all challenges by type
    c.execute("SELECT id FROM challenges WHERE challenge_type = 'daily'")
    daily_ids = [row['id'] for row in c.fetchall()]
    c.execute("SELECT id FROM challenges WHERE challenge_type = 'weekly'")
    weekly_ids = [row['id'] for row in c.fetchall()]

    # Seed random with today's date for consistent daily rotation
    rng = random.Random(today)
    pick_daily = rng.sample(daily_ids, min(4, len(daily_ids)))
    pick_weekly = rng.sample(weekly_ids, min(2, len(weekly_ids)))
    active_ids = pick_daily + pick_weekly

    # Deactivate all, then activate the picked set
    c.execute("UPDATE challenges SET is_active = 0, start_date = NULL")
    for cid in active_ids:
        c.execute("UPDATE challenges SET is_active = 1, start_date = ? WHERE id = ?", (today, cid))

    conn.commit()
    conn.close()

# Run rotation on startup so challenges are always populated
rotate_challenges()

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
                'formats': ['markdown', 'html'],
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

def calculate_extraction_confidence(prospect: dict) -> int:
    """Score 0-100 based on data completeness and quality of an extracted prospect."""
    score = 0
    name = prospect.get('name', '')
    if name and len(name.split()) >= 2:
        score += 25
        if len(name.split()) == 2:
            score += 5
    if prospect.get('title'):
        score += 20
        title_kw = ['CEO', 'CTO', 'VP', 'Director', 'Manager', 'Head', 'Lead', 'Founder', 'President', 'CFO', 'COO']
        if any(kw.lower() in prospect['title'].lower() for kw in title_kw):
            score += 5
    if prospect.get('company'):
        score += 15
        if prospect['company'] != 'Unknown Company':
            score += 5
    if prospect.get('email'):
        score += 15
    if prospect.get('linkedin_url'):
        score += 10
    return min(100, score)

def _extract_from_html_cards(html_content: str, source_url: str) -> List[Dict]:
    """Extract prospects from HTML team/about pages using structural patterns."""
    prospects = []
    if not html_content:
        return prospects

    title_keywords_lower = [
        'ceo', 'cto', 'cfo', 'coo', 'cmo', 'vp', 'director', 'manager',
        'head of', 'lead', 'engineer', 'developer', 'designer', 'founder',
        'president', 'chief', 'officer', 'executive', 'consultant',
        'architect', 'principal', 'senior', 'partner', 'analyst', 'coordinator',
        'specialist', 'recruiter', 'advisor', 'strategist', 'associate', 'co-founder',
        'controller', 'managing'
    ]

    # Strategy: find repeated card-like elements with names and titles
    # Look for text content between tags that matches Name + Title patterns
    # Remove scripts and styles
    clean_html = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    clean_html = re.sub(r'<style[^>]*>.*?</style>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)

    # Extract text blocks from card-like divs or sections
    # Find all text content, stripping tags
    text_blocks = re.split(r'<(?:div|section|article|li|td|figure)[^>]*>', clean_html, flags=re.IGNORECASE)

    name_pattern = re.compile(r'\b([A-Z][a-z]{1,15}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})?)\b')

    seen_names = set()

    for block in text_blocks:
        if len(block) > 2000 or len(block) < 10:
            continue
        # Strip remaining tags to get text
        text = re.sub(r'<[^>]+>', ' ', block)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) < 5 or len(text) > 500:
            continue

        names = name_pattern.findall(text)
        for name in names:
            name = name.strip()
            if len(name.split()) < 2:
                continue
            name_lower = name.lower()
            if name_lower in seen_names:
                continue

            # Check it's not a title/company name
            skip_words = {'privacy', 'policy', 'terms', 'copyright', 'contact', 'about', 'learn',
                          'read', 'more', 'view', 'sign', 'join', 'follow', 'get', 'started'}
            if any(w in name_lower for w in skip_words):
                continue
            first_word = name.split()[0].lower()
            title_first = ['chief', 'vice', 'senior', 'junior', 'lead', 'principal', 'director', 'manager', 'head', 'general', 'managing']
            if first_word in title_first:
                continue

            # Look for title in the same block
            title = None
            for kw in title_keywords_lower:
                if kw in text.lower():
                    # Extract the line/phrase containing the keyword
                    for segment in text.split('  '):
                        seg_clean = segment.strip()
                        if kw in seg_clean.lower() and seg_clean.lower() != name_lower and len(seg_clean) < 150:
                            title = seg_clean
                            break
                    if not title:
                        # Try finding title near the keyword
                        idx = text.lower().find(kw)
                        start = max(0, idx - 5)
                        end = min(len(text), idx + 80)
                        candidate = text[start:end].strip()
                        if candidate and candidate.lower() != name_lower:
                            title = candidate
                    if title:
                        break

            # Extract email from block
            email = None
            emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
            if emails:
                personal = [e for e in emails if not any(s in e.lower() for s in ['noreply', 'no-reply', 'support', 'info@', 'hello@'])]
                email = personal[0] if personal else emails[0]

            # Extract LinkedIn
            linkedin_url = None
            li_match = re.search(r'https?://(?:www\.)?linkedin\.com/in/[^\s"\'<>]+', block)
            if li_match:
                linkedin_url = li_match.group(0).rstrip('/')

            # Extract phone
            phone = None
            phone_match = re.search(r'[\(]?\d{3}[\).\-\s]?\s*\d{3}[\-.\s]\d{4}', text)
            if phone_match:
                phone = phone_match.group(0)

            if title or email or linkedin_url or phone:
                seen_names.add(name_lower)
                prospect = {
                    'name': name,
                    'title': title,
                    'company': None,
                    'email': email,
                    'phone': phone,
                    'linkedin_url': linkedin_url,
                    'source': source_url
                }
                prospect['confidence'] = calculate_extraction_confidence(prospect)
                prospects.append(prospect)

    return prospects

def _extract_from_headings(content: str, source_url: str) -> List[Dict]:
    """Extract prospects from markdown heading patterns (## Name / ### Name)."""
    prospects = []
    if not content:
        return prospects

    title_keywords_lower = [
        'ceo', 'cto', 'cfo', 'coo', 'cmo', 'vp', 'director', 'manager',
        'head of', 'lead', 'founder', 'president', 'chief', 'officer', 'partner',
        'executive', 'principal', 'senior', 'associate', 'co-founder', 'analyst'
    ]

    heading_pattern = re.compile(r'^#{2,4}\s+([A-Z][a-z]+ [A-Z][a-z]+(?: [A-Z][a-z]+)?)\s*$', re.MULTILINE)
    seen_names = set()

    for match in heading_pattern.finditer(content):
        name = match.group(1).strip()
        name_lower = name.lower()
        if name_lower in seen_names or len(name.split()) < 2:
            continue

        # Get context after the heading
        pos = match.end()
        context = content[pos:pos + 400]

        title = None
        lines = context.split('\n')
        for line in lines[:6]:
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith('#'):
                continue
            for kw in title_keywords_lower:
                if kw in line_stripped.lower() and len(line_stripped) < 150:
                    title = line_stripped
                    break
            if title:
                break

        email = None
        emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', context)
        if emails:
            email = emails[0]

        linkedin_url = None
        li_match = re.search(r'https?://(?:www\.)?linkedin\.com/in/[^\s)]+', context)
        if li_match:
            linkedin_url = li_match.group(0)

        if title or email or linkedin_url:
            seen_names.add(name_lower)
            prospect = {
                'name': name, 'title': title, 'company': None,
                'email': email, 'linkedin_url': linkedin_url, 'source': source_url
            }
            prospect['confidence'] = calculate_extraction_confidence(prospect)
            prospects.append(prospect)

    return prospects

def _extract_from_jsonld(html_content: str, source_url: str) -> List[Dict]:
    """Extract prospects from JSON-LD schema.org Person data."""
    import json as _json
    prospects = []
    if not html_content:
        return prospects

    ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_content, re.DOTALL | re.IGNORECASE)
    for block in ld_blocks:
        try:
            data = _json.loads(block)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get('@type') == 'Person' or (isinstance(item.get('@graph'), list)):
                    persons = [item] if item.get('@type') == 'Person' else [g for g in item.get('@graph', []) if g.get('@type') == 'Person']
                    for person in persons:
                        name = person.get('name', '')
                        if not name or len(name.split()) < 2:
                            continue
                        prospect = {
                            'name': name,
                            'title': person.get('jobTitle'),
                            'company': None,
                            'email': person.get('email'),
                            'linkedin_url': None,
                            'source': source_url
                        }
                        org = person.get('worksFor')
                        if isinstance(org, dict):
                            prospect['company'] = org.get('name')
                        elif isinstance(org, str):
                            prospect['company'] = org
                        for link in (person.get('sameAs') or []):
                            if 'linkedin.com' in str(link):
                                prospect['linkedin_url'] = link
                                break
                        prospect['confidence'] = calculate_extraction_confidence(prospect)
                        prospects.append(prospect)
        except (ValueError, KeyError, TypeError):
            continue
    return prospects

def extract_prospects_from_content(content: str, source_url: str, html_content: str = None) -> List[Dict]:
    """
    Extract prospect information using multiple strategies.
    Tries regex on markdown first, falls back to HTML card parsing,
    heading extraction, and JSON-LD.
    """
    prospects = _extract_regex(content, source_url)

    # If regex found few results, try other strategies
    if len(prospects) < 2 and html_content:
        html_prospects = _extract_from_html_cards(html_content, source_url)
        if len(html_prospects) > len(prospects):
            prospects = html_prospects

    if len(prospects) < 2 and html_content:
        jsonld_prospects = _extract_from_jsonld(html_content, source_url)
        if len(jsonld_prospects) > len(prospects):
            prospects = jsonld_prospects

    if len(prospects) < 2:
        heading_prospects = _extract_from_headings(content, source_url)
        if len(heading_prospects) > len(prospects):
            prospects = heading_prospects

    return prospects

def _extract_regex(content: str, source_url: str) -> List[Dict]:
    """
    Original regex-based extraction from Firecrawl markdown content.
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
            prospect = {
                'name': name,
                'title': title,
                'company': company,
                'email': email,
                'linkedin_url': linkedin_url,
                'source': source_url
            }
            prospect['confidence'] = calculate_extraction_confidence(prospect)
            raw_prospects.append(prospect)

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

def log_activity(prospect_id, event_type, description, metadata=None):
    """Log an activity event for a prospect."""
    try:
        conn = get_db()
        c = conn.cursor()
        import json as _json
        c.execute('INSERT INTO activity_log (prospect_id, event_type, description, metadata, created_at) VALUES (?,?,?,?,?)',
                  (prospect_id, event_type, description, _json.dumps(metadata) if metadata else None, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass

# ─── Static File Serving ──────────────────────────────────────────────────────

@app.route('/logo.png')
def serve_logo():
    return send_from_directory('static/images', 'logo.png')

@app.route('/')
def serve_index():
    return render_template('index.html')

# ─── Health Check ─────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

# ─── Prospect CRUD ────────────────────────────────────────────────────────────

@app.route('/api/prospects', methods=['GET'])
@login_required
def get_prospects():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    per_page = min(per_page, 200)
    offset = (page - 1) * per_page
    status_filter = request.args.get('status', None)
    search_q = request.args.get('q', None)

    conn = get_db()
    c = conn.cursor()

    where_clauses = []
    params = []
    if status_filter and status_filter != 'all':
        where_clauses.append('status = ?')
        params.append(status_filter)
    if search_q:
        where_clauses.append('(name LIKE ? OR company LIKE ? OR title LIKE ? OR email LIKE ?)')
        like_q = f'%{search_q}%'
        params.extend([like_q, like_q, like_q, like_q])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ''

    c.execute(f'SELECT COUNT(*) as total FROM prospects {where_sql}', params)
    total = c.fetchone()['total']

    c.execute(f'SELECT * FROM prospects {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?',
              params + [per_page, offset])
    prospects = []
    for row in c.fetchall():
        p = dict(row)
        p['warmth_score'] = calculate_warmth_score(p)
        # Stale lead detection
        if p.get('status_updated_at'):
            try:
                days_in_status = (datetime.now() - datetime.fromisoformat(p['status_updated_at'])).days
                p['is_stale'] = days_in_status >= 14 and p.get('status') in ('qualified', 'proposal')
                p['days_in_status'] = days_in_status
            except (ValueError, TypeError):
                p['is_stale'] = False
                p['days_in_status'] = 0
        else:
            p['is_stale'] = False
            p['days_in_status'] = 0
        prospects.append(p)
    conn.close()
    return jsonify({
        'success': True, 'data': prospects,
        'total': total, 'page': page, 'per_page': per_page,
        'total_pages': max(1, (total + per_page - 1) // per_page)
    })

@app.route('/api/prospects', methods=['POST'])
@login_required
def add_prospect():
    data = request.json
    prospect_id = f"p_{datetime.now().timestamp()}"
    conn = get_db()
    c = conn.cursor()
    now_iso = datetime.now().isoformat()
    c.execute('''INSERT INTO prospects (id, name, company, title, email, phone, status, deal_size, created_at, source, linkedin_url, notes, warmth_score, last_contact_date, email_opens, reply_count, status_updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (prospect_id, data.get('name'), data.get('company'), data.get('title'),
               data.get('email'), data.get('phone'), data.get('status', 'lead'), data.get('deal_size', 0),
               now_iso, data.get('source'), data.get('linkedin_url'),
               data.get('notes'), 20, None, 0, 0, now_iso))
    conn.commit()
    conn.close()
    award_xp('prospect_added', data.get('name', ''))
    log_activity(prospect_id, 'created', f'Prospect "{data.get("name", "")}" added')
    return jsonify({'success': True, 'id': prospect_id})

@app.route('/api/prospects/<prospect_id>', methods=['PUT'])
@login_required
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
                  'linkedin_url', 'warmth_score', 'last_contact_date', 'email_opens', 'reply_count', 'account_id']:
        if field in data:
            update_fields.append(f"{field} = ?")
            values.append(data[field])

    # Auto-update status_updated_at when status changes
    if 'status' in data and old_status != data.get('status'):
        update_fields.append('status_updated_at = ?')
        values.append(datetime.now().isoformat())

    if update_fields:
        values.append(prospect_id)
        query = f"UPDATE prospects SET {', '.join(update_fields)} WHERE id = ?"
        c.execute(query, values)
        conn.commit()

    # Award XP for status progressions & log activity
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
        log_activity(prospect_id, 'status_change', f'Status changed from {old_status} to {new_status}',
                     {'old_status': old_status, 'new_status': new_status})

    conn.close()
    return jsonify({'success': True})

@app.route('/api/prospects/<prospect_id>', methods=['DELETE'])
@login_required
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
@login_required
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
@limiter.limit("10 per minute")
@login_required
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
                content = result['data'].get('markdown', '')
                html_content = result['data'].get('html', '')
                page_prospects = extract_prospects_from_content(content or html_content, url, html_content=html_content)
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
                    content = page.get('markdown', '')
                    html_content = page.get('html', '')
                    page_url = page.get('url', url)
                    page_prospects = extract_prospects_from_content(content or html_content, page_url, html_content=html_content)
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
@limiter.limit("10 per minute")
@login_required
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
        content = result['data'].get('markdown', '')
        html_content = result['data'].get('html', '')
        prospects = extract_prospects_from_content(content or html_content, url, html_content=html_content)
        return jsonify({
            'success': True, 'data': result['data'],
            'prospects': prospects, 'prospect_count': len(prospects)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/crawl', methods=['POST'])
@limiter.limit("5 per minute")
@login_required
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
                content = page.get('markdown', '')
                html_content = page.get('html', '')
                page_url = page.get('url', url)
                page_prospects = extract_prospects_from_content(content or html_content, page_url, html_content=html_content)
                all_prospects.extend(page_prospects)
                pages.append({
                    'url': page_url, 'prospect_count': len(page_prospects),
                    'content_length': len(content or html_content or '')
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
@login_required
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
@login_required
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
@login_required
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
@login_required
def add_task():
    data = request.json
    task_id = f"t_{datetime.now().timestamp()}"
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO tasks (id, prospect_id, title, description, due_date, status, created_at, priority, category)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (task_id, data.get('prospect_id'), data.get('title'),
               data.get('description', ''), data.get('due_date'),
               'pending', datetime.now().isoformat(),
               data.get('priority', 'medium'), data.get('category', 'general')))
    conn.commit()
    conn.close()
    award_xp('task_added', data.get('title', ''))
    if data.get('prospect_id'):
        log_activity(data['prospect_id'], 'task_created', f'Task created: {data.get("title", "")}')
    return jsonify({'success': True, 'id': task_id})

@app.route('/api/tasks/<task_id>', methods=['PUT'])
@login_required
def update_task(task_id):
    data = request.json
    conn = get_db()
    c = conn.cursor()

    # Check if completing a task for XP
    c.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
    old_task = c.fetchone()
    if data.get('status') == 'completed' and old_task and old_task['status'] != 'completed':
        award_xp('task_completed', task_id)
        if old_task['prospect_id']:
            log_activity(old_task['prospect_id'], 'task_completed', f'Task completed: {old_task["title"]}')

    update_fields = []
    values = []
    for field in ['title', 'description', 'due_date', 'status', 'priority', 'category']:
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
@login_required
def delete_task(task_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ─── AI Icebreaker ────────────────────────────────────────────────────────────

@app.route('/api/icebreaker', methods=['POST'])
@login_required
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
@limiter.limit("5 per hour")
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
@limiter.limit("10 per minute")
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
        # Rotate challenges on login (picks new set if day changed)
        rotate_challenges()
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

@app.route('/api/profile/stats', methods=['GET'])
@login_required
def get_profile_stats():
    """Get comprehensive profile data for the profile modal."""
    user = get_current_user()
    if not user:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    conn = get_db()
    c = conn.cursor()

    uid = user['id']

    # XP & level info (user-scoped)
    c.execute('SELECT COALESCE(SUM(xp_earned), 0) as total FROM xp_log WHERE user_id = ? OR user_id IS NULL', (uid,))
    total_xp = c.fetchone()['total']
    level_info = get_level_info(total_xp)

    # Streak info
    c.execute('SELECT * FROM streaks WHERE user_id = ? LIMIT 1', (uid,))
    streak = c.fetchone()
    if not streak:
        c.execute('SELECT * FROM streaks LIMIT 1')
        streak = c.fetchone()
    streak_info = dict(streak) if streak else {'current_streak': 0, 'longest_streak': 0}

    # Prospect stats
    c.execute('SELECT COUNT(*) as total FROM prospects')
    total_prospects = c.fetchone()['total']
    c.execute("SELECT COUNT(*) as count FROM prospects WHERE status = 'won'")
    won_deals = c.fetchone()['count']
    c.execute('SELECT COALESCE(SUM(deal_size), 0) as value FROM prospects')
    pipeline_value = c.fetchone()['value']
    c.execute("SELECT COALESCE(SUM(deal_size), 0) as value FROM prospects WHERE status = 'won'")
    won_value = c.fetchone()['value']

    # Task stats
    c.execute('SELECT COUNT(*) as count FROM tasks')
    total_tasks = c.fetchone()['count']
    c.execute("SELECT COUNT(*) as count FROM tasks WHERE status = 'completed'")
    completed_tasks = c.fetchone()['count']

    # Forum stats
    c.execute('SELECT COUNT(*) as count FROM forum_posts WHERE user_id = ?', (uid,))
    forum_posts = c.fetchone()['count']
    c.execute('SELECT COUNT(*) as count FROM forum_comments WHERE user_id = ?', (uid,))
    forum_comments = c.fetchone()['count']

    # XP action breakdown (user-scoped)
    c.execute('SELECT action, COUNT(*) as count, SUM(xp_earned) as total_xp FROM xp_log WHERE user_id = ? OR user_id IS NULL GROUP BY action ORDER BY total_xp DESC', (uid,))
    xp_breakdown = [dict(row) for row in c.fetchall()]

    # Challenge stats
    c.execute('SELECT COUNT(*) as count FROM challenge_progress WHERE completed = 1')
    challenges_completed = c.fetchone()['count']

    # Member since
    c.execute('SELECT created_at FROM users WHERE id = ?', (user['id'],))
    member_row = c.fetchone()
    member_since = member_row['created_at'] if member_row else None

    conn.close()
    return jsonify({
        'success': True,
        'user': user,
        'level': level_info,
        'streak': streak_info,
        'stats': {
            'total_prospects': total_prospects,
            'won_deals': won_deals,
            'pipeline_value': pipeline_value,
            'won_value': won_value,
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'forum_posts': forum_posts,
            'forum_comments': forum_comments,
            'challenges_completed': challenges_completed,
            'member_since': member_since,
        },
        'xp_breakdown': xp_breakdown,
    })

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
        gnews_key = os.environ.get('GNEWS_API_KEY', 'demo')
        url = f'https://gnews.io/api/v4/top-headlines?category={gnews_category}&lang=en&max=8&apikey={gnews_key}'
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

# ─── Live Stock Ticker ───────────────────────────────────────────────────────

_stock_cache = {'data': [], 'timestamp': 0}
ALPHA_VANTAGE_KEY = os.environ.get('ALPHA_VANTAGE_API_KEY', '')
STOCK_SYMBOLS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'JPM', 'V', 'SPY', 'QQQ', 'NFLX', 'AMD']

@app.route('/api/stocks', methods=['GET'])
def get_stocks():
    """Return stock data. Cached for 5 minutes."""
    now = _time.time()
    if _stock_cache['data'] and (now - _stock_cache['timestamp']) < 300:
        return jsonify({'success': True, 'stocks': _stock_cache['data'], 'cached': True})

    if not ALPHA_VANTAGE_KEY:
        return jsonify({'success': True, 'stocks': [], 'error': 'No API key configured'})

    # Read symbols from DB, rotate which 5 to fetch
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT symbol FROM user_stocks ORDER BY id ASC')
    all_symbols = [row['symbol'] for row in c.fetchall()]
    conn.close()
    if not all_symbols:
        all_symbols = STOCK_SYMBOLS

    # Rotate: use cache timestamp to cycle through symbol groups
    batch_size = 5
    batch_index = int(now / 300) % max(1, (len(all_symbols) + batch_size - 1) // batch_size)
    batch_symbols = all_symbols[batch_index * batch_size:(batch_index + 1) * batch_size]
    if not batch_symbols:
        batch_symbols = all_symbols[:batch_size]

    stocks = []
    try:
        for sym in batch_symbols:
            url = f'https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={sym}&apikey={ALPHA_VANTAGE_KEY}'
            resp = requests.get(url, timeout=8)
            data = resp.json()
            quote = data.get('Global Quote', {})
            if quote:
                price = float(quote.get('05. price', 0))
                change = float(quote.get('09. change', 0))
                stocks.append({'symbol': sym, 'price': round(price, 2), 'change': round(change, 2)})
    except Exception as e:
        print(f"Stock API error: {e}")
        if _stock_cache['data']:
            return jsonify({'success': True, 'stocks': _stock_cache['data'], 'cached': True, 'stale': True})
        return jsonify({'success': True, 'stocks': []})

    if stocks:
        _stock_cache['data'] = stocks
        _stock_cache['timestamp'] = now
    return jsonify({'success': True, 'stocks': stocks, 'cached': False})

# ─── Tweets (hardcoded — Nitter RSS removed due to instability) ──────────────

# ─── Analytics Dashboard ─────────────────────────────────────────────────────

@app.route('/api/analytics', methods=['GET'])
@login_required
def get_analytics():
    conn = get_db()
    c = conn.cursor()

    pipeline = {}
    for status in ['lead', 'contacted', 'qualified', 'proposal', 'won', 'lost']:
        c.execute('SELECT COUNT(*) as count, COALESCE(SUM(deal_size), 0) as value FROM prospects WHERE status = ?', (status,))
        row = c.fetchone()
        pipeline[status] = {'count': row['count'], 'value': row['value']}

    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
    c.execute('''SELECT DATE(created_at) as day, COUNT(*) as count, COALESCE(SUM(deal_size), 0) as value
                 FROM prospects WHERE created_at >= ?
                 GROUP BY DATE(created_at) ORDER BY day''', (thirty_days_ago,))
    timeline = [{'day': row['day'], 'count': row['count'], 'value': row['value']} for row in c.fetchall()]

    c.execute('SELECT COUNT(*) as total FROM prospects')
    total = c.fetchone()['total']
    conversions = {}
    if total > 0:
        for status in ['contacted', 'qualified', 'proposal', 'won']:
            c.execute('SELECT COUNT(*) as count FROM prospects WHERE status = ?', (status,))
            conversions[status] = round(c.fetchone()['count'] / total * 100, 1)

    c.execute('''SELECT DATE(created_at) as day, COUNT(*) as actions
                 FROM xp_log WHERE created_at >= ?
                 GROUP BY DATE(created_at) ORDER BY day''', (thirty_days_ago,))
    activity = [{'day': row['day'], 'actions': row['actions']} for row in c.fetchall()]

    conn.close()
    return jsonify({
        'success': True,
        'pipeline': pipeline,
        'timeline': timeline,
        'conversions': conversions,
        'activity': activity
    })

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
@login_required
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
@login_required
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
@login_required
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
    """Award XP for an action, update streak and challenge progress."""
    xp = XP_ACTIONS.get(action, 0)
    if xp > 0:
        conn = get_db()
        c = conn.cursor()
        now = datetime.now()
        now_iso = now.isoformat()
        today_str = now.strftime('%Y-%m-%d')
        uid = session.get('user_id')

        c.execute('INSERT INTO xp_log (action, xp_earned, detail, created_at, user_id) VALUES (?, ?, ?, ?, ?)',
                  (action, xp, detail, now_iso, uid))

        # Update streak
        c.execute('SELECT * FROM streaks LIMIT 1')
        streak = c.fetchone()
        if streak:
            streak = dict(streak)
            last_date = streak.get('last_active_date', '')
            if last_date == today_str:
                pass  # Already counted today
            elif last_date == (now - timedelta(days=1)).strftime('%Y-%m-%d'):
                new_streak = streak['current_streak'] + 1
                longest = max(streak['longest_streak'], new_streak)
                c.execute('UPDATE streaks SET current_streak = ?, longest_streak = ?, last_active_date = ?, updated_at = ? WHERE id = ?',
                          (new_streak, longest, today_str, now_iso, streak['id']))
            else:
                c.execute('UPDATE streaks SET current_streak = 1, last_active_date = ?, updated_at = ? WHERE id = ?',
                          (today_str, now_iso, streak['id']))
        else:
            c.execute('INSERT INTO streaks (current_streak, longest_streak, last_active_date, updated_at) VALUES (1, 1, ?, ?)',
                      (today_str, now_iso))

        # Update challenge progress
        year, week, _ = now.isocalendar()
        week_key = f'{year}-W{week:02d}'
        c.execute('SELECT * FROM challenges WHERE is_active = 1 AND target_action = ?', (action,))
        for ch in c.fetchall():
            ch = dict(ch)
            date_key = today_str if ch['challenge_type'] == 'daily' else week_key
            c.execute('SELECT * FROM challenge_progress WHERE challenge_id = ? AND date_key = ?', (ch['id'], date_key))
            prog = c.fetchone()
            if prog:
                prog = dict(prog)
                if not prog['completed']:
                    new_count = prog['current_count'] + 1
                    completed = 1 if new_count >= ch['target_count'] else 0
                    c.execute('UPDATE challenge_progress SET current_count = ?, completed = ?, completed_at = ? WHERE id = ?',
                              (new_count, completed, now_iso if completed else None, prog['id']))
                    if completed:
                        c.execute('INSERT INTO xp_log (action, xp_earned, detail, created_at, user_id) VALUES (?, ?, ?, ?, ?)',
                                  ('challenge_completed', ch['xp_reward'], ch['title'], now_iso, uid))
            else:
                completed = 1 if 1 >= ch['target_count'] else 0
                c.execute('INSERT INTO challenge_progress (challenge_id, current_count, completed, completed_at, date_key) VALUES (?,?,?,?,?)',
                          (ch['id'], 1, completed, now_iso if completed else None, date_key))
                if completed:
                    c.execute('INSERT INTO xp_log (action, xp_earned, detail, created_at, user_id) VALUES (?, ?, ?, ?, ?)',
                              ('challenge_completed', ch['xp_reward'], ch['title'], now_iso, uid))

        conn.commit()
        conn.close()
    return xp

@app.route('/api/xp', methods=['GET'])
@login_required
def get_xp():
    """Get total XP, level info, streak, and challenge progress."""
    uid = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT COALESCE(SUM(xp_earned), 0) as total FROM xp_log WHERE user_id = ? OR user_id IS NULL', (uid,))
    total_xp = c.fetchone()['total']
    c.execute('SELECT * FROM xp_log WHERE user_id = ? OR user_id IS NULL ORDER BY id DESC LIMIT 10', (uid,))
    recent = [dict(row) for row in c.fetchall()]

    # Streak info
    c.execute('SELECT * FROM streaks LIMIT 1')
    streak = c.fetchone()
    streak_info = dict(streak) if streak else {'current_streak': 0, 'longest_streak': 0}

    # Active challenges with progress
    today = datetime.now().strftime('%Y-%m-%d')
    year, week, _ = datetime.now().isocalendar()
    week_key = f'{year}-W{week:02d}'
    c.execute('SELECT * FROM challenges WHERE is_active = 1')
    challenges = []
    for ch in c.fetchall():
        ch = dict(ch)
        date_key = today if ch['challenge_type'] == 'daily' else week_key
        c.execute('SELECT current_count, completed FROM challenge_progress WHERE challenge_id = ? AND date_key = ?',
                  (ch['id'], date_key))
        prog = c.fetchone()
        ch['current_count'] = prog['current_count'] if prog else 0
        ch['completed'] = bool(prog['completed']) if prog else False
        challenges.append(ch)

    conn.close()
    level_info = get_level_info(total_xp)
    level_info['recent_actions'] = recent
    level_info['streak'] = streak_info
    level_info['challenges'] = challenges
    return jsonify({'success': True, **level_info})

@app.route('/api/xp/award', methods=['POST'])
@login_required
def award_xp_route():
    data = request.json
    action = data.get('action', '')
    detail = data.get('detail', '')
    xp = award_xp(action, detail)
    # Return updated level info
    uid = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT COALESCE(SUM(xp_earned), 0) as total FROM xp_log WHERE user_id = ? OR user_id IS NULL', (uid,))
    total_xp = c.fetchone()['total']
    conn.close()
    level_info = get_level_info(total_xp)
    return jsonify({'success': True, 'xp_earned': xp, **level_info})

# ─── Activity Timeline ────────────────────────────────────────────────────────

@app.route('/api/prospects/<prospect_id>/activity', methods=['GET'])
@login_required
def get_prospect_activity(prospect_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM activity_log WHERE prospect_id = ? ORDER BY created_at DESC LIMIT 50', (prospect_id,))
    events = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': events})

@app.route('/api/prospects/<prospect_id>/activity', methods=['POST'])
@login_required
def add_prospect_activity(prospect_id):
    data = request.json
    log_activity(prospect_id, data.get('event_type', 'note'), data.get('description', ''), data.get('metadata'))
    return jsonify({'success': True})

# ─── Accounts (Company Normalization) ────────────────────────────────────────

@app.route('/api/accounts', methods=['GET'])
@login_required
def get_accounts():
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT a.*, COUNT(p.id) as prospect_count, COALESCE(SUM(p.deal_size), 0) as total_deal_value
                 FROM accounts a LEFT JOIN prospects p ON p.account_id = a.id
                 GROUP BY a.id ORDER BY a.name ASC''')
    accounts = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': accounts})

@app.route('/api/accounts', methods=['POST'])
@login_required
def create_account():
    data = request.json
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('INSERT INTO accounts (name, website, industry, employee_count, headquarters_location, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
              (data.get('name'), data.get('website'), data.get('industry'),
               data.get('employee_count'), data.get('headquarters_location'), now, now))
    account_id = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'id': account_id})

@app.route('/api/accounts/<int:account_id>', methods=['GET'])
@login_required
def get_account(account_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM accounts WHERE id = ?', (account_id,))
    account = c.fetchone()
    if not account:
        conn.close()
        return jsonify({'success': False, 'error': 'Account not found'}), 404
    account = dict(account)
    c.execute('SELECT * FROM prospects WHERE account_id = ? ORDER BY name ASC', (account_id,))
    account['prospects'] = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': account})

@app.route('/api/accounts/<int:account_id>', methods=['PUT'])
@login_required
def update_account(account_id):
    data = request.json
    conn = get_db()
    c = conn.cursor()
    fields = []
    values = []
    for f in ['name', 'website', 'industry', 'employee_count', 'headquarters_location']:
        if f in data:
            fields.append(f'{f} = ?')
            values.append(data[f])
    fields.append('updated_at = ?')
    values.append(datetime.now().isoformat())
    values.append(account_id)
    c.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
@login_required
def delete_account(account_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE prospects SET account_id = NULL WHERE account_id = ?', (account_id,))
    c.execute('DELETE FROM accounts WHERE id = ?', (account_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/accounts/<int:account_id>/link-prospect', methods=['POST'])
@login_required
def link_prospect_to_account(account_id):
    data = request.json
    prospect_id = data.get('prospect_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE prospects SET account_id = ? WHERE id = ?', (account_id, prospect_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ─── Stock Symbols Management ────────────────────────────────────────────────

@app.route('/api/stocks/symbols', methods=['GET'])
@login_required
def get_stock_symbols():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT symbol, added_at FROM user_stocks ORDER BY id ASC')
    symbols = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': symbols})

@app.route('/api/stocks/symbols', methods=['POST'])
@login_required
def add_stock_symbol():
    data = request.json
    symbol = (data.get('symbol') or '').upper().strip()
    if not symbol or len(symbol) > 5 or not symbol.isalpha():
        return jsonify({'success': False, 'error': 'Invalid symbol (1-5 uppercase letters)'}), 400
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute('INSERT INTO user_stocks (symbol, added_at) VALUES (?, ?)', (symbol, datetime.now().isoformat()))
        conn.commit()
    except Exception:
        conn.close()
        return jsonify({'success': False, 'error': 'Symbol already exists'}), 409
    conn.close()
    return jsonify({'success': True, 'symbol': symbol})

@app.route('/api/stocks/symbols/<symbol>', methods=['DELETE'])
@login_required
def remove_stock_symbol(symbol):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM user_stocks WHERE symbol = ?', (symbol.upper(),))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ─── Email Sequences ─────────────────────────────────────────────────────────

@app.route('/api/sequences', methods=['GET'])
@login_required
def get_sequences():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM email_sequences ORDER BY created_at DESC')
    sequences = [dict(row) for row in c.fetchall()]
    for seq in sequences:
        c.execute('SELECT * FROM sequence_steps WHERE sequence_id = ? ORDER BY step_number ASC', (seq['id'],))
        seq['steps'] = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': sequences})

@app.route('/api/sequences', methods=['POST'])
@login_required
def create_sequence():
    data = request.json
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('INSERT INTO email_sequences (name, description, is_active, created_at, updated_at) VALUES (?,?,1,?,?)',
              (data.get('name'), data.get('description', ''), now, now))
    seq_id = c.lastrowid
    for i, step in enumerate(data.get('steps', []), 1):
        c.execute('INSERT INTO sequence_steps (sequence_id, step_number, day_offset, subject_template, body_template, step_type) VALUES (?,?,?,?,?,?)',
                  (seq_id, i, step.get('day_offset', 0), step.get('subject_template', ''),
                   step.get('body_template', ''), step.get('step_type', 'email')))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'id': seq_id})

@app.route('/api/sequences/<int:seq_id>', methods=['GET'])
@login_required
def get_sequence(seq_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM email_sequences WHERE id = ?', (seq_id,))
    seq = c.fetchone()
    if not seq:
        conn.close()
        return jsonify({'success': False, 'error': 'Sequence not found'}), 404
    seq = dict(seq)
    c.execute('SELECT * FROM sequence_steps WHERE sequence_id = ? ORDER BY step_number ASC', (seq_id,))
    seq['steps'] = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': seq})

@app.route('/api/sequences/<int:seq_id>', methods=['PUT'])
@login_required
def update_sequence(seq_id):
    data = request.json
    conn = get_db()
    c = conn.cursor()
    fields = []
    values = []
    for f in ['name', 'description', 'is_active']:
        if f in data:
            fields.append(f'{f} = ?')
            values.append(data[f])
    fields.append('updated_at = ?')
    values.append(datetime.now().isoformat())
    values.append(seq_id)
    c.execute(f"UPDATE email_sequences SET {', '.join(fields)} WHERE id = ?", values)
    if 'steps' in data:
        c.execute('DELETE FROM sequence_steps WHERE sequence_id = ?', (seq_id,))
        for i, step in enumerate(data['steps'], 1):
            c.execute('INSERT INTO sequence_steps (sequence_id, step_number, day_offset, subject_template, body_template, step_type) VALUES (?,?,?,?,?,?)',
                      (seq_id, i, step.get('day_offset', 0), step.get('subject_template', ''),
                       step.get('body_template', ''), step.get('step_type', 'email')))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/sequences/<int:seq_id>', methods=['DELETE'])
@login_required
def delete_sequence(seq_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM sequence_steps WHERE sequence_id = ?', (seq_id,))
    c.execute('DELETE FROM prospect_sequences WHERE sequence_id = ?', (seq_id,))
    c.execute('DELETE FROM email_sequences WHERE id = ?', (seq_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/prospects/<prospect_id>/enroll', methods=['POST'])
@login_required
def enroll_prospect_in_sequence(prospect_id):
    data = request.json
    seq_id = data.get('sequence_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM sequence_steps WHERE sequence_id = ? ORDER BY step_number ASC', (seq_id,))
    steps = [dict(row) for row in c.fetchall()]
    if not steps:
        conn.close()
        return jsonify({'success': False, 'error': 'Sequence has no steps'}), 400
    now = datetime.now()
    now_iso = now.isoformat()
    c.execute('INSERT INTO prospect_sequences (prospect_id, sequence_id, enrolled_at, current_step, status) VALUES (?,?,?,1,?)',
              (prospect_id, seq_id, now_iso, 'active'))
    for step in steps:
        task_due = (now + timedelta(days=step['day_offset'])).strftime('%Y-%m-%d')
        task_id = f"t_{datetime.now().timestamp()}"
        c.execute('INSERT INTO tasks (id, prospect_id, title, description, due_date, status, created_at, priority, category) VALUES (?,?,?,?,?,?,?,?,?)',
                  (task_id, prospect_id, step['subject_template'] or f"Sequence Step {step['step_number']}",
                   step['body_template'] or '', task_due, 'pending', now_iso, 'medium', step.get('step_type', 'email')))
    conn.commit()
    conn.close()
    log_activity(prospect_id, 'sequence_enrolled', f'Enrolled in sequence #{seq_id}')
    return jsonify({'success': True})

@app.route('/api/prospects/<prospect_id>/sequences', methods=['GET'])
@login_required
def get_prospect_sequences(prospect_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT ps.*, es.name as sequence_name FROM prospect_sequences ps
                 JOIN email_sequences es ON ps.sequence_id = es.id
                 WHERE ps.prospect_id = ? ORDER BY ps.enrolled_at DESC''', (prospect_id,))
    sequences = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': sequences})

# ─── Contact Enrichment ──────────────────────────────────────────────────────

@app.route('/api/prospects/<prospect_id>/enrich', methods=['POST'])
@login_required
def enrich_prospect(prospect_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM prospects WHERE id = ?', (prospect_id,))
    prospect = c.fetchone()
    if not prospect:
        conn.close()
        return jsonify({'success': False, 'error': 'Prospect not found'}), 404
    prospect = dict(prospect)
    enrichment = {}

    # Email guessing
    name = prospect.get('name', '')
    company = prospect.get('company', '')
    source = prospect.get('source', '')
    if name and not prospect.get('email'):
        domain = None
        if source:
            try:
                domain = urlparse(source).netloc.replace('www.', '')
            except Exception:
                pass
        if not domain and company:
            domain = re.sub(r'[^a-z0-9]', '', company.lower()) + '.com'
        if domain:
            parts = name.strip().split()
            if len(parts) >= 2:
                first = parts[0].lower()
                last = parts[-1].lower()
                enrichment['email_guesses'] = [
                    f"{first}.{last}@{domain}", f"{first[0]}{last}@{domain}",
                    f"{first}@{domain}", f"{first}{last}@{domain}",
                    f"{first}_{last}@{domain}", f"{first[0]}.{last}@{domain}",
                    f"{last}.{first}@{domain}",
                ]

    # LinkedIn URL construction
    if not prospect.get('linkedin_url') and name:
        parts = name.strip().split()
        if len(parts) >= 2:
            slug = '-'.join(p.lower() for p in parts)
            enrichment['linkedin_suggestion'] = f"https://www.linkedin.com/in/{slug}"

    # Hunter.io email verification (if key configured)
    hunter_key = os.environ.get('HUNTER_API_KEY', '')
    if hunter_key and prospect.get('email'):
        try:
            resp = requests.get(f'https://api.hunter.io/v2/email-verifier?email={prospect["email"]}&api_key={hunter_key}', timeout=10)
            hdata = resp.json().get('data', {})
            enrichment['email_verification'] = {
                'status': hdata.get('status', 'unknown'),
                'score': hdata.get('score', 0),
            }
        except Exception:
            enrichment['email_verification'] = {'status': 'error', 'score': 0}

    # Clearbit company enrichment (if key configured)
    clearbit_key = os.environ.get('CLEARBIT_API_KEY', '')
    if clearbit_key and company:
        domain_for_clearbit = None
        if source:
            try:
                domain_for_clearbit = urlparse(source).netloc.replace('www.', '')
            except Exception:
                pass
        if domain_for_clearbit:
            try:
                resp = requests.get(f'https://company.clearbit.com/v2/companies/find?domain={domain_for_clearbit}',
                                    headers={'Authorization': f'Bearer {clearbit_key}'}, timeout=10)
                if resp.status_code == 200:
                    cdata = resp.json()
                    enrichment['company_info'] = {
                        'industry': cdata.get('category', {}).get('industry'),
                        'employee_count': cdata.get('metrics', {}).get('employees'),
                        'location': cdata.get('geo', {}).get('city'),
                    }
            except Exception:
                pass

    conn.close()
    log_activity(prospect_id, 'enriched', 'Contact enrichment performed', enrichment)
    return jsonify({'success': True, 'enrichment': enrichment})

# ─── Gamification: Streaks & Challenges ──────────────────────────────────────

@app.route('/api/streaks', methods=['GET'])
@login_required
def get_streaks():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM streaks LIMIT 1')
    streak = c.fetchone()
    if streak:
        streak = dict(streak)
    else:
        streak = {'current_streak': 0, 'longest_streak': 0, 'last_active_date': None}
    conn.close()
    return jsonify({'success': True, **streak})

@app.route('/api/challenges', methods=['GET'])
def get_challenges():
    conn = get_db()
    c = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    year, week, _ = datetime.now().isocalendar()
    week_key = f'{year}-W{week:02d}'

    c.execute('SELECT * FROM challenges WHERE is_active = 1')
    challenges = []
    for ch in c.fetchall():
        ch = dict(ch)
        date_key = today if ch['challenge_type'] == 'daily' else week_key
        c.execute('SELECT current_count, completed FROM challenge_progress WHERE challenge_id = ? AND date_key = ?',
                  (ch['id'], date_key))
        progress = c.fetchone()
        ch['current_count'] = progress['current_count'] if progress else 0
        ch['completed'] = bool(progress['completed']) if progress else False
        challenges.append(ch)
    conn.close()
    return jsonify({'success': True, 'data': challenges})

@app.route('/api/leaderboard', methods=['GET'])
@login_required
def get_leaderboard():
    conn = get_db()
    c = conn.cursor()
    c.execute('''SELECT u.id, u.username, u.display_name, u.avatar,
                 COALESCE(SUM(x.xp_earned), 0) as total_xp
                 FROM users u LEFT JOIN xp_log x ON x.user_id = u.id
                 GROUP BY u.id ORDER BY total_xp DESC LIMIT 20''')
    leaders = []
    for row in c.fetchall():
        entry = dict(row)
        level_info = get_level_info(entry['total_xp'])
        entry['level'] = level_info['level']
        entry['level_name'] = level_info['name']
        entry['tier'] = level_info['tier']
        leaders.append(entry)
    current_uid = session.get('user_id')
    conn.close()
    return jsonify({'success': True, 'data': leaders, 'current_user_id': current_uid})

# ─── Forum Moderation ────────────────────────────────────────────────────────

@app.route('/api/forum/posts/<int:post_id>', methods=['PUT'])
@login_required
def edit_forum_post(post_id):
    data = request.json
    user_id = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT user_id FROM forum_posts WHERE id = ?', (post_id,))
    post = c.fetchone()
    if not post:
        conn.close()
        return jsonify({'success': False, 'error': 'Post not found'}), 404
    c.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    is_admin = user and user['role'] in ('admin', 'moderator')
    if post['user_id'] != user_id and not is_admin:
        conn.close()
        return jsonify({'success': False, 'error': 'Not authorized'}), 403
    c.execute('UPDATE forum_posts SET title = ?, body = ?, updated_at = ? WHERE id = ?',
              (data.get('title'), data.get('body'), datetime.now().isoformat(), post_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/forum/posts/<int:post_id>', methods=['DELETE'])
@login_required
def delete_forum_post(post_id):
    user_id = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT user_id FROM forum_posts WHERE id = ?', (post_id,))
    post = c.fetchone()
    if not post:
        conn.close()
        return jsonify({'success': False, 'error': 'Post not found'}), 404
    c.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    is_admin = user and user['role'] in ('admin', 'moderator')
    if post['user_id'] != user_id and not is_admin:
        conn.close()
        return jsonify({'success': False, 'error': 'Not authorized'}), 403
    c.execute('DELETE FROM forum_comments WHERE post_id = ?', (post_id,))
    c.execute('DELETE FROM forum_posts WHERE id = ?', (post_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/forum/comments/<int:comment_id>', methods=['PUT'])
@login_required
def edit_forum_comment(comment_id):
    data = request.json
    user_id = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT user_id FROM forum_comments WHERE id = ?', (comment_id,))
    comment = c.fetchone()
    if not comment:
        conn.close()
        return jsonify({'success': False, 'error': 'Comment not found'}), 404
    c.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    is_admin = user and user['role'] in ('admin', 'moderator')
    if comment['user_id'] != user_id and not is_admin:
        conn.close()
        return jsonify({'success': False, 'error': 'Not authorized'}), 403
    c.execute('UPDATE forum_comments SET body = ? WHERE id = ?', (data.get('body'), comment_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/forum/comments/<int:comment_id>', methods=['DELETE'])
@login_required
def delete_forum_comment(comment_id):
    user_id = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT user_id FROM forum_comments WHERE id = ?', (comment_id,))
    comment = c.fetchone()
    if not comment:
        conn.close()
        return jsonify({'success': False, 'error': 'Comment not found'}), 404
    c.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    is_admin = user and user['role'] in ('admin', 'moderator')
    if comment['user_id'] != user_id and not is_admin:
        conn.close()
        return jsonify({'success': False, 'error': 'Not authorized'}), 403
    c.execute('DELETE FROM forum_comments WHERE id = ?', (comment_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/forum/posts/<int:post_id>/report', methods=['POST'])
@login_required
def report_forum_post(post_id):
    data = request.json
    user_id = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO forum_reports (post_id, reporter_user_id, reason, created_at) VALUES (?,?,?,?)',
              (post_id, user_id, data.get('reason', ''), datetime.now().isoformat()))
    c.execute('UPDATE forum_posts SET is_reported = 1 WHERE id = ?', (post_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/forum/comments/<int:comment_id>/report', methods=['POST'])
@login_required
def report_forum_comment(comment_id):
    data = request.json
    user_id = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO forum_reports (comment_id, reporter_user_id, reason, created_at) VALUES (?,?,?,?)',
              (comment_id, user_id, data.get('reason', ''), datetime.now().isoformat()))
    c.execute('UPDATE forum_comments SET is_reported = 1 WHERE id = ?', (comment_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/reports', methods=['GET'])
@login_required
def get_reports():
    user_id = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    if not user or user['role'] not in ('admin', 'moderator'):
        conn.close()
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    c.execute('SELECT * FROM forum_reports WHERE status = ? ORDER BY created_at DESC', ('pending',))
    reports = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': reports})

@app.route('/api/admin/reports/<int:report_id>', methods=['PUT'])
@login_required
def resolve_report(report_id):
    data = request.json
    user_id = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT role FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    if not user or user['role'] not in ('admin', 'moderator'):
        conn.close()
        return jsonify({'success': False, 'error': 'Admin access required'}), 403
    c.execute('UPDATE forum_reports SET status = ? WHERE id = ?', (data.get('status', 'reviewed'), report_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

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
