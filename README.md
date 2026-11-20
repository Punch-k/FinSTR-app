# FinSTR: S&P 500 Screener, Maps and Quote Pages

**Live site: [punch-k.github.io/FinSTR-app](https://punch-k.github.io/FinSTR-app/)**

FinSTR is a Finviz-style stock research tool for the S&P 500. It screens all ~500 companies
across valuation, financial, ownership, performance, technical and analyst views, maps the
market by sector, and opens a full quote page for any stock. A Python pipeline rebuilds the
data several times each US trading day and publishes it as a static site: no server, no
paid data, no API keys.

Built by [Prapanch Kokkalemada](https://punch-k.github.io/Portfolio/).

---

## What it does

| Area | Features |
|---|---|
| **Screener** | 70+ fields per stock · 12 column views (Overview, Valuation, Financial, Ownership, Performance, Technical, Analysts, …) · filters by sector, industry, market cap, P/E, PEG, dividend, ROE, margin, moving average, beta · 16 signals (52-week highs/lows, unusual volume, RSI overbought/oversold, earnings this week, …) · presets, watchlist, CSV export, shareable links |
| **Maps** | Market-cap treemap and bubble view, coloured by 1-day, 1-week, 1-month, 3-month, 6-month, YTD or 1-year performance |
| **Quote panel** | 2-year price chart with 50/200-day moving averages · 72-field snapshot table · company profile · analyst targets · headlines · valuation sandbox (your own growth and discount assumptions) |
| **Markets** | Index cards, 26 futures, 12 currency pairs, 11 crypto assets |
| **Analytics** | Candlestick charts, sector and industry groups, insider transactions, earnings and ex-dividend calendar, news by topic |

## Data and method

- **Source:** Yahoo Finance via [`yfinance`](https://github.com/ranaroussi/yfinance) (prices, fundamentals, ownership, analyst data, insider trades) and Yahoo Finance RSS (headlines). Universe: S&P 500 constituents with official GICS sectors.
- **Computed consistently for every stock** from daily prices: performance (week to year, YTD), SMA 20/50/200 and distance from each, RSI (14, Wilder), volatility (std. dev. of daily returns), ATR (14), 52-week high/low, relative volume.
- **No invented numbers.** A field Yahoo does not report shows as "-", never as zero. There is no synthetic fallback data.
- **Keep-last-good.** If a ticker fails on a run, its previous values are carried over and flagged, so one bad run never blanks the site.
- **Freshness.** The site shows the snapshot time on every page. It refreshes several times per trading day; it is not a live feed.
- **Not investment advice.** The FinSTR Score and valuation sandbox are transparent, assumption-driven illustrations.

## How it works

```
GitHub Actions (schedule / push)
  └─ pipeline/fetch.py ──► data/*.json  (screener, markets, groups, news, meta, quote/<TICKER>.json)
        └─ index.html + assets/ + data/ ──► GitHub Pages artifact ──► live site
```

The data is generated on every deploy and published with the site, so it is never committed
to the repository.

## Repository layout

| Path | What it is |
|---|---|
| `index.html` | The site (screener, maps, quote panel, markets, analytics) |
| `assets/` | Images (logo) |
| `pipeline/` | `fetch.py` data builder, `universe.csv` (S&P 500 list), `requirements.txt` |
| `.github/workflows/deploy.yml` | Scheduled build and GitHub Pages deploy |
| `server/` | Optional local Flask app: MyShare portfolio/lot tracker (adapted from Vlad Litvak's MyShare) and the experimental Quant Desk. Not part of the live site. |
| `docs/` | Project notes and the earlier code-quality report |

## Run it locally

```bash
pip install -r pipeline/requirements.txt
python pipeline/fetch.py            # about 5 minutes for the full S&P 500
python pipeline/fetch.py --limit 25 # quick test run
python -m http.server 8000          # then open http://localhost:8000
```

Optional local portfolio manager (MyShare):

```bash
cd server
pip install -r requirements.txt
python app.py                       # http://localhost:1817/myshare/home
```

## Tech

Python (pandas, yfinance) · GitHub Actions · GitHub Pages · vanilla JavaScript · D3 (treemap) · TradingView Lightweight Charts
