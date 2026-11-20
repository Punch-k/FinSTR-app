"""
FinSTR data pipeline.

Builds every file the static site reads, from free Yahoo Finance data:

    data/screener.json        one row per S&P 500 company (~70 fields)
    data/market.json          index / ETF / macro tiles for the home page
    data/groups.json          sector and industry performance
    data/meta.json            run time, counts, failures, data source notes
    data/quote/<TICKER>.json  per-company detail: 2y daily OHLCV, profile, news

Design rules
  * Never publish a made-up number. A field Yahoo does not return stays null
    and the site shows "-".
  * Keep the last good value. If a ticker fails this run, the previous row is
    reused and marked stale (meta.stale), so one bad run never blanks the site.
  * Technicals (performance, SMAs, RSI, volatility, 52-week range) are computed
    here from the price history, so every ticker uses one consistent method.

Run locally:   python pipeline/fetch.py            (all tickers)
               python pipeline/fetch.py --limit 25 (quick test)
"""

import argparse
import csv
import json
import math
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals  # noqa: E402  (pipeline/signals.py)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
QUOTE_DIR = os.path.join(DATA, "quote")
UNIVERSE = os.path.join(ROOT, "pipeline", "universe.csv")
PREVIOUS_URL = os.environ.get("FINSTR_PREVIOUS_URL", "")  # live site, for keep-last-good in CI

WORKERS = 6
NEWS_PER_TICKER = 6
UA = "Mozilla/5.0 (FinSTR data pipeline; +https://github.com/Punch-k/FinSTR-app)"

# Home-page market tiles. Yahoo symbols; label shown on the site.
MARKET = [
    ("SPY", "S&P 500", "index"), ("QQQ", "Nasdaq 100", "index"), ("DIA", "Dow 30", "index"),
    ("IWM", "Russell 2000", "index"), ("^VIX", "VIX", "volatility"), ("^TNX", "10Y Yield", "rates"),
    ("GC=F", "Gold", "commodity"), ("CL=F", "Crude Oil", "commodity"), ("DX-Y.NYB", "US Dollar", "fx"),
    ("BTC-USD", "Bitcoin", "crypto"),
    ("XLK", "Technology", "sector"), ("XLF", "Financials", "sector"), ("XLV", "Health Care", "sector"),
    ("XLY", "Cons. Discretionary", "sector"), ("XLP", "Cons. Staples", "sector"), ("XLI", "Industrials", "sector"),
    ("XLE", "Energy", "sector"), ("XLU", "Utilities", "sector"), ("XLB", "Materials", "sector"),
    ("XLRE", "Real Estate", "sector"), ("XLC", "Communication", "sector"),
]


# Futures / forex / crypto pages. Names must match the UI's FUTURES_MAP etc. in index.html.
FUTURES = {
    "S&P 500 Fut.": "ES=F", "Nasdaq 100 Fut.": "NQ=F", "Dow Jones Fut.": "YM=F", "Russell 2000 Fut.": "RTY=F",
    "VIX": "^VIX", "Crude Oil (WTI)": "CL=F", "Crude Oil (Brent)": "BZ=F", "Natural Gas": "NG=F",
    "Gasoline RBOB": "RB=F", "10Y T-Note": "ZN=F", "30Y T-Bond": "ZB=F", "5Y T-Note": "ZF=F",
    "Coffee": "KC=F", "Sugar": "SB=F", "Cocoa": "CC=F", "Gold": "GC=F", "Silver": "SI=F", "Copper": "HG=F",
    "Live Cattle": "LE=F", "Lean Hogs": "HE=F", "Soybeans": "ZS=F", "Corn": "ZC=F", "Wheat": "ZW=F",
    "USD Index": "DX-Y.NYB", "EUR/USD Fut.": "6E=F", "GBP/USD Fut.": "6B=F",
}
FOREX = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X", "USD/CAD": "USDCAD=X",
    "AUD/USD": "AUDUSD=X", "USD/CHF": "USDCHF=X", "NZD/USD": "NZDUSD=X", "EUR/GBP": "EURGBP=X",
    "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X", "USD/CNY": "USDCNY=X", "USD/MXN": "USDMXN=X",
}
CRYPTO = {
    "Bitcoin": "BTC-USD", "Ethereum": "ETH-USD", "Solana": "SOL-USD", "XRP": "XRP-USD", "Dogecoin": "DOGE-USD",
    "Cardano": "ADA-USD", "Litecoin": "LTC-USD", "Polkadot": "DOT-USD", "Avalanche": "AVAX-USD",
    "Chainlink": "LINK-USD", "Stellar": "XLM-USD", "Toncoin": "TON11419-USD",
}
INDICES = {"idxDow": ("^DJI", "DOW"), "idxNasdaq": ("^IXIC", "NASDAQ"), "idxSP": ("^GSPC", "S&P 500")}
# Headline feeds per News tab (Yahoo RSS accepts index/ETF/futures symbols too).
NEWS_FEEDS = {
    "market": ["^GSPC", "^DJI", "^IXIC", "SPY"],
    "pulse": ["^TNX", "DX-Y.NYB", "GC=F", "CL=F", "TLT"],
    "etf": ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV"],
}


