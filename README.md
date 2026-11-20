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
| **Quote page** | Finviz-order layout: candlestick chart with SMA 20/50/200 and volume · peers · 78-field snapshot table · 5-year and quarterly EPS, sales, net income and shares · earnings surprises · analyst consensus and rating changes · headlines · profile · insider trades · valuation sandbox. On phones: sticky header, swipeable tabs and grouped data cards. |
| **Stock pages** | A fast, no-JavaScript page per company at `/stock/TICKER/` (key numbers, signal, factors, reasons) plus an [A-Z index by sector](https://punch-k.github.io/FinSTR-app/stock/) and `sitemap.xml`, rebuilt on every deploy by `pipeline/pages.py` |
| **Markets** | Index cards, 26 futures, 12 currency pairs, 11 crypto assets |
| **Analytics** | Candlestick charts, sector and industry groups, insider transactions, earnings and ex-dividend calendar, news by topic |

## Data and method

- **Source:** Yahoo Finance via [`yfinance`](https://github.com/ranaroussi/yfinance) (prices, fundamentals, ownership, analyst data, insider trades) and Yahoo Finance RSS (headlines). Universe: S&P 500 constituents with official GICS sectors.
- **Computed consistently for every stock** from daily prices: performance (week to year, YTD), SMA 20/50/200 and distance from each, RSI (14, Wilder), volatility (std. dev. of daily returns), ATR (14), 52-week high/low, relative volume.
- **No invented numbers.** A field Yahoo does not report shows as "-", never as zero. There is no synthetic fallback data.
- **Keep-last-good.** If a ticker fails on a run, its previous values are carried over and flagged, so one bad run never blanks the site.
- **Freshness.** The site shows the snapshot time on every page. It refreshes several times per trading day; it is not a live feed.
- **FinSTR Signal.** Six factor scores (trend, momentum, value, quality, growth, low risk), each a percentile vs the S&P 500; value and quality are ranked within sector. The price-based factors are backtested monthly (top vs bottom fifth vs the index) and published on the Signals page, including when they did not work.
- **Not investment advice.** Signals and the valuation sandbox are transparent, educational tools.

## How it works

```
GitHub Actions (schedule / push)
  └─ pipeline/fetch.py ──► data/*.json  (screener, markets, groups, news, meta, quote/<TICKER>.json)
        └─ index.html + assets/ + data/ ──► GitHub Pages artifact ──► live site
```

The data is generated on every deploy and published with the site, so it is never committed
to the repository.

## Portfolio and email digests

The **Portfolio** page tracks a paper portfolio in your browser: profit and loss, return against the S&P 500 since
each purchase, sector mix, beta, the value-weighted FinSTR Signal, rule-based points to consider for each position,
and strong-signal ideas in sectors you don't hold. You choose your email cadence there (weekly digest, daily summary,
trigger alerts) and export the portfolio as a JSON file.

### Email digests (self-hosted)

`pipeline/digest.py` turns that file plus the latest FinSTR data into a mobile-friendly email, and a short text
version for Telegram, Slack, Discord or phone push. It uses only the Python standard library.

An AI paragraph is optional: point it at any OpenAI-compatible model (a local Ollama model by default). Code computes
every number; the model only writes the paragraph, and its text is checked before it is sent. It is dropped, and the
rule-based summary used instead, if it:

- uses a number that is not in the computed facts (rounding allowed),
- names a ticker that is not in the portfolio or the ideas,
- puts a stock in the wrong sector, or quotes the wrong figure as the S&P 500 comparison,
- calls a gain a loss or the reverse.

The email says when this happened, so a degraded run is never silent.

Set it up in about 5 minutes, free, on your own GitHub account:

1. Fork this repository.
2. In your fork: **Settings > Secrets and variables > Actions**
   - Secret `PORTFOLIO_JSON`: paste the exported file.
   - At least one way to reach you (set any or all):

     | Channel | Secrets |
     |---|---|
     | Email | `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS`, `MAIL_TO`. Gmail: `smtp.gmail.com` and an [app password](https://support.google.com/accounts/answer/185833) |
     | Telegram | `TELEGRAM_BOT_TOKEN` (from @BotFather), `TELEGRAM_CHAT_ID` |
     | Slack or Discord | `WEBHOOK_URL` (an incoming-webhook URL) |
     | Phone push | `NTFY_TOPIC`: install the free [ntfy](https://ntfy.sh) app and subscribe to a long random topic name only you know |
   - Variables: `FINSTR_DIGEST` = `true`. Optional: `FINSTR_LOCAL_AI` = `true` to run an open model on the runner,
     `LLM_MODEL` (default `llama3.2`), or secrets `LLM_BASE_URL` / `LLM_API_KEY` for a hosted model.
3. **Actions > FinSTR Digest > Run workflow** sends a test digest. After that it runs on schedule: Monday morning
   (weekly digest) and after each close (daily summary and trigger alerts), as your preferences say.

Run it locally instead:

```bash
python pipeline/digest.py --portfolio finstr-portfolio.json --mode weekly --out digest.html
LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.2 python pipeline/digest.py --portfolio finstr-portfolio.json --mode weekly --out digest.html
```

Alerts fire when a holding falls 7% or more in a day, crosses its 200-day average, reports earnings within 3 days,
or its signal label changes. Ideas skip companies that report earnings within 5 days.

## For AI agents and automation

Every number on the site is a static JSON file, documented in [`llms.txt`](https://punch-k.github.io/FinSTR-app/llms.txt):
`data/screener.json` (all companies, ~90 fields including the signal and its reasons), `data/quote/{TICKER}.json`
(price history, financials, estimates, rating changes), `data/signals.json` (weights and backtest), and more. Use them
from n8n, Zapier, scripts or an LLM tool with no API key. The Portfolio page's **Copy AI brief** button produces a
ready-made prompt with your holdings and their data for any AI assistant.

## Repository layout

| Path | What it is |
|---|---|
| `index.html` | The site (screener, maps, quote panel, markets, analytics) |
| `assets/` | Images (logo) |
| `pipeline/` | `fetch.py` data builder, `signals.py` signal engine and backtest, `digest.py` email digest, `universe.csv` (S&P 500 list), `requirements.txt` |
| `pipeline/pages.py` | Generates the per-stock pages, stock index and sitemap at deploy time |
| `llms.txt` | Data documentation for AI agents and automation tools |
| `.github/workflows/deploy.yml` | Scheduled build and GitHub Pages deploy |
| `.github/workflows/digest.yml` | Self-hosted email digest (fork, add secrets, receive emails) |
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
