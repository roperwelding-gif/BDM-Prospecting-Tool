// ─── MATRIX RAIN BACKGROUND ──────────────────────────────────────────────
(function initMatrix() {
    const canvas = document.getElementById('matrix-canvas');
    const ctx = canvas.getContext('2d');

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    const chars = '01アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン';
    const fontSize = 14;
    const columns = Math.floor(canvas.width / fontSize);
    const drops = Array(columns).fill(1);

    function draw() {
        ctx.fillStyle = 'rgba(10, 10, 10, 0.05)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#00ff41';
        ctx.font = fontSize + 'px monospace';

        for (let i = 0; i < drops.length; i++) {
            const text = chars[Math.floor(Math.random() * chars.length)];
            ctx.fillText(text, i * fontSize, drops[i] * fontSize);

            if (drops[i] * fontSize > canvas.height && Math.random() > 0.975) {
                drops[i] = 0;
            }
            drops[i]++;
        }
    }

    setInterval(draw, 50);
})();

// ─── AUTH FETCH WRAPPER ─────────────────────────────────────────────────
// Override fetch to always include credentials and handle 401 globally
const _origFetch = window.fetch;
window.fetch = function(url, opts = {}) {
    opts.credentials = opts.credentials || 'same-origin';
    return _origFetch(url, opts).then(resp => {
        if (resp.status === 401 && !url.includes('/api/auth/')) {
            showLoginPrompt();
        }
        return resp;
    });
};

let isLoggedIn = false;

// ─── SKELETON LOADING HELPERS ─────────────────────────────────────────────
function skeletonHTML(type, count = 3) {
    if (type === 'card') return Array(count).fill('<div class="skeleton skeleton-card"></div>').join('');
    if (type === 'line') return Array(count).fill('<div class="skeleton skeleton-line"></div>').join('');
    if (type === 'lines') return Array(count).fill(0).map((_, i) =>
        `<div class="skeleton skeleton-line${i % 3 === 2 ? ' short' : i % 2 === 1 ? ' medium' : ''}"></div>`).join('');
    if (type === 'chart') return '<div class="skeleton skeleton-chart"></div>';
    return '';
}

function showLoginPrompt() {
    const loginBar = document.getElementById('header-login-bar');
    if (loginBar) loginBar.style.display = 'flex';
}

function hideLoginPrompt() {
    const loginBar = document.getElementById('header-login-bar');
    if (loginBar) loginBar.style.display = 'none';
}

async function headerLogin() {
    const u = document.getElementById('header-login-user').value.trim();
    const p = document.getElementById('header-login-pass').value.trim();
    if (!u || !p) return;
    const res = await _origFetch(`${window.location.origin}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ username: u, password: p })
    });
    const data = await res.json();
    if (data.success) {
        isLoggedIn = true;
        hideLoginPrompt();
        document.getElementById('header-user-display').textContent = data.user.display_name || data.user.username;
        document.getElementById('header-user-display').style.display = 'inline';
        document.getElementById('header-logout-btn').style.display = 'inline';
        document.getElementById('header-login-btn').style.display = 'none';
        loadProspects();
        loadAllTasks();
        updateXPDisplay();
        loadStats();
    } else {
        alert(data.error || 'Login failed');
    }
}

async function headerLogout() {
    await fetch(`${window.location.origin}/api/auth/logout`, { method: 'POST' });
    isLoggedIn = false;
    document.getElementById('header-user-display').style.display = 'none';
    document.getElementById('header-logout-btn').style.display = 'none';
    document.getElementById('header-login-btn').style.display = 'inline';
    location.reload();
}

async function headerRegister() {
    const u = document.getElementById('header-login-user').value.trim();
    const p = document.getElementById('header-login-pass').value.trim();
    const e = document.getElementById('header-login-email')?.value.trim();
    if (!u || !p) return;
    const body = { username: u, password: p };
    if (e) body.email = e;
    else body.email = u + '@example.com';
    const res = await _origFetch(`${window.location.origin}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(body)
    });
    const data = await res.json();
    if (data.success) {
        alert('Registered! Logging in...');
        headerLogin();
    } else {
        alert(data.error || 'Registration failed');
    }
}

async function checkSession() {
    try {
        const res = await _origFetch(`${window.location.origin}/api/auth/me`, { credentials: 'same-origin' });
        const data = await res.json();
        if (data.success && data.user) {
            isLoggedIn = true;
            document.getElementById('header-user-display').textContent = data.user.display_name || data.user.username;
            document.getElementById('header-user-display').style.display = 'inline';
            document.getElementById('header-logout-btn').style.display = 'inline';
            document.getElementById('header-login-btn').style.display = 'none';
            hideLoginPrompt();
        } else {
            showLoginPrompt();
        }
    } catch(e) {
        showLoginPrompt();
    }
}

// ─── CONFIG ───────────────────────────────────────────────────────────────
const API_BASE = window.location.origin + '/api';
let prospects = [];
let allTasks = [];
let currentFilter = 'all';
let editingId = null;
let selectedProspectsForBulkAdd = new Set();
let crawledProspectsCache = [];
let chatUsername = localStorage.getItem('chat_username') || '';
let chatSocket = null;
let chatOpen = false;
let unreadMessages = 0;

// ─── STOCK TICKER ─────────────────────────────────────────────────────────
const fallbackStockData = [
    { symbol: 'AAPL', price: 198.45, change: 2.31 },
    { symbol: 'MSFT', price: 415.20, change: -1.45 },
    { symbol: 'GOOGL', price: 175.83, change: 3.12 },
    { symbol: 'AMZN', price: 191.70, change: 0.89 },
    { symbol: 'NVDA', price: 892.50, change: 15.40 },
    { symbol: 'META', price: 505.15, change: -2.80 },
    { symbol: 'TSLA', price: 245.60, change: 8.20 },
    { symbol: 'BRK.B', price: 408.30, change: 1.10 },
    { symbol: 'JPM', price: 198.75, change: -0.65 },
    { symbol: 'V', price: 279.40, change: 1.85 },
    { symbol: 'SPY', price: 502.30, change: 3.20 },
    { symbol: 'QQQ', price: 432.80, change: 5.60 },
    { symbol: 'DIS', price: 112.45, change: -1.20 },
    { symbol: 'NFLX', price: 628.90, change: 7.35 },
    { symbol: 'AMD', price: 178.25, change: 4.50 },
];

function renderTicker(data) {
    const track = document.getElementById('ticker-track');
    const items = data.map(s => {
        const pctChange = ((s.change / s.price) * 100).toFixed(2);
        const dir = s.change >= 0 ? 'up' : 'down';
        const arrow = s.change >= 0 ? '&#9650;' : '&#9660;';
        return `<span class="ticker-item">
            <span class="ticker-symbol">${s.symbol}</span>
            <span class="ticker-price">$${s.price.toFixed(2)}</span>
            <span class="ticker-change ${dir}">${arrow} ${s.change >= 0 ? '+' : ''}${s.change.toFixed(2)} (${pctChange}%)</span>
        </span>`;
    }).join('');
    track.innerHTML = items + items;
}

