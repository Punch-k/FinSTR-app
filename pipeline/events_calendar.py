"""Earnings and US economic-event calendar for the FinSTR Calendar page -> data/calendar.json.

Window: Monday of last week through Friday four weeks ahead (the page shows Last Week, This Week,
Next Week and three more weeks). Source: Yahoo Finance calendars via yfinance (earnings: date, timing,
market cap, EPS estimate / reported / surprise; economic events: time, period, actual, expected, prior).
Yahoo gives no revenue figures for earnings and no impact rating for economic releases, so revenue is
left out and "impact" is FinSTR's own rule (a fixed list of market-moving US releases).

If Yahoo fails, the previously published calendar is kept (keep-last-good), like the rest of the data.
"""
import datetime as dt
import json
import math
import os
import re
import sys
import urllib.request
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

HIGH = re.compile(r"\b(cpi|core cpi|consumer price|nonfarm|non-farm|payrolls|unemployment rate|fomc|fed funds|interest rate decision|"
                  r"gdp|pce|core pce|retail sales mm|retail sales m/m|ism manufacturing|ism n-mfg|ism services|ppi|producer price)\b", re.I)
MEDIUM = re.compile(r"\b(jobless claims|initial claims|consumer confidence|consumer sentiment|michigan|durable goods|housing starts|"
                    r"building permits|new home sales|existing home sales|industrial production|adp|jolts|job openings|"
                    r"trade balance|personal income|personal spending|chicago pmi|philly fed|empire state|retail sales|"
                    r"factory orders|pending home|productivity|unit labor|employment cost|import prices|fed .*speech|powell)\b", re.I)


# 5-letter tickers ending in F/Y (foreign OTC), Q (bankruptcy), D (temporary post-split), E (delinquent filer),
# Z (other OTC): not exchange-listed common stock. S&P 500 members are always kept.
OTC = re.compile(r"^[A-Z]{4}[DEFQYZ]$")
try:
    import csv
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe.csv"), encoding="utf-8") as _f:
        SP500 = {row[0] for row in csv.reader(_f)}
except OSError:
    SP500 = set()


def num(v, d=2):
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else round(f, d)
    except (TypeError, ValueError):
        return None


def window(today=None):
    today = today or dt.datetime.now(ET).date()
    mon = today - dt.timedelta(days=today.weekday())
    return mon - dt.timedelta(days=7), mon + dt.timedelta(days=4 * 7 + 4)   # last Monday .. Friday +4 weeks


def paged(fn, pages=30):
    import pandas as pd
    out = []
    for i in range(pages):
        df = fn(limit=100, offset=i * 100)
        if df is None or df.empty:
            break
        out.append(df)
        if len(df) < 100:
            break
    return pd.concat(out) if out else None


def impact(name):
    return 3 if HIGH.search(name or "") else 2 if MEDIUM.search(name or "") else 1


def build(start, end):
    import yfinance as yf
    cal = yf.Calendars(start=start, end=end + dt.timedelta(days=1))
    earn, econ = [], []
    df = paged(lambda **k: cal.get_earnings_calendar(filter_most_active=False, start=start, end=end + dt.timedelta(days=1), **k))
    if df is not None:
        seen = set()
        for sym, r in df.iterrows():
            ts = r.get("Event Start Date")
            if ts is None or str(ts) == "NaT":
                continue
            d = ts.tz_convert(ET).date() if getattr(ts, "tzinfo", None) else ts.date()
            if OTC.match(str(sym)) and str(sym) not in SP500:            # over-the-counter foreign listings (e.g. TNABF, AMMHY); Finviz lists exchange stocks only
                continue
            key = (str(sym), d)
            if key in seen or not (start <= d <= end):
                continue
            seen.add(key)
            timing = str(r.get("Timing") or "")
            earn.append({"t": str(sym), "name": str(r.get("Company") or ""), "d": d.isoformat(),
                         "time": timing if timing in ("BMO", "AMC") else "",
                         "mcap": num(r.get("Marketcap"), 0), "epsEst": num(r.get("EPS Estimate")),
                         "epsAct": num(r.get("Reported EPS")), "sur": num(r.get("Surprise(%)"))})
    df = paged(lambda **k: cal.get_economic_events_calendar(start=start, end=end + dt.timedelta(days=1), **k))
    if df is not None:
        for name, r in df.iterrows():
            if str(r.get("Region") or "") != "US":
                continue
            ts = r.get("Event Time")
            if ts is None or str(ts) == "NaT":
                continue
            t = ts.tz_convert(ET) if getattr(ts, "tzinfo", None) else ts.replace(tzinfo=dt.timezone.utc).astimezone(ET)
            if not (start <= t.date() <= end):
                continue
            ev = re.sub(r"\*+$", "", str(name)).strip()
            econ.append({"d": t.date().isoformat(), "time": t.strftime("%H:%M"), "event": ev, "for": str(r.get("For") or ""),
                         "actual": num(r.get("Actual"), 3), "expected": num(r.get("Expected"), 3), "prior": num(r.get("Last"), 3),
                         "impact": impact(ev)})
    earn.sort(key=lambda x: (x["d"], x["time"] != "BMO", x["t"]))
    econ.sort(key=lambda x: (x["d"], x["time"], x["event"]))
    return earn, econ


def previous(site):
    try:
        req = urllib.request.Request(f"{site.rstrip('/')}/data/calendar.json", headers={"User-Agent": "finstr-calendar"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001
        return None


def run(out_dir="data", site=None):
    start, end = window()
    try:
        earn, econ = build(start, end)
        if not earn and not econ:
            raise ValueError("Yahoo returned no calendar rows")
        doc = {"asOf": int(dt.datetime.now(dt.timezone.utc).timestamp()), "start": start.isoformat(), "end": end.isoformat(),
               "earnings": earn, "economic": econ, "source": "Yahoo Finance calendars; impact rating is FinSTR's rule"}
        print(f"calendar: {len(earn)} earnings, {len(econ)} US economic events, {start}..{end}")
    except Exception as e:  # noqa: BLE001
        doc = previous(site) if site else None
        print(f"[warn] calendar fetch failed ({e}); {'kept the last published copy' if doc else 'no previous copy'}", file=sys.stderr)
        if not doc:
            return
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "calendar.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))


if __name__ == "__main__":
    run(site=os.environ.get("FINSTR_PREVIOUS_URL") or os.environ.get("FINSTR_SITE"))
