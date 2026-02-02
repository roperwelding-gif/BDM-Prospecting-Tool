# OriginOS - BDM Prospecting Tool

> I wanted to build a tool that functioned properly and provided insight and high level capabilities to ANY BDM no matter their budget, while also building a community.
> With this tool all you will need is an idea and from there you will have everything you need at your fingertips without the big box tag of a million different subscriptions.
> Eventually I will add an educational section for Business Development for E-Learning and Professional Development so anyone can learn to sell.
> I hope you all enjoy looking at this code and utilizing it to win, close, and make that cheddar.

## Setup

1. Clone the repository
2. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and configure your keys:
   ```
   cp .env.example .env
   ```
4. Edit `.env` with your actual values:
   - `SECRET_KEY` - A random string for Flask session security (required)
   - `FIRECRAWL_API_KEY` - Your Firecrawl API key for web scraping
   - `GNEWS_API_KEY` - GNews API key (use `demo` for free tier)
   - `ALPHA_VANTAGE_API_KEY` - Alpha Vantage key for live stock data
5. Run the application:
   ```
   python backend.py
   ```
6. Open `http://localhost:5000` and register an account to get started

## Architecture

```
backend.py              Flask application (all API routes, database, auth)
templates/
  base.html             Jinja2 base layout (head, CDN links)
  index.html            Main page template (extends base.html)
static/
  css/main.css          All CSS styles
  js/app.js             All JavaScript
  images/logo.png       Logo asset
.env                    Environment variables (not committed)
.env.example            Template for environment variables
requirements.txt        Python dependencies
```

## Features

- Prospect management with CRUD operations
- Kanban pipeline view (drag-and-drop)
- AI-powered prospect discovery via Firecrawl
- Analytics dashboard with Chart.js
- Live stock ticker (Alpha Vantage)
- Live news feeds (GNews)
- Tweet feeds via Nitter RSS
- Buy signal alerts (The Sauce)
- XP gamification system
- Community forum and real-time chat
- Email guessing and duplicate detection
- CSV import/export

## Security

- All sensitive endpoints require authentication
- API keys stored in environment variables
- Rate limiting on scrape/crawl/auth endpoints
- Session-based authentication with bcrypt password hashing

## Future: PostgreSQL Migration

When concurrent users become a concern, migrate from SQLite to PostgreSQL:
- Replace `sqlite3` with `psycopg2` or SQLAlchemy
- Change `PRAGMA table_info` to `information_schema.columns`
- Add `DATABASE_URL` environment variable
- Add connection pooling
