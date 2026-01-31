from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime
import logging
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

DATABASE_PATH = os.getenv('DATABASE_PATH', 'prospects.db')

class DatabaseManager:
    def __init__(self, db_path=DATABASE_PATH):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''CREATE TABLE IF NOT EXISTS icp_parameters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            industries TEXT NOT NULL,
            company_sizes TEXT NOT NULL,
            target_titles TEXT NOT NULL,
            target_locations TEXT NOT NULL,
            keywords TEXT NOT NULL,
            exclude_keywords TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS prospects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            location TEXT,
            company_size TEXT,
            industry TEXT,
            website TEXT,
            linkedin_url TEXT,
            twitter_url TEXT,
            github_url TEXT,
            bio TEXT,
            source TEXT,
            icp_match_percentage REAL DEFAULT 0,
            verified BOOLEAN DEFAULT 0,
            notes TEXT,
            status TEXT DEFAULT 'lead',
            deal_size REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        cursor.execute('''CREATE TABLE IF NOT EXISTS deals (
            id TEXT PRIMARY KEY,
            prospect_id TEXT NOT NULL,
            title TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'lead',
            probability REAL DEFAULT 0,
            expected_close_date TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (prospect_id) REFERENCES prospects(id)
        )''')

        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {self.db_path}")

    def save_prospect(self, prospect_data):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''INSERT OR REPLACE INTO prospects (
                id, name, company, title, email, phone, location,
                company_size, industry, website, linkedin_url, twitter_url,
                github_url, bio, source, icp_match_percentage, verified,
                notes, status, deal_size, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
                prospect_data.get('id'),
                prospect_data.get('name'),
                prospect_data.get('company'),
                prospect_data.get('title'),
                prospect_data.get('email'),
                prospect_data.get('phone'),
                prospect_data.get('location', ''),
                prospect_data.get('company_size', ''),
                prospect_data.get('industry', ''),
                prospect_data.get('website'),
                prospect_data.get('linkedin_url'),
                prospect_data.get('twitter_url'),
                prospect_data.get('github_url'),
                prospect_data.get('bio', ''),
                prospect_data.get('source', 'manual'),
                prospect_data.get('icp_match_percentage', 0),
                prospect_data.get('verified', False),
                prospect_data.get('notes', ''),
                prospect_data.get('status', 'lead'),
                prospect_data.get('deal_size', 0),
                datetime.now().isoformat()
            ))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error saving prospect: {e}")
            return False

    def get_prospects(self, status=None, source=None):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            query = "SELECT * FROM prospects"
            params = []

            if status:
                query += " WHERE status = ?"
                params.append(status)

            if source:
                if status:
                    query += " AND source = ?"
                else:
                    query += " WHERE source = ?"
                params.append(source)

            query += " ORDER BY icp_match_percentage DESC"

            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            prospects = [dict(row) for row in rows]
            return prospects
        except Exception as e:
            logger.error(f"Error retrieving prospects: {e}")
            return []

    def update_prospect_status(self, prospect_id, status):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('UPDATE prospects SET status = ?, updated_at = ? WHERE id = ?',
                (status, datetime.now().isoformat(), prospect_id))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error updating prospect status: {e}")
            return False

    def save_icp(self, icp_data):
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''INSERT OR REPLACE INTO icp_parameters (
                name, industries, company_sizes, target_titles,
                target_locations, keywords, exclude_keywords, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (
                icp_data.get('name'),
                json.dumps(icp_data.get('industries', [])),
                json.dumps(icp_data.get('company_sizes', [])),
                json.dumps(icp_data.get('target_titles', [])),
                json.dumps(icp_data.get('target_locations', [])),
                json.dumps(icp_data.get('keywords', [])),
                json.dumps(icp_data.get('exclude_keywords', [])),
                datetime.now().isoformat()
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Error saving ICP: {e}")
            return False

    def get_icp(self, name):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM icp_parameters WHERE name = ?", (name,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                result = dict(row)
                result['industries'] = json.loads(result.get('industries', '[]'))
                result['company_sizes'] = json.loads(result.get('company_sizes', '[]'))
                result['target_titles'] = json.loads(result.get('target_titles', '[]'))
                result['target_locations'] = json.loads(result.get('target_locations', '[]'))
                result['keywords'] = json.loads(result.get('keywords', '[]'))
                result['exclude_keywords'] = json.loads(result.get('exclude_keywords', '[]'))
                return result
            return None
        except Exception as e:
            logger.error(f"Error retrieving ICP: {e}")
            return None

    def list_icps(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT name, created_at FROM icp_parameters ORDER BY updated_at DESC")
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error listing ICPs: {e}")
            return []

    def get_stats(self):
        try:
            all_prospects = self.get_prospects()
            
            stats = {
                'total_prospects': len(all_prospects),
                'by_status': {},
                'total_pipeline_value': 0,
                'avg_match_score': 0
            }

            statuses = ['lead', 'contacted', 'qualified', 'proposal', 'negotiation', 'won', 'lost']
            for status in statuses:
                count = len([p for p in all_prospects if p['status'] == status])
                stats['by_status'][status] = count

            total_value = sum(float(p.get('deal_size', 0)) for p in all_prospects)
            stats['total_pipeline_value'] = total_value
            
            if all_prospects:
                avg = sum(float(p.get('icp_match_percentage', 0)) for p in all_prospects) / len(all_prospects)
                stats['avg_match_score'] = round(avg, 2)

            return stats
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}

db_manager = DatabaseManager()

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/api/icp', methods=['POST'])
def create_icp():
    try:
        data = request.get_json()
        if db_manager.save_icp(data):
            return jsonify({'success': True, 'message': f'ICP profile "{data.get("name")}" created'})
        return jsonify({'success': False, 'error': 'Failed to save ICP'}), 400
    except Exception as e:
        logger.error(f"Error creating ICP: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/icp', methods=['GET'])
def list_icps():
    try:
        icps = db_manager.list_icps()
        return jsonify({'success': True, 'data': icps})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/icp/<name>', methods=['GET'])
def get_icp(name):
    try:
        icp = db_manager.get_icp(name)
        if icp:
            return jsonify({'success': True, 'data': icp})
        return jsonify({'success': False, 'error': 'ICP not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prospects', methods=['GET'])
def get_prospects():
    try:
        status = request.args.get('status')
        source = request.args.get('source')
        prospects = db_manager.get_prospects(status=status, source=source)
        return jsonify({'success': True, 'count': len(prospects), 'data': prospects})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prospects', methods=['POST'])
def create_prospect():
    try:
        data = request.get_json()
        data['id'] = data.get('id', f"prospect_{datetime.now().timestamp()}")
        
        if db_manager.save_prospect(data):
            return jsonify({'success': True, 'data': data})
        return jsonify({'success': False, 'error': 'Failed to save prospect'}), 400
    except Exception as e:
        logger.error(f"Error creating prospect: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prospects/<prospect_id>/status', methods=['PUT'])
def update_prospect_status(prospect_id):
    try:
        data = request.get_json()
        status = data.get('status')
        
        if db_manager.update_prospect_status(prospect_id, status):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Failed to update'}), 400
    except Exception as e:
        logger.error(f"Error updating prospect status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def get_stats():
    try:
        stats = db_manager.get_stats()
        return jsonify({'success': True, 'data': stats})
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