async function initTicker() {
    try {
        const res = await fetch(`${API_BASE}/stocks`);
        const data = await res.json();
        if (data.success && data.stocks && data.stocks.length > 0) {
            renderTicker(data.stocks);
        } else {
            renderTicker(fallbackStockData);
        }
    } catch(e) {
        renderTicker(fallbackStockData);
    }
    // Refresh every 5 minutes
    setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/stocks`);
            const data = await res.json();
            if (data.success && data.stocks && data.stocks.length > 0) {
                renderTicker(data.stocks);
            }
        } catch(e) {}
    }, 300000);
}

// ─── NEWS ─────────────────────────────────────────────────────────────────
const financialNewsData = [
    { source: 'Bloomberg', title: 'Federal Reserve Signals Potential Rate Cut in Coming Months', time: '2h ago', url: 'https://www.bloomberg.com' },
    { source: 'CNBC', title: 'S&P 500 Hits New All-Time High on Strong Earnings Reports', time: '4h ago', url: 'https://www.cnbc.com' },
    { source: 'WSJ', title: 'Corporate M&A Activity Reaches Highest Level Since 2021', time: '5h ago', url: 'https://www.wsj.com' },
    { source: 'MarketWatch', title: 'Small-Cap Stocks Outperform Large-Caps for Third Consecutive Week', time: '7h ago', url: 'https://www.marketwatch.com' },
    { source: 'Bloomberg', title: 'Venture Capital Funding Returns to Growth After Two-Year Slump', time: '8h ago', url: 'https://www.bloomberg.com' },
    { source: 'Financial Times', title: 'Bond Yields Drop as Investors Seek Safe Haven Assets', time: '10h ago', url: 'https://www.ft.com' },
    { source: 'CNBC', title: 'Bank Earnings Beat Expectations Across Major Institutions', time: '11h ago', url: 'https://www.cnbc.com' },
    { source: 'MarketWatch', title: 'Commodity Prices Surge on Supply Chain Disruptions', time: '12h ago', url: 'https://www.marketwatch.com' },
];

const topNewsData = [
    { source: 'Reuters', title: 'Tech Sector Leads Market Rally as AI Investments Surge', time: '3h ago', url: 'https://www.reuters.com' },
    { source: 'Financial Times', title: 'Global Supply Chain Recovery Accelerates in Q4', time: '6h ago', url: 'https://www.ft.com' },
    { source: 'Reuters', title: 'Energy Sector Transformation: Renewables Investment Surpasses Fossil Fuels', time: '9h ago', url: 'https://www.reuters.com' },
    { source: 'AP News', title: 'International Trade Agreements Reshape Global Commerce Landscape', time: '10h ago', url: 'https://apnews.com' },
    { source: 'BBC', title: 'Central Banks Worldwide Coordinate on Inflation Response Strategy', time: '11h ago', url: 'https://www.bbc.com/news' },
    { source: 'Reuters', title: 'Manufacturing Output Rises for Fifth Consecutive Quarter', time: '12h ago', url: 'https://www.reuters.com' },
    { source: 'AP News', title: 'New Regulations Target Big Tech Amid Antitrust Concerns', time: '13h ago', url: 'https://apnews.com' },
    { source: 'BBC', title: 'Climate Summit Produces Landmark Agreement on Carbon Reduction', time: '14h ago', url: 'https://www.bbc.com/news' },
];

// ─── TWEETS DATA ───────────────────────────────────────────────────────────
const tweetsData = {
    politics: [
        { handle: '@PoliticoRyan', text: 'Breaking: New bipartisan infrastructure bill gains momentum with key Senate votes confirmed for next week.', likes: '2.4K', retweets: '891' },
        { handle: '@DCInsider', text: 'White House announces executive order on AI regulation framework. Tech lobbyists respond.', likes: '5.1K', retweets: '2.3K' },
        { handle: '@CapitolWatch', text: 'Congressional committee hearing on data privacy yields heated exchange between lawmakers and tech CEOs.', likes: '1.8K', retweets: '634' },
        { handle: '@PolicyPulse', text: 'New polling data shows shifting voter priorities heading into election season. Economy tops concerns.', likes: '3.2K', retweets: '1.1K' },
        { handle: '@GovTracker', text: 'Federal budget proposal includes major allocation for cybersecurity and digital infrastructure.', likes: '1.5K', retweets: '445' },
        { handle: '@PoliticoRyan', text: 'Trade negotiations with Pacific Rim nations enter critical phase this week.', likes: '2.1K', retweets: '756' },
    ],
    finance: [
        { handle: '@WallStPro', text: 'SPY breaking out above resistance. Volume confirms bullish sentiment. Watch the 510 level.', likes: '8.2K', retweets: '3.1K' },
        { handle: '@FinanceGuru', text: 'Goldman Sachs raises S&P 500 year-end target by 200 points. Cites strong corporate earnings growth.', likes: '4.5K', retweets: '1.8K' },
        { handle: '@MarketMaven', text: 'Yield curve uninversion signals potential shift in economic outlook. Bond traders repositioning.', likes: '3.7K', retweets: '1.2K' },
        { handle: '@OptionFlow', text: 'Massive call sweep on NVDA 950C expiring next month. Unusual activity worth watching.', likes: '6.1K', retweets: '2.5K' },
        { handle: '@BankAnalyst', text: 'Regional bank earnings surprise to the upside. Credit quality improving across the sector.', likes: '2.9K', retweets: '890' },
        { handle: '@WallStPro', text: 'Fed minutes reveal division among members on pace of rate adjustments. Markets react.', likes: '5.3K', retweets: '2.0K' },
    ],
    crypto: [
        { handle: '@CryptoWhale', text: 'Bitcoin reclaims $100K support level. On-chain metrics show accumulation by long-term holders.', likes: '12.5K', retweets: '5.8K' },
        { handle: '@DeFiAlpha', text: 'Ethereum staking yields hit new highs as network activity surges. DeFi TVL crosses $200B.', likes: '7.3K', retweets: '3.2K' },
        { handle: '@AltSeason', text: 'Solana ecosystem sees massive growth in developer activity. New DEX volume records this week.', likes: '9.1K', retweets: '4.1K' },
        { handle: '@BlockInsight', text: 'SEC signals more clarity on crypto regulation framework. Industry leaders cautiously optimistic.', likes: '6.8K', retweets: '2.9K' },
        { handle: '@CryptoWhale', text: 'Institutional BTC inflows reach record levels through spot ETFs. Supply shock incoming?', likes: '15.2K', retweets: '7.1K' },
        { handle: '@DeFiAlpha', text: 'Layer 2 rollups processing more transactions than Ethereum mainnet. Scaling narrative plays out.', likes: '4.6K', retweets: '1.8K' },
    ],
    global: [
        { handle: '@GlobalEcon', text: 'IMF raises global growth forecast citing stronger-than-expected recovery in emerging markets.', likes: '3.4K', retweets: '1.5K' },
        { handle: '@TradeWatch', text: 'EU-Asia trade corridor sees 40% volume increase. Container shipping rates stabilize.', likes: '2.1K', retweets: '780' },
        { handle: '@EmergingMkts', text: 'India overtakes UK as 5th largest economy. FDI inflows accelerate across manufacturing sector.', likes: '5.8K', retweets: '2.4K' },
        { handle: '@MacroView', text: 'Oil prices stabilize as OPEC+ maintains production targets. Energy transition investments continue.', likes: '2.7K', retweets: '920' },
        { handle: '@GlobalEcon', text: 'Japan exits negative interest rate policy. Yen strengthens against major currencies.', likes: '4.2K', retweets: '1.7K' },
        { handle: '@TradeWatch', text: 'Rare earth supply chain diversification efforts gain traction. Australia and Canada expand mining.', likes: '1.9K', retweets: '650' },
    ]
};

let newsScrollIntervals = [];

function renderNewsColumn(containerId, articles) {
    const inner = document.getElementById(containerId);
    if (!inner) return;
    inner.innerHTML = articles.map(n => `
        <div class="news-item" onclick="window.open('${esc(n.url)}', '_blank')" title="Open ${esc(n.source)}">
            <div class="news-source">${esc(n.source)} &#8599;</div>
            <div class="news-title">${esc(n.title)}</div>
            <div class="news-time">${n.time || ''}</div>
        </div>
    `).join('');
}

function renderTweetsColumn(cat, tweets) {
    const inner = document.getElementById(`tweets-${cat}-inner`);
    if (!inner) return;
    inner.innerHTML = tweets.map(t => {
        const link = t.link || ('https://x.com/search?q=' + encodeURIComponent((t.handle || '').replace('@','') + ' ' + (t.text || '').substring(0, 60)));
        return `
        <div class="tweet-item" onclick="window.open('${esc(link)}', '_blank')" title="View on X">
            <div class="tweet-handle">${esc(t.handle)} &#8599;</div>
            <div class="tweet-text">${esc(t.text)}</div>
            <div class="tweet-metrics">
                <span>&#9825; ${t.likes || ''}</span>
                <span>&#8635; ${t.retweets || ''}</span>
            </div>
        </div>`;
    }).join('');
}

async function initNews() {
    // Show skeletons while news loads
    ['financial-news-inner', 'top-news-inner'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = skeletonHTML('lines', 6);
    });

    // Fetch live financial news
    try {
        const finRes = await fetch(`${API_BASE}/news?category=financial`);
        const finData = await finRes.json();
        if (finData.success && finData.articles && finData.articles.length > 0) {
            const articles = finData.articles.map(a => ({
                source: a.source, title: a.title, url: a.url,
                time: a.time ? new Date(a.time).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : ''
            }));
            renderNewsColumn('financial-news-inner', articles);
        } else {
            renderNewsColumn('financial-news-inner', financialNewsData);
        }
    } catch(e) {
        renderNewsColumn('financial-news-inner', financialNewsData);
    }

    // Fetch live top news
    try {
        const topRes = await fetch(`${API_BASE}/news?category=general`);
        const topData = await topRes.json();
        if (topData.success && topData.articles && topData.articles.length > 0) {
            const articles = topData.articles.map(a => ({
                source: a.source, title: a.title, url: a.url,
                time: a.time ? new Date(a.time).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : ''
            }));
            renderNewsColumn('top-news-inner', articles);
        } else {
            renderNewsColumn('top-news-inner', topNewsData);
        }
    } catch(e) {
        renderNewsColumn('top-news-inner', topNewsData);
    }

    // Render hardcoded tweets (Nitter RSS removed — unstable)
    ['politics', 'finance', 'crypto', 'global'].forEach(cat => renderTweetsColumn(cat, tweetsData[cat]));

    // Start auto-scroll
    startNewsAutoScroll();
}

function startNewsAutoScroll() {
    // Clear any existing intervals
    newsScrollIntervals.forEach(id => clearInterval(id));
    newsScrollIntervals = [];

    const scrollConfigs = [
        { innerId: 'financial-news-inner', containerId: 'financial-news-scroll' },
        { innerId: 'top-news-inner', containerId: 'top-news-scroll' },
        { innerId: 'tweets-politics-inner', containerId: 'tweets-politics' },
        { innerId: 'tweets-finance-inner', containerId: 'tweets-finance' },
        { innerId: 'tweets-crypto-inner', containerId: 'tweets-crypto' },
        { innerId: 'tweets-global-inner', containerId: 'tweets-global' },
    ];

    scrollConfigs.forEach((config, idx) => {
        let scrollPos = 0;
        const delay = 5000 + (idx * 500); // Stagger slightly so they don't all scroll at once

        const intervalId = setInterval(() => {
            const inner = document.getElementById(config.innerId);
            const container = document.getElementById(config.containerId);
            if (!inner || !container) return;

            const items = inner.querySelectorAll('.news-item, .tweet-item');
            if (items.length === 0) return;

            const itemHeight = items[0].offsetHeight;
            const maxScroll = inner.scrollHeight - container.offsetHeight;

            scrollPos += itemHeight;
            if (scrollPos >= maxScroll) {
                scrollPos = 0;
            }

            inner.style.transform = `translateY(-${scrollPos}px)`;
        }, delay);

        newsScrollIntervals.push(intervalId);
    });
}

// ─── SCROLL REVEAL ────────────────────────────────────────────────────────
function initScrollReveal() {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
            }
        });
    }, { threshold: 0.1 });

    document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
}

// ─── THE SAUCE - Buy Signal Alerts ──────────────────────────────────────
async function loadSauceAlerts(forceRefresh = false) {
    const grid = document.getElementById('sauce-grid');
    const refreshBtn = document.querySelector('.sauce-refresh-btn');
    if (forceRefresh && refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Scanning...';
    }
    grid.innerHTML = skeletonHTML('card', 3);
    try {
        const url = forceRefresh ? `${API_BASE}/sauce?refresh=1` : `${API_BASE}/sauce`;
        const res = await fetch(url);
        const data = await res.json();
        if (data.success && data.alerts && data.alerts.length > 0) {
            grid.innerHTML = data.alerts.slice(0, 3).map(a => `
                <div class="sauce-card signal-${a.signal_type}" onclick="window.open('${a.source_url}', '_blank')" title="Open source">
                    <div class="sauce-signal-badge">${getSignalIcon(a.signal_type)} ${a.signal_type}</div>
                    <div class="sauce-headline">${esc(a.headline)}</div>
                    <div class="sauce-meta">
                        <span>${esc(a.company)}</span>
                        <span class="sauce-keywords">${esc(a.trigger_keywords)}</span>
                    </div>
                </div>
            `).join('');
        } else {
            grid.innerHTML = '<div class="sauce-empty">No buy signals detected today. Check back tomorrow or hit Refresh.</div>';
        }
    } catch (e) {
        grid.innerHTML = '<div class="sauce-empty">Could not load signals. Backend may be starting up.</div>';
    } finally {
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.textContent = 'Refresh';
        }
    }
}

function getSignalIcon(type) {
    const icons = { funding: '&#128176;', acquisition: '&#129309;', leadership: '&#128100;', expansion: '&#127793;' };
    return icons[type] || '&#128161;';
}

// ─── XP / QUESTING ENGINE ──────────────────────────────────────────────────
async function loadXP() {
    try {
        const res = await fetch(`${API_BASE}/xp`);
        const data = await res.json();
        if (data.success) updateXPDisplay(data);
    } catch {}
}

function updateXPDisplay(data) {
    const trophyIcons = { bronze: '&#127942;', silver: '&#129351;', gold: '&#127941;', diamond: '&#128142;' };
    document.getElementById('xp-trophy').innerHTML = trophyIcons[data.tier] || '&#127942;';
    const nameEl = document.getElementById('xp-level-name');
    nameEl.textContent = `Lv.${data.level} ${data.name}`;
    nameEl.className = `xp-level-name tier-${data.tier}`;
    document.getElementById('xp-total').textContent = `${data.total_xp} XP`;
    document.getElementById('xp-next').textContent = `Next: ${data.next_level_xp} XP`;
    const fill = document.getElementById('xp-bar-fill');
    fill.style.width = `${data.progress}%`;
    fill.className = `xp-bar-fill tier-${data.tier}`;
    if (data.recent_actions && data.recent_actions.length > 0) {
        const recent = data.recent_actions[0];
        document.getElementById('xp-recent-action').textContent = `Last: +${recent.xp_earned}XP ${recent.action.replace(/_/g, ' ')}`;
    }
    // Streak display
    const streakEl = document.getElementById('xp-streak');
    if (streakEl && data.streak) {
        const s = data.streak;
        streakEl.innerHTML = s.current_streak > 0
            ? `<span class="streak-flame">&#128293;</span> ${s.current_streak} day streak`
            : '<span style="color:var(--text-dim);">No streak</span>';
    }
    // Challenge display
    const challengeEl = document.getElementById('xp-challenges');
    if (challengeEl && data.challenges) {
        challengeEl.innerHTML = data.challenges.slice(0, 3).map(ch => {
            const pct = Math.min(100, Math.round((ch.current_count / ch.target_count) * 100));
            return `<div class="challenge-item" style="margin-bottom:4px;">
                <div style="display:flex;justify-content:space-between;font-size:10px;">
                    <span>${esc(ch.title)}${ch.completed ? ' &#9989;' : ''}</span>
                    <span style="color:var(--text-dim);">${ch.current_count}/${ch.target_count} (+${ch.xp_reward}XP)</span>
                </div>
                <div class="challenge-bar"><div class="challenge-bar-fill${ch.completed ? ' completed' : ''}" style="width:${pct}%;"></div></div>
            </div>`;
        }).join('');
    }
}

function showXPPopup(xp, action) {
    if (xp <= 0) return;
    const popup = document.createElement('div');
    popup.className = 'xp-popup';
    popup.innerHTML = `+${xp} XP <span style="color:var(--text-dim);font-size:11px;margin-left:6px;">${action.replace(/_/g, ' ')}</span>`;
    document.body.appendChild(popup);
    setTimeout(() => popup.remove(), 2200);
}

async function awardXP(action, detail = '') {
    try {
        const res = await fetch(`${API_BASE}/xp/award`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ action, detail })
        });
        const data = await res.json();
        if (data.success) {
            showXPPopup(data.xp_earned, action);
            updateXPDisplay(data);
        }
    } catch {}
}

// ─── EMAIL GUESSING ──────────────────────────────────────────────────────
async function guessEmail() {
    const name = document.getElementById('name').value;
    const company = document.getElementById('company').value;
    const source = document.getElementById('source-url').value;
    if (!name) { alert('Enter a name first'); return; }

    try {
        const res = await fetch(`${API_BASE}/guess-email`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ name, company, source_url: source })
        });
        const data = await res.json();
        const container = document.getElementById('email-guess-container');
        if (data.success && data.guesses.length > 0) {
            container.innerHTML = `<div class="email-guess-list">${
                data.guesses.map(g => `<span class="email-guess-chip" onclick="document.getElementById('email').value='${g}';this.parentElement.parentElement.innerHTML='';">${g}</span>`).join('')
            }</div>`;
        } else {
            container.innerHTML = '<span style="font-size:10px;color:var(--text-dim);">Need name + company/URL to guess</span>';
        }
    } catch {
        document.getElementById('email-guess-container').innerHTML = '<span style="font-size:10px;color:var(--red);">Error guessing email</span>';
    }
}

// ─── DUPLICATE DETECTION ─────────────────────────────────────────────────
let duplicateCheckTimeout = null;

