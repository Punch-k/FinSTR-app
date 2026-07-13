# FinSTR — Stock Screener & Portfolio Manager

### [🌐 Live Demo (GitHub Pages)](https://punch-k.github.io/FinSTR-app/)

> **Note:** The live demo runs in static mode with 74 sample large-cap stocks. To get real Yahoo Finance data, run the Flask backend locally (see setup below) — the frontend auto-detects the backend and switches to live data.

---

### FEATURES

**Screener** (`/api/screener`)
- 74 large-cap stocks with real fundamentals pulled from Yahoo Finance via `yfinance`
- Filters: Sector, Industry, Market Cap, P/E, PEG, Dividend Yield, ROE, Profit Margin, Beta, 50D MA
- Signals: Top Gainers / Losers, Unusual Volume, Above/Below 50D MA, New High/Low
- Sort by any column · 6 column view presets (Overview, Valuation, Financial, Performance, Technical, Custom)
- Data cached in SQLite for 6 hours — fast on repeat visits

**Maps**
- Treemap and bubble chart heatmap grouped by sector/industry
- Color-coded by % change · size = market cap
- Click any tile to open the stock detail panel

**Stock Detail Modal**
- Buy/Hold/Sell scoring with DCF fair-value estimate
- Interactive growth & discount rate sliders
- 5-year investment projection table
- Live price fetch from Yahoo Finance (via backend) or Alpha Vantage (demo key fallback)

**Portfolio Manager** (MyShare — runs on same Flask app)
- Full user auth, lot tracking, sell lot tracking, P&L calculations
- Available at `/myshare/home`

---

### LOCAL SETUP

```bash
# 1. Clone the repo
git clone https://github.com/Punch-k/FinSTR-app.git
cd FinSTR-app

# 2. Install dependencies
pip install flask flask-restful passlib yahoo_fin yfinance yagmail lxml

# 3. Run the app
python app.py

# 4. Open in browser
#    Screener:  http://localhost:1817/
#    Portfolio: http://localhost:1817/myshare/home
```

On first run, `/api/screener` fetches live data for all 74 tickers in parallel (may take ~30–60s). Subsequent calls use the 6-hour SQLite cache.

---

### FILE STRUCTURE

| File | Purpose |
|---|---|
| `app.py` | Flask app — screener API + MyShare portfolio API + serves index.html |
| `index.html` | FinSTR frontend — auto-switches between live and demo data |
| `database/MyShare.db` | SQLite database (users, holdings, stock cache) |
| `database/create.sql` | Schema definition |
| `templates/` | Jinja2 HTML pages for the MyShare portfolio section |
| `static/` | CSS and JS for the MyShare portfolio section |

---

### API ENDPOINTS

| Endpoint | Method | Description |
|---|---|---|
| `/api/screener` | GET | Returns JSON array of all 74 stocks with fundamentals |
| `/api/price?ticker=AAPL` | GET | Returns current live price for a ticker |
| `/myshare/user` | GET/POST/PATCH/DELETE | User account management |
| `/myshare/user/lots` | GET/POST/PATCH/DELETE | Buy lot management |
| `/myshare/user/sell-lots` | GET/POST/PATCH/DELETE | Sell lot management |
| `/myshare/user/holdings` | GET | Aggregated portfolio holdings with P&L |

---

<div align="center">
  <br>
  <h2>Screener Demo</h2>
  <img src="demos/Holdings.gif" alt="Holdings">
</div>
