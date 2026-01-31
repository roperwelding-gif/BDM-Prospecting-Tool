from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_FILE = 'prospects.db'

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
        created_at TEXT
    )''')
    
    conn.commit()
    conn.close()

init_db()

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
    c.execute('''INSERT INTO prospects (id, name, company, title, email, status, deal_size, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (prospect_id, data.get('name'), data.get('company'), data.get('title'), 
               data.get('email'), data.get('status', 'lead'), data.get('deal_size', 0),
               datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'id': prospect_id})

@app.route('/api/prospects/<prospect_id>', methods=['PUT'])
def update_prospect(prospect_id):
    data = request.json
    
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE prospects SET status = ? WHERE id = ?', (data.get('status'), prospect_id))
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
    try:
        data = request.json
        query = data.get('query')
        
        # Web crawler simulation - in production uses FireCrawler API
        # Searches LinkedIn, Twitter, Reddit, GitHub, etc.
        mock_prospects = [
            {
                'name': 'John Smith',
                'company': 'TechStart Inc',
                'title': 'Founder & CEO',
                'email': 'john@techstart.com',
                'source': 'linkedin'
            },
            {
                'name': 'Sarah Johnson',
                'company': 'Innovation Labs',
                'title': 'VP of Product',
                'email': 'sarah@innovlabs.com',
                'source': 'twitter'
            },
            {
                'name': 'Mike Chen',
                'company': 'Digital Solutions',
                'title': 'CTO',
                'email': 'mike@digitalsol.com',
                'source': 'github'
            }
        ]

        return jsonify({
            'success': True,
            'prospects': mock_prospects,
            'message': f'Found {len(mock_prospects)} prospects matching "{query}"'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