function initDuplicateCheck() {
    const nameInput = document.getElementById('name');
    const emailInput = document.getElementById('email');
    const companyInput = document.getElementById('company');

    [nameInput, emailInput, companyInput].forEach(input => {
        input.addEventListener('input', () => {
            clearTimeout(duplicateCheckTimeout);
            duplicateCheckTimeout = setTimeout(checkForDuplicates, 500);
        });
    });
}

async function checkForDuplicates() {
    // Only check when adding new (not editing existing)
    if (editingId) return;

    const name = document.getElementById('name').value.trim();
    const email = document.getElementById('email').value.trim();
    const company = document.getElementById('company').value.trim();

    if (!name && !email) return;

    try {
        const res = await fetch(`${API_BASE}/prospects/check-duplicate`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ name, email, company })
        });
        const data = await res.json();
        const alertEl = document.getElementById('duplicate-alert');
        const matchesEl = document.getElementById('duplicate-matches');

        if (data.success && data.duplicates.length > 0) {
            matchesEl.innerHTML = data.duplicates.map(d => `
                <div class="duplicate-match">
                    <div class="duplicate-match-info">
                        <strong>${esc(d.name)}</strong> - ${esc(d.company || 'No company')}
                        ${d.email ? ` (${esc(d.email)})` : ''}
                    </div>
                    <div style="display:flex;gap:4px;align-items:center;">
                        <span class="duplicate-match-type">${d.match_type.replace(/_/g, ' ')}</span>
                        <button type="button" class="secondary small" style="font-size:9px;padding:2px 6px;" onclick="mergeWithDuplicate('${d.id}')">Merge</button>
                    </div>
                </div>
            `).join('');
            alertEl.style.display = 'block';
        } else {
            alertEl.style.display = 'none';
        }
    } catch {}
}

async function mergeWithDuplicate(existingId) {
    if (!confirm('Merge into existing prospect? The current form data will update the existing record.')) return;
    const prospect = {
        name: document.getElementById('name').value,
        company: document.getElementById('company').value,
        title: document.getElementById('title').value,
        email: document.getElementById('email').value,
        phone: document.getElementById('phone').value,
        linkedin_url: document.getElementById('linkedin-url').value,
        source: document.getElementById('source-url').value,
        notes: document.getElementById('notes').value,
    };
    // Update existing prospect with new data (only non-empty fields)
    const updateData = {};
    for (const [key, val] of Object.entries(prospect)) {
        if (val) updateData[key] = val;
    }
    try {
        const res = await fetch(`${API_BASE}/prospects/${existingId}`, {
            method: 'PUT', headers: {'Content-Type':'application/json'},
            body: JSON.stringify(updateData)
        });
        if (res.ok) {
            closeModal();
            showStatus('Merged with existing prospect', 'success');
            await loadProspects();
        }
    } catch {}
}

// ─── FORUM & AUTH ─────────────────────────────────────────────────────
let currentUser = null;
let selectedAvatar = 'avatar-default';

const AVATAR_EMOJIS = {
    'avatar-default': '&#128100;',
    'avatar-hacker': '&#128187;',
    'avatar-ghost': '&#128123;',
    'avatar-skull': '&#128128;',
    'avatar-robot': '&#129302;',
    'avatar-alien': '&#128125;',
    'avatar-ninja': '&#129399;',
    'avatar-wizard': '&#129497;',
    'avatar-dragon': '&#128009;',
    'avatar-phoenix': '&#128038;',
    'avatar-wolf': '&#128058;',
    'avatar-eagle': '&#129413;'
};

function getAvatarEmoji(avatarId) {
    return AVATAR_EMOJIS[avatarId] || AVATAR_EMOJIS['avatar-default'];
}

function renderAvatarPicker(containerId, currentAvatar) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = Object.entries(AVATAR_EMOJIS).map(([id, emoji]) =>
        `<div class="avatar-option ${id === currentAvatar ? 'selected' : ''}" data-avatar="${id}" onclick="selectAvatar('${containerId}', '${id}')">${emoji}</div>`
    ).join('');
}

function selectAvatar(containerId, avatarId) {
    selectedAvatar = avatarId;
    document.querySelectorAll(`#${containerId} .avatar-option`).forEach(el => {
        el.classList.toggle('selected', el.dataset.avatar === avatarId);
    });
}

async function checkAuth() {
    try {
        const res = await fetch(`${API_BASE}/auth/me`, { credentials: 'same-origin' });
        const data = await res.json();
        if (data.success && data.user) {
            currentUser = data.user;
            if (!chatUsername) {
                chatUsername = currentUser.username;
                localStorage.setItem('chat_username', chatUsername);
            }
        }
    } catch {}
    renderForumAuth();
    loadForumPosts();
}

function renderForumAuth() {
    const authDiv = document.getElementById('forum-auth');
    if (currentUser) {
        authDiv.innerHTML = `
            <span class="user-greeting">${getAvatarEmoji(currentUser.avatar)} ${esc(currentUser.display_name || currentUser.username)}</span>
            <button class="secondary small" onclick="showProfileEditor()">Profile</button>
            <button class="primary small" onclick="showForumCreateForm()">New Post</button>
            <button class="secondary small" onclick="forumLogout()">Logout</button>
        `;
    } else {
        authDiv.innerHTML = `
            <button class="primary small" onclick="showLoginForm()">Login</button>
            <button class="secondary small" onclick="showRegisterForm()">Register</button>
        `;
    }
}

function showLoginForm() {
    hideAuthForms();
    document.getElementById('forum-login-form').style.display = 'block';
}

function showRegisterForm() {
    hideAuthForms();
    renderAvatarPicker('avatar-picker', 'avatar-default');
    selectedAvatar = 'avatar-default';
    document.getElementById('forum-register-form').style.display = 'block';
}

function showProfileEditor() {
    hideAuthForms();
    if (currentUser) {
        document.getElementById('profile-display-name').value = currentUser.display_name || '';
        document.getElementById('profile-signature').value = currentUser.signature || '';
        selectedAvatar = currentUser.avatar || 'avatar-default';
        renderAvatarPicker('profile-avatar-picker', selectedAvatar);
    }
    document.getElementById('forum-profile-editor').style.display = 'block';
}

function hideAuthForms() {
    document.getElementById('forum-login-form').style.display = 'none';
    document.getElementById('forum-register-form').style.display = 'none';
    document.getElementById('forum-profile-editor').style.display = 'none';
    document.getElementById('auth-login-error').textContent = '';
    document.getElementById('auth-reg-error').textContent = '';
}

async function forumLogin() {
    const username = document.getElementById('auth-login-user').value.trim();
    const password = document.getElementById('auth-login-pass').value;
    if (!username || !password) { document.getElementById('auth-login-error').textContent = 'Fill in all fields'; return; }

    try {
        const res = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (data.success) {
            currentUser = data.user;
            chatUsername = currentUser.username;
            localStorage.setItem('chat_username', chatUsername);
            hideAuthForms();
            renderForumAuth();
            loadForumPosts();
        } else {
            document.getElementById('auth-login-error').textContent = data.error || 'Login failed';
        }
    } catch {
        document.getElementById('auth-login-error').textContent = 'Connection error';
    }
}

