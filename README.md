# SignalAI

A buy-side equity research terminal for tracking public and private technology companies. Built for fundamental analysts with a 1–3 year investment horizon.

![SignalAI Terminal](screenshots/hero.png)

## Features

### Public Companies Watchlist
- **45 pre-loaded tech tickers** across 12 subsectors (Hyperscalers, Semiconductors, Cybersecurity, Enterprise Software, etc.)
- Real-time prices, market cap, EV, EV/Sales, EV/FCF, and performance returns (1D through 3Y)
- Company headquarters displayed under each name
- Sortable columns with collapsible subsector grouping
- Add/remove tickers with live search autocomplete
- Click any ticker for a detailed popup with charts and fundamentals

### Private Companies Tracker
- **15 pre-loaded private companies** (OpenAI, Anthropic, Databricks, Stripe, etc.) with PitchBook-sourced data
- Last valuation, funding round, lead investors, revenue estimates, and key metrics
- Headquarters display and status badges for recently-IPO'd companies (CoreWeave, Figma) and IPO filings (Cerebras)

![Private Companies](screenshots/private.png)

### Earnings Research (SignalAI)
- Automated pre-earnings and post-earnings research notes
- Pre-earnings notes: set-up, key debates, what matters this print, scenario grid (bull/base/bear)
- Post-earnings notes: headline results vs expectations, guidance and tone, thesis impact, follow-ups
- Earnings calendar with upcoming and recent reporters
- Archive system for older notes

![Earnings Section](screenshots/earnings.png)

### Additional Features
- **Market News** feed aggregated from watchlist tickers
- **Deep-dive popup** with quantitative factors, short interest, S&P 500 outperformance, and cross-sector comps (requires backend)
- **Dark terminal theme** with JetBrains Mono + Inter typography
- Auto-classification of new tickers into subsectors
- Persistent storage with migration system for seamless updates

## Quick Start

### Option 1: Live Demo

Try it now at **[wtlittle.github.io/signalai](https://wtlittle.github.io/signalai/)**

The demo loads with a cached data snapshot so the terminal populates instantly. If a CORS proxy is available, it will attempt to fetch live prices on top of the snapshot data.

### Option 2: Static Local (no backend)

Serve the files with any static file server:

```bash
# Using Python
python -m http.server 5000

# Using Node.js
npx serve . -l 5000
```

Then open [http://localhost:5000](http://localhost:5000).

> **Note:** The static version provides core functionality (prices, charts, performance). Advanced features like quantitative factor analysis, short interest data, cross-sector comps, and news require the Python backend.

### Option 3: Full Setup (with backend)

The Python backend provides richer data, faster batch fetching, and advanced analytics.

**Requirements:** Python 3.9+

```bash
# Install dependencies
pip install yfinance numpy ddgs

# Start the backend
python backend.py
# Backend runs on port 5001

# In another terminal, serve the frontend
npx serve . -l 5000
```

Open [http://localhost:5000](http://localhost:5000). The frontend will automatically detect and use the backend.

## Architecture

```
├── index.html          # Main HTML shell
├── styles.css          # Full terminal theme
├── utils.js            # Constants, formatters, storage, company data
├── api-client.js       # Client-side fallback (CORS proxy + snapshot)
├── api.js              # Data fetching layer (backend-first, client fallback)
├── data-snapshot.json  # Cached market data for instant demo loading
├── app.js              # Main app logic, rendering, state management
├── popup.js            # Ticker detail popup
├── popup-chart.js      # Interactive price charts (Chart.js)
├── popup-deep-dive.js  # Quantitative deep-dive analysis
├── earnings.js         # Earnings calendar and research notes
├── backend.py          # Python backend (yfinance, news, analytics)
├── earnings_calendar.json  # Pre-built earnings data
└── notes/              # Pre/post earnings research notes (Markdown)
    ├── pre_earnings/
    └── post_earnings/
```

### Data Flow

1. **With backend:** Frontend → Python backend (port 5001) → yfinance → Yahoo Finance APIs
2. **Without backend (CORS proxy available):** Frontend → `api-client.js` → CORS proxy → Yahoo Finance APIs
3. **Without backend (no proxy):** Frontend → `api-client.js` → `data-snapshot.json` (cached data)

The frontend automatically detects the best available data source. When running as a static demo, it loads a pre-cached snapshot instantly while optionally attempting live price updates through CORS proxies.

## Tech Stack

- **Frontend:** Vanilla HTML/CSS/JS, Chart.js for charts
- **Backend:** Python stdlib HTTP server, yfinance, numpy, DuckDuckGo search (ddgs)
- **Design:** Dark navy terminal theme (#0a0e17), JetBrains Mono monospace, Inter sans-serif
- **Storage:** localStorage with in-memory fallback (works in iframes)

## Private Company Data

Private company data is sourced from PitchBook Company Profiles and includes:
- Valuations and funding history
- Lead investors
- Revenue estimates (TTM)
- Employee counts
- Headquarters locations
- IPO status tracking

Data is embedded in `utils.js` as `DEFAULT_PRIVATE_COMPANIES` and can be updated manually or via the PitchBook API.

## Snapshot hosting (Cloudflare R2)

The terminal no longer relies on committed JSON files for its runtime data. Seven snapshot files are published to the `signalai-data` Cloudflare R2 bucket and fetched by the front-end at page load:

- `data-snapshot.json`
- `earnings_data.json`
- `earnings_intel.json`
- `earnings_calendar.json`
- `macro_data.json`
- `weekly_briefing.json`
- `earnings_notes_index.json`

### How it works

- `snapshot-config.js` exports a single `window.SignalSnapshot` facade. Every network call for a snapshot goes through `SignalSnapshot.getSnapshotUrl(file)` / `SignalSnapshot.fetchSnapshot(file)`.
- `R2_BASE` is the public R2 URL: `https://pub-2e23479367774577a65757b8f638478a.r2.dev`.
- When a fetch fails the helper records the failure via `SignalSnapshot.markFailure(...)`. The global `#snapshot-banner` subscribes to those status updates via `snapshot-banner.js` and shows a red "Data source degraded" banner with Retry / Dismiss actions.

### Local vs R2 toggle

You can force the front-end to read snapshots from the local checkout instead of R2 using any of:

- URL param: `?local=1`
- Console: `window.SIGNALAI_USE_LOCAL = true` before page load
- When serving from `localhost` / `127.0.0.1` the module defaults to local unless `window.SIGNALAI_PREFER_LOCAL = false` is set

### Regenerate + upload flow

1. Run the daily automation job (`automation/jobs/daily_refresh`) — it writes the seven files back to the repo root.
2. Commit and push to `main`.
3. The `Upload snapshots to Cloudflare R2` GitHub Actions workflow (`.github/workflows/r2-upload.yml`) triggers automatically and runs `wrangler r2 object put` for each file. It also runs after the `SignalAI Daily Research` workflow completes.

**Required repo secrets:**

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN` (R2 read/write)
- `CLOUDFLARE_R2_BUCKET` (optional; defaults to `signalai-data`)

Manual uploads can be performed locally with:

```bash
wrangler r2 object put signalai-data/data-snapshot.json --file=data-snapshot.json --remote
```

## License

MIT

---

Built with [Perplexity Computer](https://www.perplexity.ai/computer)
