"""
Headline sentiment for FinSTR Autopilot.

Scores every company headline FinSTR already collects with a small finance word list (positive and negative
terms in the style of the Loughran-McDonald finance dictionary, plus "not"/"no" flips). No model download, no
API key, the same answer every run, and every score can be explained by the words that produced it.

Output: data/sentiment.json
    {"asOf": <unix>, "market": {"score", "pos", "neg", "n"},
     "tickers": {"AAPL": {"score", "n", "avg30", "prev30", "trend"}},
     "history": {"2026-11-26": {"AAPL": [0.33, 3], ...}, ...}}  # daily [mean, headlines], last 120 days

score is -1 (all negative) .. +1 (all positive). avg30 is the tone of the last 30 days, prev30 the 30 days before,
so the monthly job can see "negative two months running". Both are shrunk toward 0 when there are few headlines
(sum / (count + 3)), so one cheerful headline cannot outrank a month of steady coverage.
"""

import datetime as dt
import json
import os
import re
import urllib.request

POS = set("""
beat beats beating tops topped record records surge surges surged soar soars soared jump jumps jumped rally rallies
rallied gain gains gained rise rises rising rose climb climbs climbed upgrade upgrades upgraded outperform outperforms
strong stronger strongest growth grow grows grew boost boosts boosted raise raises raised hike hikes win wins won
profit profits profitable expand expands expansion approval approved approves breakthrough bullish optimism optimistic
upbeat rebound rebounds rebounded recover recovers recovery accelerate accelerates exceed exceeds exceeded milestone
partnership launch launches dividend buyback buybacks overweight positive momentum higher highs
""".split())
NEG = set("""
miss misses missed fall falls fell drop drops dropped plunge plunges plunged sink sinks sank slump slumps slumped
tumble tumbles tumbled slide slides slid decline declines declined downgrade downgrades downgraded underperform
underweight weak weaker weakest loss losses lose loses losing cut cuts cutting lower lows warn warns warning
warned lawsuit sue sues sued probe investigation fraud recall recalls layoff layoffs bearish concern concerns fear
fears risk risks crash crashes slowdown slows slowing halt halts delay delays delayed fined penalty bankrupt
bankruptcy default defaults pressure pressured struggle struggles struggling worst negative volatile selloff sell-off
tariff tariffs strike strikes breach hack hacked resign resigns resigned exit exits shortfall
""".split())
NEGATE = {"not", "no", "never", "without", "fails", "failed", "fail"}
WORD = re.compile(r"[a-z][a-z\-']*")
HISTORY_DAYS = 120


def score_text(text):
    """(score, hits): score in -1..1 or None when no sentiment words; hits lists the words that counted."""
    words = WORD.findall((text or "").lower().replace("’", "'"))
    p = n = 0
    hits = []
    for i, w in enumerate(words):
        sign = 1 if w in POS else -1 if w in NEG else 0
        if not sign:
            continue
        if any(x in NEGATE for x in words[max(0, i - 2):i]):
            sign = -sign
        p, n = (p + 1, n) if sign > 0 else (p, n + 1)
        hits.append(("+" if sign > 0 else "-") + w)
    if not p and not n:
        return None, hits
    return round((p - n) / (p + n), 3), hits


def _load_previous(previous_url, ua):
    if not previous_url:
        return None
    try:
        req = urllib.request.Request(previous_url.rstrip("/") + "/data/sentiment.json", headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001  first run, or the site is down: start a fresh history
        return None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def build(rows, today, previous=None):
    """rows: screener rows with 'ticker' and 'news' (list of {title, ts}). today: date. Returns the JSON dict."""
    hist = dict((previous or {}).get("history") or {})
    day = today.isoformat()
    todays, tick = {}, {}
    pos = neg = n_all = 0
    seen = set()
    for r in rows:
        scores = []
        for a in r.get("news") or []:
            s, _ = score_text(a.get("title"))
            if a.get("title") in seen:     # the same story under several tickers counts once for the market
                pass
            else:
                seen.add(a.get("title"))
                n_all += 1
                pos += bool(s and s > 0)
                neg += bool(s and s < 0)
            if s is None:
                continue
            scores.append(s)
        if scores:
            todays[r["ticker"]] = [_mean(scores), len(scores)]
            tick[r["ticker"]] = {"score": todays[r["ticker"]][0], "n": len(scores)}
    hist[day] = todays
    for d, v in hist.items():          # (older copies stored a bare mean per ticker)
        for t, x in list(v.items()):
            if not isinstance(x, list):
                v[t] = [x, 1]
    cutoff = (today - dt.timedelta(days=HISTORY_DAYS)).isoformat()
    hist = {d: v for d, v in sorted(hist.items()) if d >= cutoff}
    d30 = (today - dt.timedelta(days=30)).isoformat()
    d60 = (today - dt.timedelta(days=60)).isoformat()
    every = {t for v in hist.values() for t in v}
    def shrunk(t, lo, hi):
        xs = [v[t] for d, v in hist.items() if lo < d <= hi and t in v]
        n = sum(x[1] for x in xs)
        return (round(sum(x[0] * x[1] for x in xs) / (n + 3), 3), n) if n else (None, 0)
    for t in every:
        (a30, n30), (p30, _) = shrunk(t, d30, day), shrunk(t, d60, d30)
        e = tick.setdefault(t, {"score": None, "n": 0})
        e["avg30"], e["prev30"], e["n30"] = a30, p30, n30
        e["trend"] = (None if a30 is None or p30 is None else
                      "improving" if a30 - p30 > 0.1 else "worsening" if p30 - a30 > 0.1 else "steady")
    return {
        "asOf": int(dt.datetime.now(dt.timezone.utc).timestamp()),
        "method": "Finance word list (Loughran-McDonald style) applied to company headlines; -1 negative .. +1 positive. "
                  "Market score = (positive - negative) / all unique headlines, so neutral news pulls it toward 0. "
                  "Describes the tone of the news, it does not predict prices.",
        "market": {"score": round((pos - neg) / n_all, 3) if n_all else None, "pos": pos, "neg": neg, "n": n_all,
                   "avg30": _mean([_mean([x[0] for x in v.values()]) for d, v in hist.items() if d > d30])},
        "tickers": tick,
        "history": hist,
    }


def mood_label(score):
    if score is None:
        return "No data"
    return ("Positive" if score >= 0.25 else "Slightly positive" if score >= 0.08 else
            "Negative" if score <= -0.25 else "Slightly negative" if score <= -0.08 else "Neutral")


def run(rows, data_dir, today, previous_url=None, ua="FinSTR"):
    out = build(rows, today, _load_previous(previous_url, ua))
    with open(os.path.join(data_dir, "sentiment.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    return out