async function forumRegister() {
    const username = document.getElementById('auth-reg-user').value.trim();
    const email = document.getElementById('auth-reg-email').value.trim();
    const password = document.getElementById('auth-reg-pass').value;
    const display_name = document.getElementById('auth-reg-display').value.trim();
    const signature = document.getElementById('auth-reg-sig').value.trim();

    if (!username || !email || !password) {
        document.getElementById('auth-reg-error').textContent = 'Username, email, and password are required';
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/auth/register`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ username, email, password, display_name, avatar: selectedAvatar, signature })
        });
        const data = await res.json();
        if (data.success) {
            currentUser = data.user;
            chatUsername = currentUser.username;
            localStorage.setItem('chat_username', chatUsername);
            hideAuthForms();
            renderForumAuth();
            loadForumPosts();
        } else {
            document.getElementById('auth-reg-error').textContent = data.error || 'Registration failed';
        }
    } catch {
        document.getElementById('auth-reg-error').textContent = 'Connection error';
    }
}

async function forumLogout() {
    try {
        await fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'same-origin' });
    } catch {}
    currentUser = null;
    renderForumAuth();
    loadForumPosts();
}

async function saveProfile() {
    const display_name = document.getElementById('profile-display-name').value.trim();
    const signature = document.getElementById('profile-signature').value.trim();

    try {
        const res = await fetch(`${API_BASE}/auth/profile`, {
            method: 'PUT', headers: {'Content-Type':'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ display_name, avatar: selectedAvatar, signature })
        });
        const data = await res.json();
        if (data.success) {
            currentUser = data.user;
            hideAuthForms();
            renderForumAuth();
        }
    } catch {}
}

async function loadForumPosts() {
    const container = document.getElementById('forum-post-list');
    try {
        const res = await fetch(`${API_BASE}/forum/posts`, { credentials: 'same-origin' });
        const data = await res.json();
        if (data.success && data.data.length > 0) {
            container.innerHTML = data.data.map(p => `
                <div class="forum-post-card" onclick="viewForumPost(${p.id})">
                    <h3>${esc(p.title)}</h3>
                    <div class="forum-post-meta">
                        <span class="avatar-small">${getAvatarEmoji(p.avatar)}</span>
                        <span>${esc(p.display_name || p.username)}</span>
                        <span>${formatForumTime(p.created_at)}</span>
                        <span class="comment-count">${p.comment_count} comment${p.comment_count !== 1 ? 's' : ''}</span>
                    </div>
                </div>
            `).join('');
        } else {
            container.innerHTML = '<div class="forum-empty">No posts yet. Be the first to start a conversation!</div>';
        }
    } catch {
        container.innerHTML = '<div class="forum-empty">Could not load forum posts.</div>';
    }
}

async function viewForumPost(postId) {
    document.getElementById('forum-post-list').style.display = 'none';
    document.getElementById('forum-create-form').style.display = 'none';
    const detail = document.getElementById('forum-post-detail');
    detail.style.display = 'block';
    detail.innerHTML = '<div class="forum-empty">' + skeletonHTML('lines', 5) + '</div>';

    try {
        const res = await fetch(`${API_BASE}/forum/posts/${postId}`, { credentials: 'same-origin' });
        const data = await res.json();
        if (!data.success) { detail.innerHTML = '<div class="forum-empty">Post not found.</div>'; return; }

        const p = data.post;
        const comments = data.comments;
        detail.innerHTML = `
            <div class="forum-post-detail">
                <button class="secondary small" onclick="showForumList()" style="margin-bottom:12px;">&larr; Back to posts</button>
                <div class="forum-post-full">
                    <h2>${esc(p.title)}</h2>
                    <div class="forum-post-meta" style="margin-bottom:12px;">
                        <span class="avatar-small">${getAvatarEmoji(p.avatar)}</span>
                        <span>${esc(p.display_name || p.username)}</span>
                        <span>${formatForumTime(p.created_at)}</span>
                    </div>
                    <div class="post-body">${esc(p.body)}</div>
                    ${p.signature ? `<div class="post-signature">${esc(p.signature)}</div>` : ''}
                    <div style="display:flex;gap:6px;margin-top:8px;">
                        ${currentUser && (currentUser.id === p.user_id || currentUser.role === 'admin') ? `<button class="secondary small" onclick="editForumPost(${p.id}, '${esc(p.title).replace(/'/g,"\\'")}', '${esc(p.body).replace(/'/g,"\\'")}')">Edit</button><button class="danger small" onclick="deleteForumPost(${p.id})">Delete</button>` : ''}
                        ${currentUser ? `<button class="secondary small" onclick="reportForumPost(${p.id})" style="font-size:10px;">Report</button>` : ''}
                    </div>
                </div>

                <h3 style="font-family:var(--font-mono);font-size:13px;color:var(--green-dim);margin-bottom:12px;">
                    // Comments (${comments.length})
                </h3>

                ${comments.map(c => `
                    <div class="forum-comment">
                        <div class="comment-header">
                            <span class="avatar-small">${getAvatarEmoji(c.avatar)}</span>
                            <span class="comment-author">${esc(c.display_name || c.username)}</span>
                            <span class="comment-time">${formatForumTime(c.created_at)}</span>
                        </div>
                        <div class="comment-body">${esc(c.body)}</div>
                        ${c.signature ? `<div class="comment-signature">${esc(c.signature)}</div>` : ''}
                        <div style="display:flex;gap:4px;margin-top:4px;">
                            ${currentUser && (currentUser.id === c.user_id || currentUser.role === 'admin') ? `<button class="secondary small" style="font-size:9px;padding:2px 6px;" onclick="deleteForumComment(${c.id}, ${postId})">Del</button>` : ''}
                            ${currentUser ? `<button class="secondary small" style="font-size:9px;padding:2px 6px;" onclick="reportForumComment(${c.id})">Report</button>` : ''}
                        </div>
                    </div>
                `).join('')}

                ${currentUser ? `
                    <div style="margin-top:16px;margin-left:20px;">
                        <textarea id="forum-comment-input" placeholder="Write a comment..." rows="3"
                            style="width:100%;background:var(--bg-primary);border:1px solid var(--border);color:var(--text);padding:10px;border-radius:var(--radius);font-family:var(--font-sans);font-size:13px;"></textarea>
                        <button class="primary small" onclick="submitForumComment(${postId})" style="margin-top:6px;">Post Comment</button>
                    </div>
                ` : `
                    <div style="margin-top:16px;margin-left:20px;color:var(--text-dim);font-size:12px;font-family:var(--font-mono);">
                        <a href="#" onclick="event.preventDefault();showLoginForm()" style="color:var(--green);">Login</a> or
                        <a href="#" onclick="event.preventDefault();showRegisterForm()" style="color:var(--green);">Register</a> to comment.
                    </div>
                `}
            </div>
        `;
    } catch {
        detail.innerHTML = '<div class="forum-empty">Error loading post.</div>';
    }
}

function showForumList() {
    document.getElementById('forum-post-detail').style.display = 'none';
    document.getElementById('forum-create-form').style.display = 'none';
    document.getElementById('forum-post-list').style.display = 'block';
    loadForumPosts();
}

function showForumCreateForm() {
    if (!currentUser) { showLoginForm(); return; }
    document.getElementById('forum-post-list').style.display = 'none';
    document.getElementById('forum-post-detail').style.display = 'none';
    document.getElementById('forum-create-form').style.display = 'block';
    document.getElementById('forum-new-title').value = '';
    document.getElementById('forum-new-body').value = '';
}

async function submitForumPost() {
    const title = document.getElementById('forum-new-title').value.trim();
    const body = document.getElementById('forum-new-body').value.trim();
    if (!title || !body) return;

    try {
        const res = await fetch(`${API_BASE}/forum/posts`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ title, body })
        });
        const data = await res.json();
        if (data.success) {
            showForumList();
        }
    } catch {}
}

async function submitForumComment(postId) {
    const input = document.getElementById('forum-comment-input');
    const body = input.value.trim();
    if (!body) return;

    try {
        const res = await fetch(`${API_BASE}/forum/posts/${postId}/comments`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({ body })
        });
        const data = await res.json();
        if (data.success) {
            viewForumPost(postId);
        }
    } catch {}
}

async function editForumPost(postId, title, body) {
    const newTitle = prompt('Edit title:', title);
    if (newTitle === null) return;
    const newBody = prompt('Edit body:', body);
    if (newBody === null) return;
    try {
        await fetch(`${API_BASE}/forum/posts/${postId}`, {
            method: 'PUT', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ title: newTitle, body: newBody })
        });
        viewForumPost(postId);
    } catch {}
}

async function deleteForumPost(postId) {
    if (!confirm('Delete this post?')) return;
    try {
        await fetch(`${API_BASE}/forum/posts/${postId}`, { method: 'DELETE' });
        showForumList();
        loadForumPosts();
    } catch {}
}

async function deleteForumComment(commentId, postId) {
    if (!confirm('Delete this comment?')) return;
    try {
        await fetch(`${API_BASE}/forum/comments/${commentId}`, { method: 'DELETE' });
        viewForumPost(postId);
    } catch {}
}

async function reportForumPost(postId) {
    const reason = prompt('Reason for reporting:');
    if (!reason) return;
    try {
        await fetch(`${API_BASE}/forum/posts/${postId}/report`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ reason })
        });
        showStatus('Post reported', 'success');
    } catch {}
}

async function reportForumComment(commentId) {
    const reason = prompt('Reason for reporting:');
    if (!reason) return;
    try {
        await fetch(`${API_BASE}/forum/comments/${commentId}/report`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ reason })
        });
        showStatus('Comment reported', 'success');
    } catch {}
}

function formatForumTime(isoString) {
    if (!isoString) return '';
    const diff = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    const hours = Math.floor(mins / 60);
    if (hours < 24) return hours + 'h ago';
    const days = Math.floor(hours / 24);
    if (days < 30) return days + 'd ago';
    return new Date(isoString).toLocaleDateString();
}

// ─── INPUT SANITIZATION ─────────────────────────────────────────────────
function sanitizeInput(value) {
    if (typeof value !== 'string') return value;
    return value
        .replace(/[\u200B-\u200D\uFEFF]/g, '')
        .replace(/\u00A0/g, ' ')
        .replace(/[\u2000-\u200A]/g, ' ')
        .replace(/\r\n/g, ' ')
        .replace(/[\r\n]/g, ' ')
        .replace(/\s{2,}/g, ' ')
        .trim();
}

function initPasteSanitization() {
    document.addEventListener('paste', (e) => {
        const target = e.target;
        if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') {
            setTimeout(() => {
                target.value = sanitizeInput(target.value);
            }, 0);
        }
    });

    document.addEventListener('blur', (e) => {
        const target = e.target;
        if ((target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') && target.type !== 'file') {
            target.value = sanitizeInput(target.value);
        }
    }, true);
}

// ─── INIT ─────────────────────────────────────────────────────────────────
async function init() {
    checkSession();
    initTicker();
    initNews();
    initScrollReveal();
    initFooterMatrix();
    initProspectSearch();
    initPasteSanitization();
    initDuplicateCheck();
    checkAuth();
    loadProspects();
    loadAllTasks();
    loadSauceAlerts();
    loadXP();
    initChat();

    document.getElementById('add-prospect-btn').addEventListener('click', openModal);
    document.getElementById('prospect-form').addEventListener('submit', handleFormSubmit);
    document.getElementById('csv-import-input').addEventListener('change', handleCSVImport);

    document.querySelectorAll('input[name="crawlType"]').forEach(radio => {
        radio.addEventListener('change', handleCrawlTypeChange);
    });

    document.getElementById('search-input').addEventListener('keyup', (e) => {
        if (e.key === 'Enter') searchProspects();
    });

    document.getElementById('chat-msg-input').addEventListener('keyup', (e) => {
        if (e.key === 'Enter') sendChatMessage();
    });

    document.getElementById('chat-username-input').addEventListener('keyup', (e) => {
        if (e.key === 'Enter') setChatUsername();
    });

    // Animate stat counters
    setTimeout(() => {
        document.querySelectorAll('.stat-card').forEach((card, i) => {
            card.style.animationDelay = `${i * 0.1}s`;
        });
    }, 300);
}

// ─── FOOTER MATRIX EFFECT ────────────────────────────────────────────────
function initFooterMatrix() {
    const canvas = document.getElementById('footer-matrix-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    function resize() {
        canvas.width = canvas.parentElement.offsetWidth;
        canvas.height = canvas.parentElement.offsetHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    const chars = '01001101010110100101';
    const fontSize = 10;
    const columns = Math.floor(canvas.width / fontSize);
    const drops = Array(columns).fill(1);

    function draw() {
        ctx.fillStyle = 'rgba(10, 10, 10, 0.15)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#00ff41';
        ctx.font = fontSize + 'px monospace';

        for (let i = 0; i < drops.length; i++) {
            const text = chars[Math.floor(Math.random() * chars.length)];
            ctx.fillText(text, i * fontSize, drops[i] * fontSize);

            if (drops[i] * fontSize > canvas.height && Math.random() > 0.95) {
                drops[i] = 0;
            }
            drops[i]++;
        }
    }

    setInterval(draw, 80);
}

// ─── SEARCHABLE PROSPECT DROPDOWN ────────────────────────────────────────
function initProspectSearch() {
    const searchInput = document.getElementById('new-task-prospect-search');
    const dropdown = document.getElementById('prospect-search-dropdown');
    const hiddenInput = document.getElementById('new-task-prospect');

    searchInput.addEventListener('focus', () => {
        showProspectDropdown('');
    });

    searchInput.addEventListener('input', () => {
        showProspectDropdown(searchInput.value.trim().toLowerCase());
    });

    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            dropdown.classList.remove('active');
        }
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest('.prospect-search-wrapper')) {
            dropdown.classList.remove('active');
        }
    });
}

function showProspectDropdown(query) {
    const dropdown = document.getElementById('prospect-search-dropdown');
    let options = [{ id: '', name: 'General', company: '' }];

    prospects.forEach(p => {
        options.push({ id: p.id, name: p.name, company: p.company });
    });

    if (query) {
        options = options.filter(o =>
            o.name.toLowerCase().includes(query) ||
            o.company.toLowerCase().includes(query)
        );
    }

    if (options.length === 0) {
        dropdown.innerHTML = '<div class="prospect-search-option" style="color:var(--text-dim);cursor:default;">No matches found</div>';
    } else {
        dropdown.innerHTML = options.map(o => {
            if (o.id === '') {
                return `<div class="prospect-search-option general-option" data-id="" data-name="General" onclick="selectProspectForTask(this)">General (No specific prospect)</div>`;
            }
            return `<div class="prospect-search-option" data-id="${o.id}" data-name="${esc(o.name)}" onclick="selectProspectForTask(this)">
                ${esc(o.name)} <span class="option-company">${esc(o.company)}</span>
            </div>`;
        }).join('');
    }

    dropdown.classList.add('active');
}

function selectProspectForTask(el) {
    const id = el.dataset.id;
    const name = el.dataset.name;
    document.getElementById('new-task-prospect').value = id;
    document.getElementById('new-task-prospect-search').value = name;
    document.getElementById('prospect-search-dropdown').classList.remove('active');
}

function handleCrawlTypeChange(e) {
    document.getElementById('crawl-limit-group').style.display = e.target.value === 'crawl' ? 'block' : 'none';
}

// ─── LOAD PROSPECTS ───────────────────────────────────────────────────────
let currentPage = 1;
let totalPages = 1;
let totalProspects = 0;
let currentSearchQ = '';

async function loadProspects(page = 1, append = false) {
    try {
        let url = `${API_BASE}/prospects?page=${page}&per_page=50`;
        if (currentFilter && currentFilter !== 'all') url += `&status=${currentFilter}`;
        if (currentSearchQ) url += `&q=${encodeURIComponent(currentSearchQ)}`;
        const response = await fetch(url);
        const data = await response.json();
        if (data.success) {
            if (append) {
                prospects = prospects.concat(data.data);
            } else {
                prospects = data.data;
            }
            currentPage = data.page;
            totalPages = data.total_pages;
            totalProspects = data.total;
            displayProspects();
            updateStats();
            updateTaskProspectDropdown();
        }
    } catch (error) {
        console.error('Error loading prospects:', error);
    }
}

// ─── DISPLAY PROSPECTS ────────────────────────────────────────────────────
function displayProspects() {
    const container = document.getElementById('prospects-container');

    if (prospects.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">&#9678;</div>
                <h3>No prospects found</h3>
                <p>${currentFilter === 'all' ? 'Add your first prospect or scan a website to discover leads' : 'No prospects in this stage'}</p>
            </div>`;
        return;
    }

    let html = prospects.map(p => {
        const warmth = p.warmth_score || 0;
        const warmthClass = warmth >= 70 ? 'warmth-hot' : warmth >= 40 ? 'warmth-warm' : 'warmth-cold';
        const staleClass = p.is_stale ? ' stale' : '';
        return `
        <div class="prospect-card ${p.source ? 'crawled' : ''}${staleClass}" onclick="openDrawer('${p.id}')">
            <div class="warmth-dot ${warmthClass}" title="Warmth: ${warmth}"></div>
            <div class="prospect-info">
                <h3>${esc(p.name)}${p.is_stale ? ' <span class="stale-badge">OVERDUE ' + p.days_in_status + 'd</span>' : ''}</h3>
                <p><strong>${esc(p.company)}</strong>${p.title ? ' &middot; ' + esc(p.title) : ''}</p>
                <div class="prospect-meta">
                    <span class="stage-badge stage-${p.status}">${p.status}</span>
                    <span class="prospect-value">$${(p.deal_size || 0).toLocaleString()}</span>
                    ${p.source ? `<span class="source-badge">${extractDomain(p.source)}</span>` : ''}
                    ${p.linkedin_url ? '<span style="color:var(--blue);">in</span>' : ''}
                </div>
            </div>
            <div class="prospect-actions" onclick="event.stopPropagation()">
                <button class="secondary small" onclick="editProspect('${p.id}')">Edit</button>
                <button class="danger small" onclick="deleteProspect('${p.id}')">Del</button>
            </div>
        </div>`;
    }).join('');

    if (currentPage < totalPages) {
        html += `<div class="load-more-container" style="text-align:center;padding:16px;">
            <button class="secondary" onclick="loadProspects(${currentPage + 1}, true)" style="font-family:var(--font-mono);font-size:12px;">
                Load More (${prospects.length} of ${totalProspects})
            </button>
        </div>`;
    }

    container.innerHTML = html;
    updateBulkActionsBar();
}

