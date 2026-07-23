"""
Standalone data-fetch script for FinSTR's static (GitHub Pages) deployment.

Runs outside Flask — no server, no SQLite cache. Pulls fundamentals + per-ticker
news for the full SCREENER_TICKERS universe and writes plain JSON files that the
static frontend (index.html) reads directly. Intended to be run on a schedule by
.github/workflows/update-data.yml, but works identically from a local shell:

    python scripts/fetch_data.py

Output:
    data/screener.json  - array of per-ticker fundamentals + top 3 news headlines
    data/meta.json      - generation timestamp + ticker/error counts
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.tickers import SCREENER_TICKERS

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
MAX_WORKERS = 8
NEWS_PER_TICKER = 3


INSIDER_PER_TICKER = 5


def fetch_insider_transactions(ticker):
    try:
        df = yfinance.Ticker(ticker).insider_transactions
        if df is None or df.empty:
            return []
    except Exception:
        return []
    rows = []
    for _, row in df.head(INSIDER_PER_TICKER).iterrows():
        rows.append({
            "insider": row.get("Insider", ""),
            "position": row.get("Position", ""),
            "transaction": row.get("Text", "") or row.get("Transaction", ""),
            "shares": int(row["Shares"]) if row.get("Shares") == row.get("Shares") else 0,
            "value": float(row["Value"]) if row.get("Value") == row.get("Value") else None,
            "date": str(row.get("Start Date", "")),
        })
    return rows


def fetch_news(ticker):
    try:
        raw = yfinance.Ticker(ticker).news or []
    except Exception:
        return []
    articles = []
    for a in raw[:NEWS_PER_TICKER]:
        articles.append({
            "title": a.get("title", ""),
            "link": a.get("link", ""),
            "publisher": a.get("publisher", ""),
            "publishedAt": a.get("providerPublishTime", 0),
        })
    return articles


def fetch_calendar(ticker):
    try:
        cal = yfinance.Ticker(ticker).calendar
        if not cal:
            return {}
    except Exception:
        return {}
    earnings = cal.get("Earnings Date") or []
    return {
        "earningsDate": str(earnings[0]) if earnings else None,
        "exDividendDate": str(cal["Ex-Dividend Date"]) if cal.get("Ex-Dividend Date") else None,
    }


def fetch_ticker_data(ticker):
    try:
        t = yfinance.Ticker(ticker)
        info = t.info
        hist = t.history(period="60d")
        price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        prev = float(info.get("previousClose") or price)
        chg = round(((price - prev) / prev * 100), 2) if prev else 0
        n = min(50, len(hist))
        ma50 = round(float(hist["Close"].tail(n).mean()), 2) if n >= 5 else round(price, 2)
        return {
            "ticker": ticker,
            "name": info.get("shortName") or info.get("longName") or ticker,
            "sector": info.get("sector") or "Unknown",
            "industry": info.get("industry") or "Unknown",
            "marketCap": round((info.get("marketCap") or 0) / 1e9, 1),
            "price": round(price, 2),
            "changePercent": chg,
            "pe": round(float(info.get("trailingPE") or 0), 1),
            "forwardPE": round(float(info.get("forwardPE") or 0), 1),
            "peg": round(float(info.get("pegRatio") or 0), 2),
            "ma50": ma50,
            "eps": round(float(info.get("trailingEps") or 0), 2),
            "dividendYield": round(float(info.get("dividendYield") or 0) * 100, 2),
            "beta": round(float(info.get("beta") or 1), 2),
            "roe": round(float(info.get("returnOnEquity") or 0) * 100, 1),
            "profitMargin": round(float(info.get("profitMargins") or 0) * 100, 1),
            "volume": round((info.get("volume") or 0) / 1e6, 2),
            "avgVolume": round((info.get("averageVolume") or 0) / 1e6, 2),
            "news": fetch_news(ticker),
            "insiderTransactions": fetch_insider_transactions(ticker),
            **fetch_calendar(ticker),
        }
    except Exception as e:
        print(f"  [skip] {ticker}: {e}", file=sys.stderr)
        return None


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    results = []
    errors = []

    print(f"Fetching {len(SCREENER_TICKERS)} tickers with {MAX_WORKERS} workers...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_ticker_data, t): t for t in SCREENER_TICKERS}
        for future in as_completed(futures):
            ticker = futures[future]
            data = future.result()
            if data:
                results.append(data)
            else:
                errors.append(ticker)

    results.sort(key=lambda x: x.get("marketCap", 0), reverse=True)

    with open(os.path.join(OUTPUT_DIR, "screener.json"), "w") as f:
        json.dump(results, f, indent=None, separators=(",", ":"))

    meta = {
        "generatedAt": int(time.time()),
        "tickerCount": len(results),
        "requestedCount": len(SCREENER_TICKERS),
        "errors": errors,
    }
    with open(os.path.join(OUTPUT_DIR, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print(f"Wrote {len(results)}/{len(SCREENER_TICKERS)} tickers to data/screener.json")
    if errors:
        print(f"Failed tickers: {', '.join(errors)}")


if __name__ == "__main__":
    main()