# ───────────────────────── helpers ─────────────────────────
def num(v, nd=2, scale=1.0):
    """Clean float or None. Yahoo returns 'Infinity', NaN, strings, 0-for-missing."""
    try:
        f = float(v) * scale
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def ratio(v):
    """Valuation multiples: Yahoo uses 0 as a placeholder for 'not available'."""
    x = num(v)
    return None if x == 0 else x


def pct_change(series, n):
    s = series.dropna()
    if len(s) <= n:
        return None
    a, b = float(s.iloc[-1 - n]), float(s.iloc[-1])
    return num((b / a - 1) * 100) if a else None


def rsi(series, n=14):
    s = series.dropna()
    if len(s) < n + 1:
        return None
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up.iloc[-1] / dn.iloc[-1] if dn.iloc[-1] else float("inf")
    return num(100 - 100 / (1 + rs), 1)


def iso_date(ts):
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def retry(fn, tries=3, wait=2.0):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - yfinance raises many types
            if i == tries - 1:
                raise
            limited = "Too Many Requests" in str(e) or "Rate limited" in str(e)
            time.sleep((20 if limited else wait) * (i + 1))
    return None


def load_universe():
    with open(UNIVERSE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_previous():
    """Last published screener rows, keyed by ticker (keep-last-good)."""
    prev = {}
    path = os.path.join(DATA, "screener.json")
    try:
        if PREVIOUS_URL:
            with urllib.request.urlopen(urllib.request.Request(PREVIOUS_URL + "/data/screener.json", headers={"User-Agent": UA}), timeout=30) as r:
                rows = json.loads(r.read().decode("utf-8"))
        elif os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
        else:
            rows = []
        # Only reuse rows in the current format (older snapshots lack the computed technicals).
        prev = {r["ticker"]: r for r in rows if isinstance(r, dict) and "perfW" in r}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] no previous data: {e}", file=sys.stderr)
    return prev


# ───────────────────────── prices ─────────────────────────
def download_history(symbols):
    """2 years of daily OHLCV for many symbols in batches. Returns {sym: DataFrame}."""
    out = {}

    def grab(batch):
        df = retry(lambda: yf.download(batch, period="2y", interval="1d", group_by="ticker",
                                       auto_adjust=False, threads=True, progress=False))
        for s in batch:
            try:
                h = df[s].dropna(how="all") if len(batch) > 1 else df.dropna(how="all")
                if len(h.dropna(subset=["Close"])):
                    out[s] = h
            except KeyError:
                pass

    for i in range(0, len(symbols), 100):
        grab(symbols[i:i + 100])
    # Second pass, one by one, for anything a dropped connection lost.
    for s in [s for s in symbols if s not in out]:
        try:
            grab([s, "SPY"] if s != "SPY" else [s])
        except Exception:  # noqa: BLE001
            pass
    return out