function esc(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function extractDomain(url) {
    try { return new URL(url).hostname.replace('www.', ''); }
    catch { return 'web'; }
}

function extractCompanyFromUrl(url) {
    try {
        let d = new URL(url).hostname.replace('www.','').split('.')[0];
        return d.charAt(0).toUpperCase() + d.slice(1);
    } catch { return null; }
}

// ─── SELECTION ────────────────────────────────────────────────────────────
function toggleProspectSelection(id, checked) {
    if (checked) selectedProspectsForBulkAdd.add(id);
    else selectedProspectsForBulkAdd.delete(id);
    updateBulkActionsBar();
    displayProspects();
}

function updateBulkActionsBar() {
    const bar = document.getElementById('bulk-actions');
    document.getElementById('selected-count').textContent = selectedProspectsForBulkAdd.size;
    bar.classList.toggle('active', selectedProspectsForBulkAdd.size > 0);
}

function clearSelection() {
    selectedProspectsForBulkAdd.clear();
    updateBulkActionsBar();
    displayProspects();
}

async function addSelectedProspects() {
    if (selectedProspectsForBulkAdd.size === 0) return;
    const ids = Array.from(selectedProspectsForBulkAdd);
    let ok = 0;
    for (const id of ids) {
        const p = crawledProspectsCache.find(x => x._temp_id === id);
        if (!p) continue;
        try {
            const res = await fetch(`${API_BASE}/prospects`, {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify(p)
            });
            if (res.ok) ok++;
        } catch {}
    }
    if (ok > 0) {
        showStatus(`Added ${ok} prospect${ok !== 1 ? 's' : ''} to pipeline`, 'success');
        // Clear crawled cache and hide bulk actions
        crawledProspectsCache = [];
        selectedProspectsForBulkAdd.clear();
        updateBulkActionsBar();
        // Reset crawl URL input
        document.getElementById('crawl-url').value = '';
        // Reload normal prospect view (hides crawled cards)
        await loadProspects();
    }
}

// ─── FILTER / SEARCH ──────────────────────────────────────────────────────
function filterProspects(status, el) {
    currentFilter = status;
    currentPage = 1;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    if (el) el.classList.add('active');
    loadProspects(1);
}

let searchDebounceTimer = null;
function searchProspects() {
    const q = document.getElementById('search-input').value.trim();
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
        currentSearchQ = q;
        currentPage = 1;
        loadProspects(1);
    }, 300);
}

// ─── KANBAN VIEW ─────────────────────────────────────────────────────────
let currentView = 'list';
const KANBAN_STAGES = ['lead', 'contacted', 'qualified', 'proposal', 'won', 'lost'];
const KANBAN_CARD_LIMIT = 20;

function setView(view) {
    currentView = view;
    document.getElementById('list-view-btn').classList.toggle('active', view === 'list');
    document.getElementById('kanban-view-btn').classList.toggle('active', view === 'kanban');
    document.getElementById('prospects-container').style.display = view === 'list' ? '' : 'none';
    document.getElementById('kanban-board').style.display = view === 'kanban' ? 'grid' : 'none';
    document.getElementById('filter-tabs').style.display = view === 'list' ? '' : 'none';
    if (view === 'kanban') renderKanban();
}

async function loadKanbanProspects() {
    try {
        const res = await fetch(`${API_BASE}/prospects?per_page=200`);
        const data = await res.json();
        if (data.success) return data.data;
    } catch(e) { console.error('Kanban load error', e); }
    return prospects;
}

async function renderKanban() {
    const allP = await loadKanbanProspects();
    KANBAN_STAGES.forEach(stage => {
        const container = document.getElementById(`kanban-${stage}`);
        const stageProspects = allP.filter(p => p.status === stage);
        document.getElementById(`kanban-count-${stage}`).textContent = stageProspects.length;

        const visible = stageProspects.slice(0, KANBAN_CARD_LIMIT);
        const hidden = stageProspects.length - visible.length;

        container.innerHTML = visible.map(p => `
            <div class="kanban-card${p.is_stale ? ' stale' : ''}" data-id="${p.id}" onclick="openDrawer('${p.id}')">
                <div class="kc-name">${esc(p.name)}${p.is_stale ? ' <span class="stale-badge">OVERDUE</span>' : ''}</div>
                <div class="kc-company">${esc(p.company || '')}</div>
                <div class="kc-value">$${(p.deal_size || 0).toLocaleString()}</div>
            </div>
        `).join('') + (hidden > 0 ? `<div class="kanban-show-more" onclick="expandKanbanColumn('${stage}')">+ ${hidden} more</div>` : '');

        if (typeof Sortable !== 'undefined') {
            new Sortable(container, {
                group: 'kanban',
                animation: 150,
                ghostClass: 'sortable-ghost',
                filter: '.kanban-show-more',
                onEnd: async function(evt) {
                    const prospectId = evt.item.dataset.id;
                    const newStatus = evt.to.closest('.kanban-column').dataset.status;
                    const oldStatus = evt.from.closest('.kanban-column').dataset.status;
                    if (newStatus === oldStatus) return;
                    await fetch(`${API_BASE}/prospects/${prospectId}`, {
                        method: 'PUT',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ status: newStatus })
                    });
                    const p = allP.find(x => x.id === prospectId);
                    if (p) p.status = newStatus;
                    KANBAN_STAGES.forEach(s => {
                        document.getElementById(`kanban-count-${s}`).textContent =
                            allP.filter(x => x.status === s).length;
                    });
                    awardXP('status_' + (oldStatus === 'lead' ? 'lead_to_contacted' : 'to_' + newStatus), prospectId);
                }
            });
        }
    });
}

function expandKanbanColumn(stage) {
    // Reload without limit
    loadKanbanProspects().then(allP => {
        const container = document.getElementById(`kanban-${stage}`);
        const stageProspects = allP.filter(p => p.status === stage);
        container.innerHTML = stageProspects.map(p => `
            <div class="kanban-card" data-id="${p.id}" onclick="openDrawer('${p.id}')">
                <div class="kc-name">${esc(p.name)}</div>
                <div class="kc-company">${esc(p.company || '')}</div>
                <div class="kc-value">$${(p.deal_size || 0).toLocaleString()}</div>
            </div>
        `).join('');
    });
}

// ─── ANALYTICS ────────────────────────────────────────────────────────────
let chartInstances = {};
let analyticsExpanded = false;

function toggleAnalytics() {
    const grid = document.getElementById('charts-grid');
    const btn = document.getElementById('analytics-toggle-btn');
    analyticsExpanded = !analyticsExpanded;
    grid.style.display = analyticsExpanded ? 'grid' : 'none';
    btn.textContent = analyticsExpanded ? 'Collapse' : 'Run Analytics';
    if (analyticsExpanded) loadAnalytics();
}

