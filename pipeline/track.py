"""
Live, out-of-sample record of the FinSTR Signal (data/track.json).

Each trading day the pipeline saves which S&P 500 stocks are in the top fifth (signal 80+) and bottom fifth
(below 20). Each saved list is then held, equal-weight, from that day's close to the next trading day's close
and compared with every S&P 500 stock equal-weight. Picks are saved before the returns they are measured on,
so this record cannot be fitted after the fact. It complements the backtest in signals.json.

The site is rebuilt from scratch on every run, so the record is carried forward from the live site
(FINSTR_PREVIOUS_URL) with an Actions-cache copy as backup. If neither can be read, the run stops rather
than silently starting a new record.
"""

import json
import math
import os
import time
import urllib.error
import urllib.request

import pandas as pd

METHOD = ("Each trading day FinSTR saves which S&P 500 stocks are in the top fifth (signal 80+) and the bottom fifth "
          "(below 20). Each list is held equal-weight from that day's close to the next trading day's close and compared "
          "with all S&P 500 stocks equal-weight. Picks are saved before the returns they are measured on, so the record "
          "cannot be fitted after the fact. Price returns, before costs and dividends; weeks of data are mostly noise.")


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def load(previous_url, cache_path, ua):
    """Returns (record or None, live_ok). live_ok is False when the live copy exists but could not be read."""
    found, live_ok = [], True
    if previous_url:
        live_ok = False
        for attempt in range(3):
            try:
                req = urllib.request.Request(previous_url.rstrip("/") + "/data/track.json", headers={"User-Agent": ua})
                with urllib.request.urlopen(req, timeout=30) as r:
                    found.append(json.loads(r.read().decode("utf-8")))
                live_ok = True
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:          # not published yet: a first run
                    live_ok = True
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(5 * (attempt + 1))
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                found.append(json.load(f))
        except Exception:  # noqa: BLE001
            pass
    best = max(found, key=lambda t: len(t.get("snapshots") or []), default=None)
    return best, live_ok


def update(prev, rows, hist, day, provisional=False):
    """Adds/replaces today's picks (the last run of a day wins) and re-measures the whole record."""
    snaps = {s["date"]: s for s in (prev or {}).get("snapshots", [])}
    top = sorted(r["ticker"] for r in rows if _isnum(r.get("signal")) and r["signal"] >= 80)
    bottom = sorted(r["ticker"] for r in rows if _isnum(r.get("signal")) and r["signal"] < 20)
    if top and bottom:
        snaps[day] = {"date": day, "top": top, "bottom": bottom}
    snaps = [snaps[d] for d in sorted(snaps)]
    series, stats = evaluate(snaps, hist, [r["ticker"] for r in rows])
    if stats is not None:
        stats["provisional"] = bool(provisional and series and series[-1]["d"] == day)
    return {"version": 1, "started": snaps[0]["date"] if snaps else day, "updated": day, "method": METHOD,
            "stats": stats, "series": series, "snapshots": snaps}


def evaluate(snaps, hist, tickers):
    if not snaps:
        return [], None
    start = snaps[0]["date"]
    px = pd.DataFrame({t: hist[t]["Close"] for t in tickers if t in hist}).sort_index()
    px = px[px.index >= pd.Timestamp(start)]
    vals = {"top": 100.0, "bottom": 100.0, "universe": 100.0}
    if px.empty:
        return [], {"days": 0, "snapshots": len(snaps)}
    series = [{"d": px.index[0].strftime("%Y-%m-%d"), **vals}]
    ret = px.pct_change()
    days = wins = 0
    k = -1
    for i in range(1, len(px)):
        prev_day = px.index[i - 1].strftime("%Y-%m-%d")
        while k + 1 < len(snaps) and snaps[k + 1]["date"] <= prev_day:
            k += 1
        if k < 0:
            continue
        row = ret.iloc[i]
        r_top = float(row.reindex(snaps[k]["top"]).mean())
        r_bot = float(row.reindex(snaps[k]["bottom"]).mean())
        r_all = float(row.mean())
        if any(pd.isna(x) for x in (r_top, r_bot, r_all)):
            continue
        vals["top"] *= 1 + r_top
        vals["bottom"] *= 1 + r_bot
        vals["universe"] *= 1 + r_all
        days += 1
        wins += int(r_top > r_all)
        series.append({"d": px.index[i].strftime("%Y-%m-%d"), **{k2: round(v, 3) for k2, v in vals.items()}})
    stats = {"days": days, "snapshots": len(snaps),
             "cumTop": round(vals["top"] - 100, 2), "cumBottom": round(vals["bottom"] - 100, 2),
             "cumUniverse": round(vals["universe"] - 100, 2),
             "hitRate": round(wins / days * 100, 1) if days else None}
    return series, stats
