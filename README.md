# Prospect Tracker - Web Version

A clean, simple prospect tracking tool that runs in your browser. No backend required—data is stored locally in your browser.

## What Changed

✅ **Removed** VS Code dependency  
✅ **Simplified** to pure HTML/CSS/JavaScript  
✅ **Added** in-browser data storage (localStorage)  
✅ **Kept** the same prospect model and features  

## Features

- 📊 Real-time dashboard with pipeline stats
- 🎯 Track prospects through 7 pipeline stages
- 💾 Data stored locally in your browser (no signup required)
- 📱 Responsive design (works on mobile, tablet, desktop)
- ⚡ Zero backend—just open and use

## How to Use

### Option 1: Open Directly (Fastest)
1. Download or copy `index.html`
2. Double-click the file in your browser
3. Start adding prospects

### Option 2: Simple Web Server (Recommended)

If you have Python:
```bash
# Python 3
python -m http.server 8000

# Python 2
python -m SimpleHTTPServer 8000
```

If you have Node.js:
```bash
npx serve
```

Then open `http://localhost:8000` in your browser.

### Option 3: Deploy to Free Hosting

**Netlify (Easiest)**
1. Drag and drop `index.html` to https://app.netlify.com/drop
2. Done! Get a live URL instantly

**Vercel**
```bash
npm i -g vercel
vercel
```

**GitHub Pages**
1. Create a repo
2. Add `index.html` to main branch
3. Enable Pages in Settings > Pages
4. Access at `yourusername.github.io/repo-name`

## Data Storage

Data is stored in **browser localStorage** — this means:
- ✅ All data stays on YOUR device/browser
- ✅ No account needed
- ✅ No server costs
- ⚠️ Data is unique to each browser/device (clearing browser data will delete prospects)

**To backup your data:**
1. Open DevTools (F12)
2. Go to Application > Local Storage
3. Copy the "prospects" entry to save as JSON

**To restore:**
1. Open DevTools
2. Go to Application > Local Storage
3. Paste your JSON data back into the "prospects" key

## Future Enhancements

- [ ] Backend sync (Google Sheets, Airtable)
- [ ] Multi-device sync with Firebase
- [ ] CSV import/export
- [ ] Email reminders
- [ ] CRM integrations (Salesforce, HubSpot)
- [ ] Team collaboration

## Tech Stack

- HTML5
- CSS3
- Vanilla JavaScript
- Browser localStorage API

## License

MIT