async function loadAnalytics() {
    try {
        const res = await fetch(`${API_BASE}/analytics`);
        const data = await res.json();
        if (!data.success) return;

        const chartDefaults = {
            color: '#888',
            borderColor: '#1e1e1e',
        };

        // Destroy old charts
        Object.values(chartInstances).forEach(c => c.destroy());
        chartInstances = {};

        // Pipeline Funnel
        const stages = ['lead', 'contacted', 'qualified', 'proposal', 'won', 'lost'];
        chartInstances.funnel = new Chart(document.getElementById('funnel-chart'), {
            type: 'bar',
            data: {
                labels: stages.map(s => s.charAt(0).toUpperCase() + s.slice(1)),
                datasets: [{
                    data: stages.map(s => (data.pipeline[s] || {}).count || 0),
                    backgroundColor: 'rgba(0, 255, 65, 0.25)',
                    borderColor: '#00ff41',
                    borderWidth: 1
                }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#888' }, grid: { color: '#1a1a1a' } },
                    y: { ticks: { color: '#ccc', font: { family: 'JetBrains Mono', size: 10 } }, grid: { display: false } }
                }
            }
        });

        // Timeline
        if (data.timeline.length > 0) {
            chartInstances.timeline = new Chart(document.getElementById('timeline-chart'), {
                type: 'line',
                data: {
                    labels: data.timeline.map(t => t.day ? t.day.slice(5) : ''),
                    datasets: [{
                        label: 'New Prospects',
                        data: data.timeline.map(t => t.count),
                        borderColor: '#00ff41',
                        backgroundColor: 'rgba(0, 255, 65, 0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 3,
                        pointBackgroundColor: '#00ff41'
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { ticks: { color: '#888', font: { size: 9 } }, grid: { color: '#1a1a1a' } },
                        y: { ticks: { color: '#888' }, grid: { color: '#1a1a1a' }, beginAtZero: true }
                    }
                }
            });
        }

        // Conversions
        const convLabels = Object.keys(data.conversions);
        const convValues = Object.values(data.conversions);
        if (convLabels.length > 0) {
            chartInstances.conversion = new Chart(document.getElementById('conversion-chart'), {
                type: 'doughnut',
                data: {
                    labels: convLabels.map(s => s.charAt(0).toUpperCase() + s.slice(1)),
                    datasets: [{
                        data: convValues,
                        backgroundColor: ['#00cc33', '#00ff41', '#33ff66', '#66ff99'],
                        borderColor: '#0a0a0a',
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { labels: { color: '#ccc', font: { family: 'JetBrains Mono', size: 10 } } }
                    }
                }
            });
        }

        // Activity
        if (data.activity.length > 0) {
            chartInstances.activity = new Chart(document.getElementById('activity-chart'), {
                type: 'bar',
                data: {
                    labels: data.activity.map(a => a.day ? a.day.slice(5) : ''),
                    datasets: [{
                        label: 'Actions',
                        data: data.activity.map(a => a.actions),
                        backgroundColor: 'rgba(0, 255, 65, 0.3)',
                        borderColor: '#00ff41',
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { ticks: { color: '#888', font: { size: 9 } }, grid: { color: '#1a1a1a' } },
                        y: { ticks: { color: '#888' }, grid: { color: '#1a1a1a' }, beginAtZero: true }
                    }
                }
            });
        }
    } catch(e) {
        console.error('Analytics error:', e);
    }
}

// ─── FIRECRAWL ────────────────────────────────────────────────────────────
async function runFirecrawl() {
    const url = document.getElementById('crawl-url').value.trim();
    const type = document.querySelector('input[name="crawlType"]:checked').value;
    const limit = parseInt(document.getElementById('crawl-limit').value);

    if (!url) { showStatus('Enter a URL', 'error'); return; }
    try { new URL(url); } catch { showStatus('Invalid URL (include https://)', 'error'); return; }

    const btn = document.getElementById('crawl-btn');
    btn.disabled = true;
    btn.textContent = 'SCANNING...';
    showStatus(type === 'scrape' ? 'Scraping page...' : 'Crawling website (this may take a moment)...', 'loading');

    try {
        const payload = { url, type };
        if (type === 'crawl') payload.limit = limit;

        const res = await fetch(`${API_BASE}/search`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.success) {
            const count = data.prospects ? data.prospects.length : 0;
            showStatus(count > 0 ? `Found ${count} prospects - select to add` : 'No prospects detected. Try a different URL.', count > 0 ? 'success' : 'warning');
            displayCrawledProspects(data.prospects || [], url);
        } else {
            showStatus(`Error: ${data.error}`, 'error');
        }
    } catch (error) {
        showStatus('Connection error. Check backend.', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'SCAN';
    }
}

function displayCrawledProspects(crawledProspects, sourceUrl) {
    const container = document.getElementById('prospects-container');
    crawledProspectsCache = [];
    selectedProspectsForBulkAdd.clear();

    if (!crawledProspects.length) {
        container.innerHTML = `<div class="empty-state"><div class="empty-state-icon">&#8709;</div><h3>No prospects detected</h3><p>Try a different URL or add manually</p></div>`;
        return;
    }

    container.innerHTML = crawledProspects.map((p, idx) => {
        const tempId = `crawled_${idx}`;
        let company = p.company || 'Unknown Company';
        if (company === 'Unknown' || company === 'Unknown Company') {
            company = extractCompanyFromUrl(p.source || sourceUrl) || company;
        }
        const confidence = p.confidence || 0;
        const confClass = confidence >= 70 ? 'confidence-high' : confidence >= 40 ? 'confidence-med' : 'confidence-low';
        const confLabel = confidence >= 70 ? 'High' : confidence >= 40 ? 'Medium' : 'Low';
        const autoChecked = confidence >= 70;
        if (autoChecked) selectedProspectsForBulkAdd.add(tempId);
        const pd = { ...p, company, status: 'lead', deal_size: 0, _temp_id: tempId };
        crawledProspectsCache.push(pd);
        return `
        <div class="prospect-card crawled">
            <input type="checkbox" class="prospect-checkbox" ${autoChecked ? 'checked' : ''} onchange="toggleCrawledProspectSelection('${tempId}', this.checked)" />
            <div class="warmth-dot warmth-cold"></div>
            <div class="prospect-info">
                <h3>${esc(p.name || 'Unknown')}</h3>
                <p><strong>${esc(company)}</strong>${p.title ? ' &middot; ' + esc(p.title) : ''}</p>
                ${p.email ? `<p style="color:var(--text-dim);font-size:11px;">${esc(p.email)}</p>` : ''}
                <div class="prospect-meta">
                    <span class="confidence-badge ${confClass}" title="Confidence: ${confidence}%">${confLabel} (${confidence}%)</span>
                    <span class="source-badge">${extractDomain(p.source || sourceUrl)}</span>
                    ${p.linkedin_url ? '<span style="color:var(--blue);">in</span>' : ''}
                </div>
            </div>
            <div class="prospect-actions">
                <button class="secondary small" onclick="editCrawledProspect(${idx})">Edit</button>
            </div>
        </div>`;
    }).join('');
    updateBulkActionsBar();
}

function toggleCrawledProspectSelection(tempId, checked) {
    if (checked) selectedProspectsForBulkAdd.add(tempId);
    else selectedProspectsForBulkAdd.delete(tempId);
    updateBulkActionsBar();
}

function editCrawledProspect(idx) {
    const p = crawledProspectsCache[idx];
    editingId = null;
    document.getElementById('modal-title').textContent = 'Edit Prospect';
    document.getElementById('name').value = p.name || '';
    document.getElementById('company').value = p.company || '';
    document.getElementById('title').value = p.title || '';
    document.getElementById('email').value = p.email || '';
    document.getElementById('status').value = 'lead';
    document.getElementById('deal-size').value = 0;
    document.getElementById('linkedin-url').value = p.linkedin_url || '';
    document.getElementById('source-url').value = p.source || '';
    document.getElementById('notes').value = '';
    document.getElementById('prospect-form').dataset.crawledIdx = idx;
    const modal = document.getElementById('prospect-modal');
    modal.style.display = 'flex';
    modal.offsetHeight;
    modal.classList.add('active');
}

function displayUrlMap(urls) {
    const container = document.getElementById('prospects-container');
    container.innerHTML = `
        <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;">
            <h3 style="font-family:var(--font-mono);font-size:13px;color:var(--green-dim);margin-bottom:12px;">SITE MAP (${urls.length} pages)</h3>
            <p style="font-size:12px;color:var(--text-dim);margin-bottom:12px;">Copy a URL and use "Scrape Page" mode to extract prospects</p>
            <div style="max-height:400px;overflow-y:auto;background:var(--bg-primary);padding:12px;border-radius:var(--radius);font-size:11px;font-family:var(--font-mono);">
                ${urls.map(u => `<div style="padding:4px 0;border-bottom:1px solid var(--border);color:var(--green-dim);">${esc(u)}</div>`).join('')}
            </div>
        </div>`;
}

function showStatus(msg, type = 'info') {
    const el = document.getElementById('crawl-status');
    el.textContent = msg;
    el.className = 'crawl-status active';
    if (type === 'success') setTimeout(() => el.classList.remove('active'), 5000);
}

// ─── SIDE DRAWER ──────────────────────────────────────────────────────────
function openDrawer(prospectId) {
    const p = prospects.find(x => x.id === prospectId);
    if (!p) return;

    document.getElementById('drawer-prospect-name').textContent = p.name;
    const body = document.getElementById('drawer-body');
    const warmth = p.warmth_score || 0;
    const wColor = warmth >= 70 ? 'var(--green)' : warmth >= 40 ? 'var(--yellow)' : '#555';
    const wLabel = warmth >= 70 ? 'HOT' : warmth >= 40 ? 'WARM' : 'COLD';

    body.innerHTML = `
        <!-- Details -->
        <div class="drawer-section">
            <div class="drawer-section-title">Prospect Details</div>
            <div class="drawer-detail"><span class="drawer-detail-label">Company</span><span class="drawer-detail-value">${esc(p.company)}</span></div>
            <div class="drawer-detail"><span class="drawer-detail-label">Title</span><span class="drawer-detail-value">${esc(p.title)}</span></div>
            <div class="drawer-detail"><span class="drawer-detail-label">Email</span><span class="drawer-detail-value">${esc(p.email || 'N/A')}</span></div>
            <div class="drawer-detail"><span class="drawer-detail-label">Phone</span><span class="drawer-detail-value">${p.phone ? esc(p.phone) : 'N/A'}</span></div>
            <div class="drawer-detail"><span class="drawer-detail-label">Status</span><span class="drawer-detail-value"><span class="stage-badge stage-${p.status}">${p.status}</span></span></div>
            <div class="drawer-detail"><span class="drawer-detail-label">Deal Size</span><span class="drawer-detail-value prospect-value">$${(p.deal_size||0).toLocaleString()}</span></div>
            ${p.linkedin_url ? `<div class="drawer-detail"><span class="drawer-detail-label">LinkedIn</span><span class="drawer-detail-value"><a href="${esc(p.linkedin_url)}" target="_blank" style="color:var(--blue);">Profile</a></span></div>` : ''}
            ${p.source ? `<div class="drawer-detail"><span class="drawer-detail-label">Source</span><span class="drawer-detail-value">${extractDomain(p.source)}</span></div>` : ''}
            ${p.notes ? `<div class="drawer-detail"><span class="drawer-detail-label">Notes</span><span class="drawer-detail-value">${esc(p.notes)}</span></div>` : ''}
        </div>

        <!-- Outreach -->
        <div class="drawer-section">
            <div class="drawer-section-title">Outreach</div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
                ${p.email ? `<a href="mailto:${esc(p.email)}?subject=${encodeURIComponent('Following Up - ' + (p.company || ''))}" class="primary small" style="text-decoration:none;display:inline-flex;align-items:center;gap:4px;padding:6px 12px;background:linear-gradient(135deg,var(--green-dark),#006622);color:var(--bg-primary);border:1px solid var(--green-dim);border-radius:var(--radius);font-size:11px;font-weight:600;">&#9993; Send Email</a>` : '<span style="color:var(--text-dim);font-size:11px;">No email - add one to enable outreach</span>'}
                ${p.phone ? `<a href="tel:${esc(p.phone)}" class="secondary small" style="text-decoration:none;display:inline-flex;align-items:center;gap:4px;padding:6px 12px;background:var(--bg-card);color:var(--text);border:1px solid var(--border);border-radius:var(--radius);font-size:11px;">&#9742; Call</a>` : ''}
                ${p.linkedin_url ? `<a href="${esc(p.linkedin_url)}" target="_blank" class="secondary small" style="text-decoration:none;display:inline-flex;align-items:center;gap:4px;padding:6px 12px;background:var(--bg-card);color:var(--blue);border:1px solid var(--border);border-radius:var(--radius);font-size:11px;">in Message</a>` : ''}
            </div>
        </div>

        <!-- Warmth -->
        <div class="drawer-section">
            <div class="drawer-section-title">Lead Warmth</div>
            <div style="display:flex;justify-content:space-between;align-items:center;font-size:13px;">
                <span style="color:${wColor};font-family:var(--font-mono);font-weight:600;">${warmth}/100 ${wLabel}</span>
            </div>
            <div class="warmth-bar">
                <div class="warmth-bar-fill" style="width:${warmth}%;background:${wColor};"></div>
            </div>
        </div>

        <!-- AI Icebreaker -->
        <div class="drawer-section">
            <div class="drawer-section-title">AI Icebreaker</div>
            <button class="primary small" onclick="generateIcebreaker('${p.id}')" id="icebreaker-btn-${p.id}">Generate Icebreaker</button>
            <div id="icebreaker-results-${p.id}"></div>
        </div>

        <!-- Enrichment -->
        <div class="drawer-section">
            <div class="drawer-section-title">Contact Enrichment</div>
            <button class="secondary small" onclick="enrichContact('${p.id}')" id="enrich-btn-${p.id}">Enrich Contact</button>
            <div id="enrich-results-${p.id}"></div>
        </div>

        <!-- Sequences -->
        <div class="drawer-section">
            <div class="drawer-section-title">Email Sequences</div>
            <button class="secondary small" onclick="showSequenceEnroll('${p.id}')" style="margin-bottom:8px;">Assign Sequence</button>
            <div id="drawer-sequences-${p.id}">${skeletonHTML('line', 2)}</div>
        </div>

        <!-- Tasks -->
        <div class="drawer-section">
            <div class="drawer-section-title">Tasks & Reminders</div>
            <button class="secondary small" onclick="openTaskModal('${p.id}')" style="margin-bottom:8px;">+ Add Task</button>
            <div id="drawer-tasks-${p.id}">${skeletonHTML('card', 2)}</div>
        </div>

        <!-- Activity Timeline -->
        <div class="drawer-section">
            <div class="drawer-section-title">Activity Timeline</div>
            <div id="drawer-timeline-${p.id}">${skeletonHTML('lines', 4)}</div>
        </div>
    `;

    document.getElementById('drawer-overlay').classList.add('active');
    document.getElementById('side-drawer').classList.add('active');

    loadDrawerTasks(p.id);
    loadDrawerTimeline(p.id);
    loadDrawerSequences(p.id);
}

function closeDrawer() {
    document.getElementById('drawer-overlay').classList.remove('active');
    document.getElementById('side-drawer').classList.remove('active');
}

// ─── DRAWER: Activity Timeline ────────────────────────────────────────────
async function loadDrawerTimeline(prospectId) {
    const container = document.getElementById(`drawer-timeline-${prospectId}`);
    if (!container) return;
    try {
        const res = await fetch(`${API_BASE}/prospects/${prospectId}/activity`);
        const data = await res.json();
        if (data.success && data.data.length > 0) {
            const EVENT_ICONS = { created: '&#10024;', status_change: '&#8644;', task_completed: '&#9989;', task_created: '&#9744;',
                                  enriched: '&#128269;', sequence_enrolled: '&#9993;', note: '&#128221;' };
            container.innerHTML = '<div class="timeline">' + data.data.slice(0, 20).map(e => {
                const icon = EVENT_ICONS[e.event_type] || '&#8226;';
                const timeAgo = formatTimeAgo(e.created_at);
                return `<div class="timeline-event">
                    <span class="timeline-icon">${icon}</span>
                    <div class="timeline-content">
                        <div class="timeline-event-desc">${esc(e.description || e.event_type)}</div>
                        <div class="timeline-event-time">${timeAgo}</div>
                    </div>
                </div>`;
            }).join('') + '</div>';
        } else {
            container.innerHTML = '<p style="font-size:11px;color:var(--text-dim);">No activity yet</p>';
        }
    } catch { container.innerHTML = '<p style="font-size:11px;color:var(--text-dim);">Could not load timeline</p>'; }
}

function formatTimeAgo(isoStr) {
    if (!isoStr) return '';
    const d = new Date(isoStr);
    const now = new Date();
    const diff = Math.floor((now - d) / 1000);
    if (diff < 60) return 'Just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
    return d.toLocaleDateString();
}

// ─── DRAWER: Contact Enrichment ───────────────────────────────────────────
async function enrichContact(prospectId) {
    const btn = document.getElementById(`enrich-btn-${prospectId}`);
    const results = document.getElementById(`enrich-results-${prospectId}`);
    btn.disabled = true;
    btn.textContent = 'Enriching...';
    results.innerHTML = skeletonHTML('lines', 3);
    try {
        const res = await fetch(`${API_BASE}/prospects/${prospectId}/enrich`, {
            method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}'
        });
        const data = await res.json();
        if (data.success) {
            let html = '';
            const e = data.enrichment;
            if (e.email_guesses && e.email_guesses.length > 0) {
                html += '<div style="margin-top:8px;"><strong style="font-size:11px;color:var(--green);">Email Guesses:</strong>';
                html += e.email_guesses.map(em => `<div style="font-size:11px;color:var(--text-dim);padding:2px 0;">${esc(em)}</div>`).join('');
                html += '</div>';
            }
            if (e.linkedin_suggestion) {
                html += `<div style="margin-top:6px;font-size:11px;"><strong style="color:var(--green);">LinkedIn:</strong> <a href="${esc(e.linkedin_suggestion)}" target="_blank" style="color:var(--blue);">${esc(e.linkedin_suggestion)}</a></div>`;
            }
            if (e.email_verification) {
                const status = e.email_verification.status;
                const badge = status === 'valid' ? '&#9989; Verified' : status === 'invalid' ? '&#10060; Invalid' : '&#10067; ' + status;
                html += `<div style="margin-top:6px;font-size:11px;"><strong style="color:var(--green);">Email Verification:</strong> ${badge}</div>`;
            }
            if (e.company_info) {
                html += `<div style="margin-top:6px;font-size:11px;"><strong style="color:var(--green);">Company:</strong> ${esc(e.company_info.industry || '')} | ${e.company_info.employee_count || '?'} employees</div>`;
            }
            results.innerHTML = html || '<p style="font-size:11px;color:var(--text-dim);">No additional data found</p>';
        }
    } catch {
        results.innerHTML = '<p style="font-size:11px;color:var(--red);">Enrichment failed</p>';
    }
    btn.disabled = false;
    btn.textContent = 'Enrich Contact';
}

// ─── DRAWER: Sequences ────────────────────────────────────────────────────
async function loadDrawerSequences(prospectId) {
    const container = document.getElementById(`drawer-sequences-${prospectId}`);
    if (!container) return;
    try {
        const res = await fetch(`${API_BASE}/prospects/${prospectId}/sequences`);
        const data = await res.json();
        if (data.success && data.data.length > 0) {
            container.innerHTML = data.data.map(s => `
                <div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:11px;">
                    <strong style="color:var(--green);">${esc(s.sequence_name)}</strong>
                    <span class="stage-badge stage-${s.status}" style="margin-left:6px;">${s.status}</span>
                    <span style="color:var(--text-dim);margin-left:6px;">Step ${s.current_step}</span>
                </div>
            `).join('');
        } else {
            container.innerHTML = '<p style="font-size:11px;color:var(--text-dim);">No sequences assigned</p>';
        }
    } catch { container.innerHTML = ''; }
}

async function showSequenceEnroll(prospectId) {
    try {
        const res = await fetch(`${API_BASE}/sequences`);
        const data = await res.json();
        if (data.success && data.data.length > 0) {
            const container = document.getElementById(`drawer-sequences-${prospectId}`);
            container.innerHTML = '<div style="margin-bottom:8px;">' + data.data.map(s =>
                `<button class="secondary small" style="margin:2px;" onclick="enrollInSequence('${prospectId}', ${s.id})">${esc(s.name)}</button>`
            ).join('') + '</div>';
        } else {
            showStatus('No sequences created yet. Create one in the Sequences section.', 'info');
        }
    } catch { showStatus('Could not load sequences', 'error'); }
}

async function enrollInSequence(prospectId, sequenceId) {
    try {
        const res = await fetch(`${API_BASE}/prospects/${prospectId}/enroll`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ sequence_id: sequenceId })
        });
        const data = await res.json();
        if (data.success) {
            showStatus('Prospect enrolled in sequence! Tasks created.', 'success');
            loadDrawerSequences(prospectId);
            loadDrawerTasks(prospectId);
            loadAllTasks();
        } else {
            showStatus(data.error || 'Enrollment failed', 'error');
        }
    } catch { showStatus('Enrollment failed', 'error'); }
}

// ─── STOCK TICKER SETTINGS ────────────────────────────────────────────────
async function openStockSettings() {
    const existing = document.getElementById('stock-settings-popup');
    if (existing) { existing.remove(); return; }
    const popup = document.createElement('div');
    popup.id = 'stock-settings-popup';
    popup.className = 'stock-settings-popup';
    popup.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;"><strong style="color:var(--green);font-size:12px;">Stock Symbols</strong><button class="secondary small" onclick="document.getElementById(\'stock-settings-popup\').remove()">&#x2715;</button></div><div id="stock-symbols-list">' + skeletonHTML('lines', 3) + '</div><div style="display:flex;gap:4px;margin-top:8px;"><input id="new-stock-symbol" placeholder="SYMBOL" maxlength="5" style="flex:1;padding:6px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:12px;text-transform:uppercase;" /><button class="primary small" onclick="addStockSymbol()">Add</button></div>';
    document.body.appendChild(popup);
    loadStockSymbols();
}

async function loadStockSymbols() {
    try {
        const res = await fetch(`${API_BASE}/stocks/symbols`);
        const data = await res.json();
        if (data.success) {
            const container = document.getElementById('stock-symbols-list');
            container.innerHTML = data.data.map(s =>
                `<div style="display:flex;justify-content:space-between;align-items:center;padding:3px 0;font-size:11px;"><span style="color:var(--green);font-family:var(--font-mono);">${esc(s.symbol)}</span><button class="danger small" style="padding:2px 6px;font-size:9px;" onclick="removeStockSymbol('${s.symbol}')">&#x2715;</button></div>`
            ).join('');
        }
    } catch {}
}

async function addStockSymbol() {
    const input = document.getElementById('new-stock-symbol');
    const symbol = input.value.trim().toUpperCase();
    if (!symbol) return;
    try {
        const res = await fetch(`${API_BASE}/stocks/symbols`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ symbol })
        });
        const data = await res.json();
        if (data.success) { input.value = ''; loadStockSymbols(); }
        else showStatus(data.error || 'Failed to add', 'error');
    } catch {}
}