def technicals(h):
    c, hi, lo, v = h["Close"], h["High"], h["Low"], h["Volume"]
    last = float(c.dropna().iloc[-1])
    year = h.tail(252)
    ytd = h[h.index >= pd.Timestamp(datetime.now().year, 1, 1)]
    t = {
        "perfW": pct_change(c, 5), "perfM": pct_change(c, 21), "perfQ": pct_change(c, 63),
        "perfH": pct_change(c, 126), "perfY": pct_change(c, 252),
        "perfYTD": num((last / float(ytd["Close"].iloc[0]) - 1) * 100) if len(ytd) > 1 else None,
        "rsi": rsi(c),
        "high52": num(year["High"].max()), "low52": num(year["Low"].min()),
    }
    for n in (20, 50, 200):
        if len(c.dropna()) >= n:
            sma = float(c.tail(n).mean())
            t[f"sma{n}"] = num(sma)
            t[f"vsSma{n}"] = num((last / sma - 1) * 100)
        else:
            t[f"sma{n}"] = t[f"vsSma{n}"] = None
    t["fromHigh52"] = num((last / t["high52"] - 1) * 100) if t["high52"] else None
    t["fromLow52"] = num((last / t["low52"] - 1) * 100) if t["low52"] else None
    ret = c.pct_change().dropna()
    t["volW"] = num(ret.tail(5).std() * 100) if len(ret) >= 5 else None     # daily stdev, %
    t["volM"] = num(ret.tail(21).std() * 100) if len(ret) >= 21 else None
    tr = pd.concat([hi - lo, (hi - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    t["atr"] = num(tr.tail(14).mean())
    t["avgVol3m"] = num(v.tail(63).mean(), 0)
    t["relVol"] = num(float(v.iloc[-1]) / t["avgVol3m"]) if t["avgVol3m"] else None
    t["spark"] = [round(float(x), 2) for x in c.dropna().tail(30)]
    return t


# ───────────────────────── fundamentals ─────────────────────────
def info_row(sym):
    i = retry(lambda: yf.Ticker(sym).info) or {}
    price = num(i.get("currentPrice") or i.get("regularMarketPrice"))
    if not price:
        raise ValueError("no price")
    dps = num(i.get("dividendRate"))
    tgt = num(i.get("targetMeanPrice"))
    return {
        "price": price,
        "change": num(i.get("regularMarketChangePercent")),
        "prevClose": num(i.get("regularMarketPreviousClose") or i.get("previousClose")),
        "open": num(i.get("regularMarketOpen") or i.get("open")),
        "dayLow": num(i.get("regularMarketDayLow") or i.get("dayLow")),
        "dayHigh": num(i.get("regularMarketDayHigh") or i.get("dayHigh")),
        "volume": num(i.get("regularMarketVolume") or i.get("volume"), 0),
        "marketCap": num(i.get("marketCap"), 0),
        "ev": num(i.get("enterpriseValue"), 0),
        "pe": ratio(i.get("trailingPE")), "fpe": ratio(i.get("forwardPE")),
        "peg": ratio(i.get("trailingPegRatio")), "ps": ratio(i.get("priceToSalesTrailing12Months")),
        "pb": ratio(i.get("priceToBook")), "evEbitda": ratio(i.get("enterpriseToEbitda")),
        "evSales": ratio(i.get("enterpriseToRevenue")),
        "pfcf": num(i.get("marketCap") / i["freeCashflow"]) if i.get("freeCashflow") and i.get("marketCap") and i["freeCashflow"] > 0 else None,
        "eps": num(i.get("trailingEps")), "fwdEps": num(i.get("forwardEps")),
        "epsGrowthQ": num(i.get("earningsQuarterlyGrowth"), 1, 100),
        "salesGrowthQ": num(i.get("revenueGrowth"), 1, 100),
        "grossMargin": ratio(num(i.get("grossMargins"), 1, 100)),  # banks report 0 = n/a
        "operMargin": num(i.get("operatingMargins"), 1, 100),
        "profitMargin": num(i.get("profitMargins"), 1, 100),
        "roe": num(i.get("returnOnEquity"), 1, 100), "roa": num(i.get("returnOnAssets"), 1, 100),
        "debtEq": num(i.get("debtToEquity"), 2, 0.01), "currentRatio": num(i.get("currentRatio")),
        "quickRatio": num(i.get("quickRatio")),
        "divYield": num(dps / price * 100) if dps else 0.0,   # forward yield, %
        "divRate": dps, "payout": num(i.get("payoutRatio"), 1, 100),
        "beta": num(i.get("beta")),
        "shares": num(i.get("sharesOutstanding"), 0), "float": num(i.get("floatShares"), 0),
        "insiderOwn": num(i.get("heldPercentInsiders"), 2, 100),
        "instOwn": num(i.get("heldPercentInstitutions"), 2, 100),
        "shortFloat": num(i.get("shortPercentOfFloat"), 2, 100), "shortRatio": num(i.get("shortRatio")),
        "shortInterest": num(i.get("sharesShort"), 0),
        "divTTM": num(i.get("trailingAnnualDividendRate")) or None,
        "divYieldTTM": num(i.get("trailingAnnualDividendYield"), 2, 100) or None,
        "ipo": iso_date((i.get("firstTradeDateMilliseconds") or 0) / 1000),
        "target": tgt, "targetHigh": num(i.get("targetHighPrice")), "targetLow": num(i.get("targetLowPrice")),
        "upside": num((tgt / price - 1) * 100) if tgt else None,
        "recMean": num(i.get("recommendationMean")), "analysts": i.get("numberOfAnalystOpinions"),
        "revenue": num(i.get("totalRevenue"), 0), "ebitda": num(i.get("ebitda"), 0),
        "netIncome": num(i.get("netIncomeToCommon"), 0), "fcf": num(i.get("freeCashflow"), 0),
        "cash": num(i.get("totalCash"), 0), "debt": num(i.get("totalDebt"), 0),
        "bookPerShare": num(i.get("bookValue")), "cashPerShare": num(i.get("totalCashPerShare")),
        "employees": i.get("fullTimeEmployees"),
        "earnings": iso_date(i.get("earningsTimestampStart") or i.get("earningsTimestamp")),
        "exDiv": iso_date(i.get("exDividendDate")),
        "country": i.get("country"), "exchange": i.get("fullExchangeName") or i.get("exchange"),
        "_profile": {
            "summary": i.get("longBusinessSummary"), "website": i.get("website"),
            "city": i.get("city"), "state": i.get("state"), "country": i.get("country"),
            "employees": i.get("fullTimeEmployees"), "yahooIndustry": i.get("industry"),
        },
    }


STOP = {"inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "company", "plc", "ltd", "group", "holdings",
        "the", "class", "a)", "b)", "(class", "&", "and", "international", "technologies", "systems"}


def keywords(sym, name):
    words = [w.strip(",.()").lower() for w in (name or "").split()]
    keys = {w for w in words if len(w) > 2 and w not in STOP}
    return {sym.lower().replace("-", ".")} | keys


def news(sym, name=None):
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
            root = ET.fromstring(r.read())
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in root.iter("item"):
        title, link = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()
        if not title or not link.startswith("http"):
            continue
        pub = it.findtext("pubDate") or ""
        try:
            ts = int(datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %z").timestamp())
        except ValueError:
            ts = None
        host = urllib.parse.urlparse(link).netloc.replace("www.", "")
        out.append({"title": title, "link": link, "source": host, "ts": ts})
    if name:  # company feed: keep headlines that name the company; fall back to Yahoo's top 3
        keys = keywords(sym, name)
        rel = [a for a in out if any(k in a["title"].lower().replace("’", "'").split() or k in a["title"].lower() for k in keys)]
        out = rel if rel else out[:3]
    return out[:NEWS_PER_TICKER]


def insiders(sym, n=6):
    try:
        df = yf.Ticker(sym).insider_transactions
        if df is None or df.empty:
            return []
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for _, r in df.head(n).iterrows():
        sh, val = r.get("Shares"), r.get("Value")
        rows.append({
            "insider": str(r.get("Insider", "") or ""), "position": str(r.get("Position", "") or ""),
            "transaction": str(r.get("Text", "") or r.get("Transaction", "") or ""),
            "shares": int(sh) if sh == sh and sh is not None else 0,
            "value": float(val) if val == val and val is not None else None,
            "date": str(r.get("Start Date", ""))[:10],
        })
    return rows


# ───────────────────────── deep data (financials, estimates, rating changes) ─────────────────────────
# Slow-moving and costly in requests, so it is refreshed about once a day per ticker on a rotation
# (at most DEEP_PER_RUN tickers per run) and otherwise reused from the previous publish.
DEEP_MAX_AGE_H = 20
DEEP_PER_RUN = int(os.environ.get("FINSTR_DEEP_PER_RUN", "200"))
_deep_lock = threading.Lock()
_deep_budget = [DEEP_PER_RUN]


def previous_quote(sym):
    path = os.path.join(QUOTE_DIR, f"{sym}.json")
    try:
        if PREVIOUS_URL:
            req = urllib.request.Request(f"{PREVIOUS_URL}/data/quote/{sym}.json", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:  # noqa: BLE001
        return None
    return None


def _series(df, row, scale=1.0, nd=2):
    if df is None or df.empty or row not in df.index:
        return None
    cols = sorted(df.columns)  # oldest -> newest
    return {"periods": [c.strftime("%Y-%m") for c in cols],
            "values": [num(df.loc[row, c], nd, scale) for c in cols]}


def fetch_deep(sym):
    t = yf.Ticker(sym)
    a = retry(lambda: t.income_stmt)
    q = retry(lambda: t.quarterly_income_stmt)
    deep = {
        "asOf": int(time.time()),
        "annual": {"eps": _series(a, "Diluted EPS"), "sales": _series(a, "Total Revenue", 1e-9, 2),
                   "shares": _series(a, "Diluted Average Shares", 1e-9, 3), "netIncome": _series(a, "Net Income", 1e-9, 2)},
        "quarterly": {"eps": _series(q, "Diluted EPS"), "sales": _series(q, "Total Revenue", 1e-9, 2),
                      "shares": _series(q, "Diluted Average Shares", 1e-9, 3), "netIncome": _series(q, "Net Income", 1e-9, 2)},
        "ratings": [], "estimates": {}, "surprises": [], "ltg": None,
    }
    try:
        u = t.upgrades_downgrades
        if u is not None and not u.empty:
            for d, r in u.sort_index(ascending=False).head(25).iterrows():
                deep["ratings"].append({
                    "date": d.strftime("%Y-%m-%d"), "firm": str(r.get("Firm") or ""), "action": str(r.get("Action") or ""),
                    "from": str(r.get("FromGrade") or ""), "to": str(r.get("ToGrade") or ""),
                    "ptAction": str(r.get("priceTargetAction") or ""),
                    "pt": num(r.get("currentPriceTarget")) or None, "priorPt": num(r.get("priorPriceTarget")) or None})
    except Exception:  # noqa: BLE001
        pass
    try:
        e = t.earnings_estimate
        for k, label in (("0q", "thisQ"), ("+1q", "nextQ"), ("0y", "thisY"), ("+1y", "nextY")):
            if e is not None and k in e.index:
                r = e.loc[k]
                deep["estimates"][label] = {"avg": num(r.get("avg")), "low": num(r.get("low")), "high": num(r.get("high")),
                                            "n": int(r["numberOfAnalysts"]) if r.get("numberOfAnalysts") == r.get("numberOfAnalysts") and r.get("numberOfAnalysts") is not None else None,
                                            "growth": num(r.get("growth"), 2, 100), "yearAgo": num(r.get("yearAgoEps"))}
    except Exception:  # noqa: BLE001
        pass
    try:
        h = t.earnings_history
        if h is not None and not h.empty:
            for d, r in h.sort_index().iterrows():
                deep["surprises"].append({"period": d.strftime("%Y-%m"), "actual": num(r.get("epsActual")),
                                          "estimate": num(r.get("epsEstimate")), "surprise": num(r.get("surprisePercent"), 2, 100)})
    except Exception:  # noqa: BLE001
        pass
    try:
        g = t.growth_estimates
        if g is not None and "LTG" in g.index:
            deep["ltg"] = num(g.loc["LTG", "stockTrend"], 2, 100)
    except Exception:  # noqa: BLE001
        pass
    return deep


def get_deep(sym):
    """Fresh-enough previous deep data, else a new fetch while this run's budget lasts."""
    prev = previous_quote(sym) or {}
    old = prev.get("deep")
    if old and time.time() - old.get("asOf", 0) < DEEP_MAX_AGE_H * 3600:
        return old
    with _deep_lock:
        if _deep_budget[0] <= 0:
            return old  # may be None or older; refreshed on a later run
        _deep_budget[0] -= 1
    try:
        return fetch_deep(sym)
    except Exception as e:  # noqa: BLE001
        print(f"  [deep] {sym}: {e}", file=sys.stderr)
        return old


def deep_fields(d):
    """Screener columns derived from the deep data (Finviz's EPS this/next Y, next 5Y, surprise...)."""
    if not d:
        return {}
    est, sur = d.get("estimates") or {}, d.get("surprises") or []
    last = sur[-1] if sur else {}
    ann = (d.get("annual") or {}).get("sales") or {}
    vals = [v for v in (ann.get("values") or []) if v]
    sales3y = num(((vals[-1] / vals[-4]) ** (1 / 3) - 1) * 100) if len(vals) >= 4 and vals[-4] > 0 else None
    return {
        "epsNextQ": (est.get("thisQ") or {}).get("avg"), "epsThisY": (est.get("thisY") or {}).get("growth"),
        "epsNextYGrowth": (est.get("nextY") or {}).get("growth"), "epsNext5Y": d.get("ltg"),
        "epsSurprise": last.get("surprise"), "salesPast3Y": sales3y,
    }


def compat(r):
    """Field names and units the existing index.html expects, next to the new fields."""
    c = dict(r)
    c["marketCap"] = num((r.get("marketCap") or 0) / 1e9, 1)        # $B
    c["marketCapRaw"] = r.get("marketCap")
    c["changePercent"] = r.get("change")
    c["forwardPE"] = r.get("fpe")
    c["ma50"] = r.get("sma50")
    c["dividendYield"] = r.get("divYield")
    c["volume"] = num((r.get("volume") or 0) / 1e6, 2)               # millions
    c["avgVolume"] = num((r.get("avgVol3m") or 0) / 1e6, 2)
    c["earningsDate"] = r.get("earnings")
    c["exDividendDate"] = r.get("exDiv")
    return c


def tile(name, sym, h):
    c = h["Close"].dropna()
    last, prev = float(c.iloc[-1]), float(c.iloc[-2]) if len(c) > 1 else float(c.iloc[-1])
    lastbar = h.dropna(subset=["Close"]).iloc[-1]
    return {
        "name": name, "symbol": sym, "price": num(last, 4), "change": num(last - prev, 4),
        "changePercent": num((last / prev - 1) * 100) if prev else 0,
        "dayHigh": num(lastbar["High"], 4), "dayLow": num(lastbar["Low"], 4),
        "volume": int(lastbar["Volume"]) if lastbar["Volume"] == lastbar["Volume"] else 0,
        "perfW": pct_change(c, 5), "perfM": pct_change(c, 21), "perfY": pct_change(c, 252),
        "spark": [round(float(x), 4) for x in c.tail(60)],
        "dates": [d.strftime("%Y-%m-%d") for d in c.tail(60).index],
    }


# ───────────────────────── build ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the first N tickers (testing)")
    args = ap.parse_args()

    os.makedirs(QUOTE_DIR, exist_ok=True)
    started = time.time()
    universe = load_universe()
    if args.limit:
        universe = universe[: args.limit]
    prev = load_previous()
    syms = [u["ticker"] for u in universe]

    extra = sorted({m[0] for m in MARKET} | set(FUTURES.values()) | set(FOREX.values())
                   | set(CRYPTO.values()) | {v[0] for v in INDICES.values()})
    print(f"history: {len(syms)} tickers + {len(extra)} market symbols")
    hist = download_history(syms + extra)

    rows, failed, stale = [], [], []

    def work(u):
        s = u["ticker"]
        row = {"ticker": s, "name": u["name"], "sector": u["sector"], "industry": u["industry"]}
        row.update(info_row(s))
        h = hist.get(s)
        if h is None or len(h) < 30:
            raise ValueError("no history")
        row.update(technicals(h))
        row["news"] = news(s, u["name"])
        row["insiderTransactions"] = insiders(s)
        row["_deep"] = get_deep(s)
        row.update(deep_fields(row["_deep"]))
        return row, h, row["news"]

    def run(batch, workers):
        results, errs = [], []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(work, u): u for u in batch}
            for fu in as_completed(futs):
                try:
                    results.append(fu.result())
                except Exception as e:  # noqa: BLE001
                    errs.append((futs[fu], e))
        return results, errs

    done, errs = run(universe, WORKERS)
    if errs:  # Yahoo throttles bursts: cool down, then retry the misses slowly
        print(f"retrying {len(errs)} after cool-down", file=sys.stderr)
        time.sleep(60)
        more, errs = run([u for u, _ in errs], 2)
        done += more
    for u, e in errs:
        failed.append(u["ticker"])
        if u["ticker"] in prev:
            old = dict(prev[u["ticker"]])
            old["stale"] = True
            rows.append(old)
            stale.append(u["ticker"])
        print(f"  [fail] {u['ticker']}: {e}", file=sys.stderr)
    deep_map = {}
    for row, h, nw in done:
        profile = row.pop("_profile")
        deep = row.pop("_deep", None)
        deep_map[row["ticker"]] = deep
        rows.append(compat(row))
        h = h.dropna(subset=["Close"])
        quote = {
            "ticker": row["ticker"], "profile": profile, "news": nw, "deep": deep,
            "history": {
                "d": [d.strftime("%Y-%m-%d") for d in h.index],
                "o": [round(float(x), 2) for x in h["Open"]], "h": [round(float(x), 2) for x in h["High"]],
                "l": [round(float(x), 2) for x in h["Low"]], "c": [round(float(x), 2) for x in h["Close"]],
                "v": [int(x) if x == x else 0 for x in h["Volume"]],
            },
        }
        with open(os.path.join(QUOTE_DIR, f"{row['ticker']}.json"), "w", encoding="utf-8") as f:
            json.dump(quote, f, separators=(",", ":"))

    rows.sort(key=lambda r: r.get("marketCap") or 0, reverse=True)

    # signal engine: factor scores, composite, reasons; price-factor backtest
    signals.score_rows(rows, deep_map)
    try:
        bt = signals.backtest(hist, [r["ticker"] for r in rows])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] backtest failed: {e}", file=sys.stderr)
        bt = None
    dist = {}
    for r in rows:
        dist[r.get("signalLabel") or "n/a"] = dist.get(r.get("signalLabel") or "n/a", 0) + 1
    with open(os.path.join(DATA, "signals.json"), "w", encoding="utf-8") as f:
        json.dump({"asOf": int(time.time()), "weights": signals.WEIGHTS, "distribution": dist, "backtest": bt}, f, separators=(",", ":"))

    with open(os.path.join(DATA, "screener.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, separators=(",", ":"))

    # futures / forex / crypto / index cards
    def tiles(mapping):
        return [tile(n, s, hist[s]) for n, s in mapping.items() if s in hist and len(hist[s].dropna(subset=["Close"])) > 1]
    markets = {
        "futures": tiles(FUTURES), "forex": tiles(FOREX), "crypto": tiles(CRYPTO),
        "indices": [dict(tile(lbl, sym, hist[sym]), id=i) for i, (sym, lbl) in INDICES.items() if sym in hist],
    }
    with open(os.path.join(DATA, "markets.json"), "w", encoding="utf-8") as f:
        json.dump(markets, f, separators=(",", ":"))

    # benchmark (S&P 500 via SPY) daily closes, for "portfolio vs S&P since purchase"
    if "SPY" in hist:
        b = hist["SPY"]["Close"].dropna()
        with open(os.path.join(DATA, "benchmark.json"), "w", encoding="utf-8") as f:
            json.dump({"symbol": "SPY", "d": [d.strftime("%Y-%m-%d") for d in b.index], "c": [round(float(x), 2) for x in b]}, f, separators=(",", ":"))

    # news tabs: market / pulse / etf from index & macro feeds, stocks from company feeds
    def dedupe(items):
        seen, out = set(), []
        for a in sorted(items, key=lambda a: -(a.get("ts") or 0)):
            k = a["title"].lower()
            if k not in seen:
                seen.add(k)
                out.append(a)
        return out
    def as_ui(items, tag=None):
        return [{"title": a["title"], "link": a["link"], "publisher": a["source"], "ticker": a.get("ticker") or tag,
                 "date": datetime.fromtimestamp(a["ts"], tz=timezone.utc).isoformat() if a.get("ts") else None}
                for a in items]
    feed_news = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        syms_needed = sorted({s for v in NEWS_FEEDS.values() for s in v})
        for s, items in zip(syms_needed, ex.map(news, syms_needed)):
            feed_news[s] = items
    company = [dict(a, ticker=r["ticker"]) for r in rows[:150] for a in (r.get("news") or [])]
    movers = sorted([r for r in rows if r.get("change") is not None], key=lambda r: -abs(r["change"]))[:15]
    mover_news = [dict(a, ticker=r["ticker"]) for r in movers for a in (r.get("news") or [])[:2]]
    news_tabs = {}
    for tab, feeds in NEWS_FEEDS.items():
        news_tabs[tab] = {"main": as_ui(dedupe([a for s in feeds for a in feed_news.get(s, [])])[:45]),
                          "blogs": as_ui(dedupe(mover_news)[:12])}
    news_tabs["stocks"] = {"main": as_ui(dedupe(company)[:60]), "blogs": as_ui(dedupe(mover_news)[:12])}
    with open(os.path.join(DATA, "news.json"), "w", encoding="utf-8") as f:
        json.dump(news_tabs, f, separators=(",", ":"))

    # market tiles
    market = []
    for sym, label, kind in MARKET:
        h = hist.get(sym)
        if h is None or len(h) < 2:
            continue
        c = h["Close"].dropna()
        market.append({
            "symbol": sym, "label": label, "kind": kind, "last": num(c.iloc[-1]),
            "change": pct_change(c, 1), "perfW": pct_change(c, 5), "perfM": pct_change(c, 21),
            "perfYTD": technicals(h)["perfYTD"], "perfY": pct_change(c, 252),
            "spark": [round(float(x), 2) for x in c.tail(60)],
        })
    with open(os.path.join(DATA, "market.json"), "w", encoding="utf-8") as f:
        json.dump(market, f, separators=(",", ":"))

    # sector / industry groups, market-cap weighted
    def groups(key):
        acc = {}
        for r in rows:
            g = acc.setdefault(r[key], {"name": r[key], "count": 0, "marketCap": 0, "_w": {}, "adv": 0, "dec": 0})
            mc = r.get("marketCap") or 0
            g["count"] += 1
            g["marketCap"] += mc
            if (r.get("change") or 0) > 0:
                g["adv"] += 1
            elif (r.get("change") or 0) < 0:
                g["dec"] += 1
            for f in ("change", "perfW", "perfM", "perfQ", "perfH", "perfYTD", "perfY", "pe", "fpe", "divYield"):
                if r.get(f) is not None:
                    w = g["_w"].setdefault(f, [0.0, 0.0])
                    w[0] += r[f] * mc
                    w[1] += mc
        out = []
        for g in acc.values():
            for f, (s, w) in g.pop("_w").items():
                g[f] = num(s / w) if w else None
            out.append(g)
        return sorted(out, key=lambda g: -g["marketCap"])
    if rows:
        with open(os.path.join(DATA, "groups.json"), "w", encoding="utf-8") as f:
            json.dump({"sectors": groups("sector"), "industries": groups("industry")}, f, separators=(",", ":"))

    meta = {
        "generatedAt": int(time.time()), "seconds": round(time.time() - started),
        "tickers": len(rows), "requested": len(universe), "failed": failed, "stale": stale,
        "source": "Yahoo Finance (prices, fundamentals, analyst data) via yfinance; Yahoo Finance RSS (headlines). "
                  "Universe: S&P 500 constituents with GICS sectors (Wikipedia list).",
    }
    with open(os.path.join(DATA, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"done: {len(rows)}/{len(universe)} rows, {len(failed)} failed ({len(stale)} kept from last run), "
          f"{meta['seconds']}s")
    # Fail the job only if most of the universe failed, so the site keeps the last good deploy.
    if len(rows) < 0.8 * len(universe):
        sys.exit(1)


if __name__ == "__main__":
    main()