async function removeStockSymbol(symbol) {
    try {
        await fetch(`${API_BASE}/stocks/symbols/${symbol}`, { method: 'DELETE' });
        loadStockSymbols();
    } catch {}
}

// ─── SEQUENCE BUILDER ─────────────────────────────────────────────────────
async function loadSequences() {
    const container = document.getElementById('sequences-list');
    if (!container) return;
    try {
        const res = await fetch(`${API_BASE}/sequences`);
        const data = await res.json();
        if (data.success) {
            if (data.data.length === 0) {
                container.innerHTML = '<p style="font-size:12px;color:var(--text-dim);text-align:center;">No sequences yet. Create your first one!</p>';
                return;
            }
            container.innerHTML = data.data.map(s => `
                <div class="sequence-card" style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:12px;margin-bottom:8px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <strong style="color:var(--green);font-size:13px;">${esc(s.name)}</strong>
                        <button class="danger small" onclick="deleteSequence(${s.id})">Delete</button>
                    </div>
                    <p style="font-size:11px;color:var(--text-dim);margin:4px 0;">${esc(s.description || '')}</p>
                    <div style="font-size:10px;color:var(--text-dim);">${s.steps.length} steps: ${s.steps.map(st => 'Day ' + st.day_offset + ' - ' + (st.step_type || 'email')).join(', ')}</div>
                </div>
            `).join('');
        }
    } catch {}
}

async function createSequence() {
    const name = (document.getElementById('seq-name') || {}).value?.trim();
    if (!name) { showStatus('Enter a sequence name', 'error'); return; }
    const steps = [];
    document.querySelectorAll('.seq-step-row').forEach(row => {
        steps.push({
            day_offset: parseInt(row.querySelector('.seq-day').value) || 0,
            subject_template: row.querySelector('.seq-subject').value.trim(),
            body_template: row.querySelector('.seq-body').value.trim(),
            step_type: row.querySelector('.seq-type').value || 'email'
        });
    });
    if (steps.length === 0) { showStatus('Add at least one step', 'error'); return; }
    try {
        const res = await fetch(`${API_BASE}/sequences`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ name, description: '', steps })
        });
        const data = await res.json();
        if (data.success) { showStatus('Sequence created!', 'success'); loadSequences(); }
    } catch { showStatus('Failed to create sequence', 'error'); }
}

function addSequenceStep() {
    const container = document.getElementById('seq-steps-container');
    if (!container) return;
    const idx = container.children.length;
    const row = document.createElement('div');
    row.className = 'seq-step-row';
    row.style.cssText = 'display:flex;gap:6px;margin-bottom:6px;align-items:center;';
    row.innerHTML = `
        <input class="seq-day" type="number" min="0" value="${idx * 3}" placeholder="Day" style="width:50px;padding:6px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:11px;" />
        <select class="seq-type" style="padding:6px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:11px;">
            <option value="email">Email</option><option value="call">Call</option><option value="linkedin">LinkedIn</option>
        </select>
        <input class="seq-subject" placeholder="Subject/Title" style="flex:1;padding:6px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:11px;" />
        <input class="seq-body" placeholder="Body/Notes" style="flex:1;padding:6px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:11px;" />
        <button class="danger small" onclick="this.parentElement.remove()" style="padding:4px 8px;">&#x2715;</button>
    `;
    container.appendChild(row);
}

async function deleteSequence(id) {
    try {
        await fetch(`${API_BASE}/sequences/${id}`, { method: 'DELETE' });
        loadSequences();
    } catch {}
}

// ─── MOBILE NAVIGATION ───────────────────────────────────────────────────
function mobileNav(section) {
    document.querySelectorAll('.mobile-nav-btn').forEach(b => b.classList.remove('active'));
    if (event && event.currentTarget) event.currentTarget.classList.add('active');
    const targets = {
        prospects: 'prospects-container',
        board: 'kanban-board',
        tasks: 'tasks-section',
        forum: 'forum-section',
        more: 'education-section'
    };
    const el = document.getElementById(targets[section]);
    if (el) {
        if (section === 'board') { setView('kanban'); }
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

// ─── AI ICEBREAKER ────────────────────────────────────────────────────────
async function generateIcebreaker(prospectId) {
    const p = prospects.find(x => x.id === prospectId);
    if (!p) return;

    const btn = document.getElementById(`icebreaker-btn-${prospectId}`);
    const results = document.getElementById(`icebreaker-results-${prospectId}`);
    btn.disabled = true;
    btn.textContent = 'Generating...';
    results.innerHTML = '<p style="font-size:12px;color:var(--text-dim);">Analyzing source data...</p>';

    try {
        const res = await fetch(`${API_BASE}/icebreaker`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ name: p.name, company: p.company, title: p.title, source: p.source })
        });
        const data = await res.json();

        if (data.success && data.icebreakers.length) {
            results.innerHTML = data.icebreakers.map((ib, i) => `
                <div class="icebreaker-card" onclick="copyToClipboard(this.textContent.trim())" title="Click to copy">
                    ${esc(ib)}
                </div>
            `).join('');
        } else {
            results.innerHTML = '<p style="font-size:12px;color:var(--text-dim);">Could not generate icebreaker.</p>';
        }
    } catch {
        results.innerHTML = '<p style="font-size:12px;color:var(--red);">Error generating icebreaker.</p>';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Generate Icebreaker';
    }
}

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showStatus('Copied to clipboard', 'success');
    });
}

// ─── TASKS ────────────────────────────────────────────────────────────────
async function loadAllTasks() {
    try {
        const res = await fetch(`${API_BASE}/tasks`);
        const data = await res.json();
        if (data.success) {
            allTasks = data.data;
            renderGlobalTasks();
            updateTasksCount();
        }
    } catch {}
}

function updateTasksCount() {
    const pending = allTasks.filter(t => t.status === 'pending').length;
    document.getElementById('tasks-count').textContent = pending;
}

const PRIORITY_COLORS = { high: 'var(--red)', medium: 'var(--yellow)', low: 'var(--green)' };
const CATEGORY_ICONS = { call: '&#128222;', email: '&#9993;', meeting: '&#128197;', research: '&#128269;', general: '&#9733;' };
let taskFilterPriority = 'all';
let taskFilterCategory = 'all';

function renderGlobalTasks() {
    const container = document.getElementById('global-tasks-list');
    let pending = allTasks.filter(t => t.status === 'pending');
    if (taskFilterPriority !== 'all') pending = pending.filter(t => (t.priority || 'medium') === taskFilterPriority);
    if (taskFilterCategory !== 'all') pending = pending.filter(t => (t.category || 'general') === taskFilterCategory);
    if (!pending.length) {
        container.innerHTML = '<p style="font-size:12px;color:var(--text-dim);text-align:center;padding:12px;">No pending tasks</p>';
        return;
    }
    container.innerHTML = pending.slice(0, 10).map(t => {
        const isOverdue = t.due_date && new Date(t.due_date) < new Date();
        const prospect = prospects.find(p => p.id === t.prospect_id);
        const priority = t.priority || 'medium';
        const category = t.category || 'general';
        return `
        <div class="global-task-item">
            <input type="checkbox" class="task-checkbox" onchange="completeTask('${t.id}')" />
            <span class="priority-dot" style="color:${PRIORITY_COLORS[priority]}" title="${priority} priority">&#9679;</span>
            <span class="category-icon" title="${category}">${CATEGORY_ICONS[category] || CATEGORY_ICONS.general}</span>
            <span class="task-title">${esc(t.title)}</span>
            ${prospect ? `<span class="global-task-prospect">${esc(prospect.name)}</span>` : ''}
            ${t.due_date ? `<span class="task-due ${isOverdue?'overdue':''}">${t.due_date}</span>` : ''}
            <button class="task-delete-btn" onclick="deleteTask('${t.id}')" title="Delete task">&#x2715;</button>
        </div>`;
    }).join('');
}

function setTaskFilter(type, value) {
    if (type === 'priority') taskFilterPriority = value;
    if (type === 'category') taskFilterCategory = value;
    renderGlobalTasks();
}

async function addGlobalTask() {
    const title = document.getElementById('new-task-title').value.trim();
    const date = document.getElementById('new-task-date').value;
    const prospectId = document.getElementById('new-task-prospect').value;
    const priority = (document.getElementById('new-task-priority') || {}).value || 'medium';
    const category = (document.getElementById('new-task-category') || {}).value || 'general';
    if (!title || !date) {
        showStatus('Please enter a task title and due date', 'error');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/tasks`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ title, due_date: date, prospect_id: prospectId || null, priority, category })
        });
        const data = await res.json();
        if (data.success) {
            document.getElementById('new-task-title').value = '';
            document.getElementById('new-task-date').value = '';
            document.getElementById('new-task-prospect').value = '';
            document.getElementById('new-task-prospect-search').value = '';
            showStatus('Task added', 'success');
            await loadAllTasks();
        } else {
            showStatus('Error adding task', 'error');
        }
    } catch (error) {
        console.error('Error adding task:', error);
        showStatus('Error adding task', 'error');
    }
}

async function completeTask(taskId) {
    try {
        await fetch(`${API_BASE}/tasks/${taskId}`, {
            method: 'PUT', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ status: 'completed' })
        });
        await loadAllTasks();
    } catch {}
}

async function deleteTask(taskId) {
    try {
        await fetch(`${API_BASE}/tasks/${taskId}`, { method: 'DELETE' });
        await loadAllTasks();
    } catch {}
}

function openTaskModal(prospectId) {
    document.getElementById('task-prospect-id-input').value = prospectId || '';
    document.getElementById('task-title-input').value = '';
    document.getElementById('task-desc-input').value = '';
    document.getElementById('task-date-input').value = '';
    const modal = document.getElementById('task-modal');
    modal.style.display = 'flex';
    modal.offsetHeight;
    modal.classList.add('active');
}

function closeTaskModal() {
    const modal = document.getElementById('task-modal');
    modal.classList.add('closing');
    setTimeout(() => {
        modal.classList.remove('active', 'closing');
        modal.style.display = 'none';
    }, 300);
}

async function handleTaskFormSubmit(e) {
    e.preventDefault();
    const title = document.getElementById('task-title-input').value;
    const desc = document.getElementById('task-desc-input').value;
    const date = document.getElementById('task-date-input').value;
    const prospectId = document.getElementById('task-prospect-id-input').value;

    try {
        await fetch(`${API_BASE}/tasks`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ title, description: desc, due_date: date, prospect_id: prospectId || null })
        });
        closeTaskModal();
        await loadAllTasks();
        if (prospectId) loadDrawerTasks(prospectId);
    } catch {}
}

async function loadDrawerTasks(prospectId) {
    const container = document.getElementById(`drawer-tasks-${prospectId}`);
    if (!container) return;
    try {
        const res = await fetch(`${API_BASE}/tasks?prospect_id=${prospectId}`);
        const data = await res.json();
        if (data.success && data.data.length) {
            container.innerHTML = data.data.map(t => {
                const done = t.status === 'completed';
                const overdue = !done && t.due_date && new Date(t.due_date) < new Date();
                return `
                <div class="task-item">
                    <input type="checkbox" class="task-checkbox" ${done?'checked disabled':''} onchange="completeTask('${t.id}')" />
                    <span class="task-title ${done?'done':''}">${esc(t.title)}</span>
                    ${t.due_date ? `<span class="task-due ${overdue?'overdue':''}">${t.due_date}</span>` : ''}
                    <button class="task-delete-btn" onclick="deleteTask('${t.id}')" title="Delete task">&#x2715;</button>
                </div>`;
            }).join('');
        } else {
            container.innerHTML = '<p style="font-size:12px;color:var(--text-dim);">No tasks yet</p>';
        }
    } catch {
        container.innerHTML = '<p style="font-size:12px;color:var(--text-dim);">Error loading tasks</p>';
    }
}

function updateTaskProspectDropdown() {
    // No longer a <select>, the searchable input handles this dynamically
    // Just reset the search field if it has stale data
    const searchInput = document.getElementById('new-task-prospect-search');
    if (searchInput && !searchInput.value) {
        searchInput.value = '';
        document.getElementById('new-task-prospect').value = '';
    }
}

// ─── CSV IMPORT/EXPORT ────────────────────────────────────────────────────
async function handleCSVImport(e) {
    const file = e.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetch(`${API_BASE}/import-csv`, { method: 'POST', body: formData });
        const data = await res.json();
        if (data.success) {
            showStatus(`Imported ${data.imported} prospects${data.errors ? `, ${data.errors} errors` : ''}`, 'success');
            await loadProspects();
        } else {
            showStatus(`Import error: ${data.error}`, 'error');
        }
    } catch {
        showStatus('Import failed', 'error');
    }
    e.target.value = '';
}

function exportCSV() {
    window.open(`${API_BASE}/export-csv`, '_blank');
}

// ─── MODAL (Smooth Transitions) ─────────────────────────────────────────
function openModal() {
    editingId = null;
    document.getElementById('modal-title').textContent = 'Add Prospect';
    document.getElementById('prospect-form').reset();
    document.getElementById('prospect-form').dataset.crawledIdx = '';
    const modal = document.getElementById('prospect-modal');
    modal.style.display = 'flex';
    // Force reflow for transition
    modal.offsetHeight;
    modal.classList.add('active');
}

function closeModal() {
    const modal = document.getElementById('prospect-modal');
    modal.classList.add('closing');
    setTimeout(() => {
        modal.classList.remove('active', 'closing');
        modal.style.display = 'none';
    }, 300);
}

async function handleFormSubmit(e) {
    e.preventDefault();
    const saveBtn = document.querySelector('#prospect-form button[type="submit"]');
    const origText = saveBtn.textContent;
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';

    const prospect = {
        name: document.getElementById('name').value,
        company: document.getElementById('company').value,
        title: document.getElementById('title').value,
        email: document.getElementById('email').value,
        phone: document.getElementById('phone').value,
        status: document.getElementById('status').value,
        deal_size: parseFloat(document.getElementById('deal-size').value) || 0,
        linkedin_url: document.getElementById('linkedin-url').value,
        source: document.getElementById('source-url').value,
        notes: document.getElementById('notes').value
    };

    try {
        let res;
        if (editingId) {
            res = await fetch(`${API_BASE}/prospects/${editingId}`, {
                method: 'PUT', headers: {'Content-Type':'application/json'},
                body: JSON.stringify(prospect)
            });
        } else {
            res = await fetch(`${API_BASE}/prospects`, {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify(prospect)
            });
        }
        const data = await res.json();
        if (data.success) {
            closeModal();
            showStatus(editingId ? 'Prospect updated' : 'Prospect added', 'success');
            await loadProspects();
        }
    } catch {
        showStatus('Error saving prospect', 'error');
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = origText;
    }
}

async function editProspect(id) {
    const p = prospects.find(x => x.id === id);
    if (!p) return;
    editingId = id;
    document.getElementById('modal-title').textContent = 'Edit Prospect';
    document.getElementById('name').value = p.name || '';
    document.getElementById('company').value = p.company || '';
    document.getElementById('title').value = p.title || '';
    document.getElementById('email').value = p.email || '';
    document.getElementById('phone').value = p.phone || '';
    document.getElementById('status').value = p.status || 'lead';
    document.getElementById('deal-size').value = p.deal_size || 0;
    document.getElementById('linkedin-url').value = p.linkedin_url || '';
    document.getElementById('source-url').value = p.source || '';
    document.getElementById('notes').value = p.notes || '';
    const modal = document.getElementById('prospect-modal');
    modal.style.display = 'flex';
    modal.offsetHeight;
    modal.classList.add('active');
}

async function deleteProspect(id) {
    if (!confirm('Delete this prospect?')) return;
    try {
        const res = await fetch(`${API_BASE}/prospects/${id}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) await loadProspects();
    } catch {}
}

// ─── STATS ────────────────────────────────────────────────────────────────
function updateStats() {
    const total = prospects.length;
    const leads = prospects.filter(p => p.status === 'lead').length;
    const won = prospects.filter(p => p.status === 'won').length;
    const value = prospects.reduce((s, p) => s + (p.deal_size || 0), 0);

    animateCounter('total-count', total);
    animateCounter('leads-count', leads);
    animateCounter('won-count', won);
    document.getElementById('pipeline-value').textContent = '$' + value.toLocaleString();
}

function animateCounter(id, target) {
    const el = document.getElementById(id);
    const current = parseInt(el.textContent) || 0;
    if (current === target) return;
    const step = target > current ? 1 : -1;
    const duration = 300;
    const steps = Math.abs(target - current);
    const interval = Math.max(duration / steps, 20);
    let val = current;
    const timer = setInterval(() => {
        val += step;
        el.textContent = val;
        if (val === target) clearInterval(timer);
    }, interval);
}

// ─── CHAT ─────────────────────────────────────────────────────────────────
function initChat() {
    if (chatUsername) {
        document.getElementById('chat-username-setup').style.display = 'none';
        document.getElementById('chat-main').style.display = 'flex';
    }
    loadChatMessages();
    connectChatSocket();
}

function connectChatSocket() {
    try {
        chatSocket = io(window.location.origin + '/chat', {
            transports: ['websocket', 'polling']
        });

        chatSocket.on('connect', () => {
            if (chatUsername) {
                chatSocket.emit('set_username', { username: chatUsername });
            }
        });

        chatSocket.on('new_message', (msg) => {
            appendChatMessage(msg);
            if (!chatOpen && msg.username !== chatUsername) {
                unreadMessages++;
                updateChatBadge();
            }
        });

        chatSocket.on('user_joined', (data) => {
            document.getElementById('chat-online-count').textContent = data.online_users.length;
        });

        chatSocket.on('user_left', (data) => {
            document.getElementById('chat-online-count').textContent = data.online_users.length;
        });
    } catch (e) {
        console.log('Chat socket not available, using REST fallback');
    }
}

function setChatUsername() {
    const input = document.getElementById('chat-username-input');
    const name = input.value.trim();
    if (!name) return;
    chatUsername = name;
    localStorage.setItem('chat_username', name);
    document.getElementById('chat-username-setup').style.display = 'none';
    document.getElementById('chat-main').style.display = 'flex';
    if (chatSocket && chatSocket.connected) {
        chatSocket.emit('set_username', { username: name });
    }
}

async function loadChatMessages() {
    try {
        const res = await fetch(`${API_BASE}/chat/messages?limit=50`);
        const data = await res.json();
        if (data.success) {
            const container = document.getElementById('chat-messages');
            container.innerHTML = '';
            data.data.forEach(msg => appendChatMessage(msg, false));
            container.scrollTop = container.scrollHeight;
        }
    } catch {}
}

function appendChatMessage(msg, scroll = true) {
    const container = document.getElementById('chat-messages');
    const isSelf = msg.username === chatUsername;
    const time = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '';
    const div = document.createElement('div');
    div.className = `chat-msg ${isSelf ? 'self' : ''}`;
    div.innerHTML = `
        <div class="chat-msg-header">
            <span class="chat-msg-user">${esc(msg.username)}</span>
            <span class="chat-msg-time">${time}</span>
        </div>
        <div class="chat-msg-text">${esc(msg.message)}</div>
    `;
    container.appendChild(div);
    if (scroll) container.scrollTop = container.scrollHeight;
}

function sendChatMessage() {
    const input = document.getElementById('chat-msg-input');
    const msg = input.value.trim();
    if (!msg || !chatUsername) return;

    if (chatSocket && chatSocket.connected) {
        chatSocket.emit('send_message', { message: msg, username: chatUsername });
    } else {
        // REST fallback
        fetch(`${API_BASE}/chat/messages`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ username: chatUsername, message: msg })
        });
    }
    input.value = '';
}

function toggleChat() {
    chatOpen = !chatOpen;
    document.getElementById('chat-window').classList.toggle('active', chatOpen);
    if (chatOpen) {
        unreadMessages = 0;
        updateChatBadge();
        document.getElementById('chat-messages').scrollTop = document.getElementById('chat-messages').scrollHeight;
    }
}

function updateChatBadge() {
    const badge = document.getElementById('chat-badge');
    badge.textContent = unreadMessages;
    badge.classList.toggle('active', unreadMessages > 0);
}

// ─── MODAL CLICK OUTSIDE ──────────────────────────────────────────────────
document.addEventListener('click', (e) => {
    if (e.target.id === 'prospect-modal') closeModal();
    if (e.target.id === 'task-modal') closeTaskModal();
});

// ─── GO ───────────────────────────────────────────────────────────────────
window.addEventListener('load', init);
