"""
FinSTR Digest: email your paper portfolio's review on a schedule.

Open source and self-hostable. Standard library only.

    python pipeline/digest.py --portfolio finstr-portfolio.json --mode weekly --out digest.html
    python pipeline/digest.py --portfolio finstr-portfolio.json --mode auto --send

Inputs
  --portfolio   the JSON file exported from the FinSTR Portfolio page (or env PORTFOLIO_JSON with its contents)
  --data        where to read FinSTR data: the live site URL (default) or a local folder with data/
  --mode        weekly | daily | alerts | auto (auto = what the portfolio's email preferences ask for today)
  --state       optional JSON file remembering last run's signals and screen matches (for "changed" alerts)

AI write-up (optional): set LLM_BASE_URL to any OpenAI-compatible endpoint, e.g.
  http://localhost:11434/v1 (Ollama), plus LLM_MODEL (default llama3.2) and LLM_API_KEY if needed.
The model only receives the computed facts. Its text is checked (numbers, tickers, sectors, S&P comparison,
gain or loss direction); if any check fails the rule-based summary is used instead.

Delivery (--send goes to every channel that is configured; set at least one):
  Email     SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASS, MAIL_TO, MAIL_FROM (defaults to SMTP_USER)
  Telegram  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  Slack / Discord / any chat webhook   WEBHOOK_URL
  Phone push via ntfy (free, no account)   NTFY_TOPIC, optional NTFY_SERVER (default https://ntfy.sh)
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.request
from email.message import EmailMessage

DEFAULT_DATA = "https://punch-k.github.io/FinSTR-app"
UA = "FinSTR-digest/1.0 (+https://github.com/Punch-k/FinSTR-app)"


# ───────────────────────── data ─────────────────────────
def load(base, path):
    if base.startswith("http"):
        req = urllib.request.Request(f"{base.rstrip('/')}/{path}", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    with open(os.path.join(base, path), encoding="utf-8") as f:
        return json.load(f)


def close_on(series_d, series_c, date):
    ans = None
    for d, c in zip(series_d, series_c):
        if d <= date:
            ans = c
        else:
            break
    return ans


def isnum(v):
    return isinstance(v, (int, float)) and v == v


# ───────────────────────── analysis (mirrors the site's Portfolio page) ─────────────────────────
def analyse(pf, rows, bench, base):
    by = {r["ticker"]: r for r in rows}
    hold, value, cost, day = [], 0.0, 0.0, 0.0
    for h in pf.get("holdings", []):
        s = by.get(h["ticker"])
        if not s:
            continue
        v, c = h["shares"] * s["price"], h["shares"] * h["cost"]
        prev = s.get("prevClose") or s["price"] / (1 + (s.get("changePercent") or 0) / 100)
        r = {"h": h, "s": s, "value": v, "cost": c, "pnl": v - c, "pnlPct": (v / c - 1) * 100, "day": h["shares"] * (s["price"] - prev)}
        if h.get("date") and bench:
            b0 = close_on(bench["d"], bench["c"], h["date"])
            if b0:
                r["spyPct"] = (bench["c"][-1] / b0 - 1) * 100
                r["vsSpy"] = (s["price"] / h["cost"] - 1) * 100 - r["spyPct"]
        hold.append(r)
        value += v; cost += c; day += r["day"]
    for r in hold:
        r["weight"] = r["value"] / value * 100 if value else 0
    hold.sort(key=lambda r: -r["value"])
    sectors = {}
    for r in hold:
        sectors[r["s"]["sector"]] = sectors.get(r["s"]["sector"], 0) + r["value"]
    wsum = lambda key: (sum(r["value"] * r["s"][key] for r in hold if isnum(r["s"].get(key))) /
                        max(1e-9, sum(r["value"] for r in hold if isnum(r["s"].get(key))))) if hold else None
    dated = [r for r in hold if "vsSpy" in r]
    excess = sum(r["cost"] * r["vsSpy"] for r in dated) / sum(r["cost"] for r in dated) if dated else None
    spy = (sum(r["cost"] * r["spyPct"] for r in dated) / sum(r["cost"] for r in dated)) if dated else None

    # trigger alerts: big daily drop, 200-day cross, earnings soon, signal flip (needs state)
    alerts = []
    today = dt.date.today()
    for r in hold:
        s, t = r["s"], r["s"]["ticker"]
        if isnum(s.get("changePercent")) and s["changePercent"] <= -7:
            alerts.append(f"{t} fell {s['changePercent']:.1f}% on the day.")
        if s.get("earningsDate"):
            d = dt.date.fromisoformat(s["earningsDate"])
            if 0 <= (d - today).days <= 3:
                alerts.append(f"{t} reports earnings on {d:%a %b %d}.")
        try:
            q = load(base, f"data/quote/{t}.json")["history"]
            c = q["c"]
            if len(c) > 201:
                sma_now, sma_prev = sum(c[-200:]) / 200, sum(c[-201:-1]) / 200
                if (c[-1] - sma_now) * (c[-2] - sma_prev) < 0:
                    alerts.append(f"{t} crossed {'above' if c[-1] > sma_now else 'below'} its 200-day average ({sma_now:.2f}).")
        except Exception:  # noqa: BLE001
            pass

    cons = []
    for r in hold:
        s, t = r["s"], r["s"]["ticker"]
        if r["weight"] > 25:
            trim = int((r["value"] - 0.20 * value) / s["price"])
            cons.append(f"{t} is {r['weight']:.0f}% of the portfolio; above 25% in one stock concentrates risk. "
                        f"Selling about {trim} share{'s' if trim != 1 else ''} would bring it to 20%.")
        if isnum(s.get("signal")) and s["signal"] < 20 and isnum(s.get("vsSma200")) and s["vsSma200"] < 0:
            cons.append(f"{t} has a Very Weak signal ({s['signal']}) and is {s['vsSma200']:.1f}% below its 200-day average.")
        if r["pnlPct"] <= -15 and isnum(s.get("signal")) and s["signal"] < 40:
            cons.append(f"{t} is down {abs(r['pnlPct']):.1f}% from cost with a weak signal ({s['signal']}).")
        if isnum(s.get("rsi")) and s["rsi"] >= 75 and r["pnlPct"] >= 20:
            cons.append(f"{t} is up {r['pnlPct']:.0f}% and overbought (RSI {s['rsi']:.0f}).")
        if isnum(s.get("signal")) and s["signal"] >= 80 and r["weight"] < 5:
            cons.append(f"{t} has a Strong signal ({s['signal']}) at only {r['weight']:.1f}% weight.")
    if sectors and value:
        top = max(sectors.items(), key=lambda kv: kv[1])
        if top[1] / value > 0.4:
            cons.append(f"{top[0]} is {top[1] / value * 100:.0f}% of the portfolio.")

    held = {r["s"]["ticker"] for r in hold}
    wsec = lambda sec: sectors.get(sec, 0) / value * 100 if value else 0
    ideas, per = [], {}
    def reports_soon(x):  # skip ideas that report within 5 days: results can gap the price either way
        try:
            return -1 <= (dt.date.fromisoformat(x.get("earningsDate") or "") - today).days <= 5
        except ValueError:
            return False
    for s in sorted((x for x in rows if x["ticker"] not in held and isnum(x.get("signal")) and x["signal"] >= 80
                     and wsec(x["sector"]) < 10 and not reports_soon(x)),
                    key=lambda x: (wsec(x["sector"]), -x["signal"])):
        if per.get(s["sector"], 0) < 2:
            ideas.append(s); per[s["sector"]] = per.get(s["sector"], 0) + 1
        if len(ideas) == 5:
            break
    upcoming = sorted(((r["s"]["ticker"], r["s"]["earningsDate"]) for r in hold if r["s"].get("earningsDate")
                       and 0 <= (dt.date.fromisoformat(r["s"]["earningsDate"]) - today).days <= 14), key=lambda x: x[1])
    return {"holdings": hold, "value": value, "cost": cost, "day": day, "excess": excess, "spy": spy,
            "signal": wsum("signal"), "beta": wsum("beta"), "sectors": sectors,
            "alerts": alerts, "considerations": cons, "ideas": ideas, "earnings": upcoming}


def load_state(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    return st if "labels" in st or "screens" in st else {"labels": st}   # older files held labels only


def signal_flips(A, state):
    prev, now, flips = state.get("labels", {}), {}, []
    for r in A["holdings"]:
        t, lab = r["s"]["ticker"], r["s"].get("signalLabel")
        now[t] = lab
        if prev.get(t) and lab and prev[t] != lab:
            flips.append(f"{t} signal changed from {prev[t]} to {lab} ({r['s'].get('signal')}).")
    state["labels"] = now
    return flips


def rule_ok(s, r):
    """Same rule format and meaning as the site (index.html, ruleOk): a missing value never passes."""
    f, op = r[0], r[1]
    a = r[2] if len(r) > 2 else None
    b = r[3] if len(r) > 3 else None
    v = s.get(f)
    if op == "text":
        q = str(a).lower()
        return q in s["ticker"].lower() or q in (s.get("name") or "").lower()
    if op == "in":
        return isinstance(a, list) and v in a
    if op == "eq":
        return v == a
    if op == "falsy":
        return not v
    if op == "days":
        try:
            d = dt.datetime.combine(dt.date.fromisoformat(v), dt.time(12))
        except (TypeError, ValueError):
            return False
        return a <= (d - dt.datetime.now()).total_seconds() / 86400 <= b
    if not isnum(v):
        return False
    if op.endswith("f"):
        w = s.get(a)
        return isnum(w) and (v > w if op == ">f" else v >= w if op == ">=f" else v < w)
    return {"<": v < a, "<=": v <= a, ">": v > a, ">=": v >= a, "abs<=": abs(v) <= a}.get(op, False)


def screen_alerts(pf, rows, state):
    """Saved screener filters (Portfolio > Screen alerts): report stocks that entered since the last run."""
    prev, now, out = state.get("screens", {}), {}, []
    for sc in (pf.get("screens") or [])[:20]:
        rules = sc.get("rules") or []
        if not rules or not sc.get("id"):
            continue
        hits = sorted(r["ticker"] for r in rows if all(rule_ok(r, x) for x in rules))
        now[sc["id"]] = hits
        if sc["id"] in prev:
            new = [t for t in hits if t not in prev[sc["id"]]]
            if new:
                more = f" and {len(new) - 8} more" if len(new) > 8 else ""
                out.append(f'New in your screen "{sc.get("name", "screen")}": {", ".join(new[:8])}{more} ({len(hits)} match now).')
    state["screens"] = now
    return out


# ───────────────────────── optional AI write-up ─────────────────────────
def facts(A):
    return {
        "portfolio": {"value": round(A["value"]), "pnl_pct": round((A["value"] / A["cost"] - 1) * 100, 2) if A["cost"] else None,
                      "sp500_same_dates_pct": round(A["spy"], 2) if A.get("spy") is not None else None,
                      "vs_sp500_pts": round(A["excess"], 2) if A["excess"] is not None else None,
                      "signal": round(A["signal"]) if A["signal"] is not None else None, "beta": round(A["beta"], 2) if A["beta"] else None},
        "holdings": [{"ticker": r["s"]["ticker"], "weight_pct": round(r["weight"], 1), "pnl_pct": round(r["pnlPct"], 1),
                      "signal": r["s"].get("signal"), "label": r["s"].get("signalLabel"), "reasons": r["s"].get("reasons", [])[:3]}
                     for r in A["holdings"]],
        "alerts": A["alerts"], "considerations": A["considerations"],
        "ideas": [{"ticker": s["ticker"], "sector": s["sector"], "signal": s["signal"], "reasons": s.get("reasons", [])[:2]} for s in A["ideas"]],
    }


SECTORS = ["Information Technology", "Health Care", "Financials", "Consumer Discretionary", "Consumer Staples",
           "Communication Services", "Industrials", "Energy", "Utilities", "Real Estate", "Materials"]


def context_errors(text, F):
    """Sentence-level checks: tickers named alongside a sector must be in it; numbers next to
    'S&P 500' must be the portfolio's actual comparison figures."""
    sector_of = {h["ticker"]: h.get("sector") for h in F["holdings"]} | {i["ticker"]: i.get("sector") for i in F["ideas"]}
    p = F["portfolio"]
    sp_ok = [abs(x) for x in (p.get("vs_sp500_pts"), p.get("sp500_same_dates_pct")) if x is not None]
    for sent in re.split(r"(?<=[.!?])\s+", text):
        named = [sec for sec in SECTORS if sec.lower() in sent.lower()]
        ticks = [t for t in sector_of if re.search(r"" + re.escape(t) + r"", sent)]
        if named and ticks:
            for t in ticks:
                if sector_of.get(t) and sector_of[t] not in named:
                    return f"{t} placed in {named[0]} (it is {sector_of[t]})"
        if re.search(r"S&(?:amp;)?P", sent):
            clean = re.sub(r"S&(?:amp;)?P\s*500", " ", sent)
            for m in re.findall(r"\d+(?:\.\d+)?(?=\s*(?:%|percent|points|pts))", clean):
                if not any(abs(float(m) - k) <= 0.06 for k in sp_ok):
                    return f"{m} given as an S&P 500 comparison (actual: {', '.join(f'{k:.2f}' for k in sp_ok) or 'n/a'})"
    return None


def unverified_numbers(text, F):
    """Every number the model writes must match a number in the facts (after rounding), so it cannot
    invent prices or returns. Small counting words like 'two' are fine; digits are checked."""
    known = []
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            known.append(abs(float(x)))
        elif isinstance(x, str):
            for m in re.findall(r"\d+(?:\.\d+)?", x.replace(",", "")):
                known.append(float(m))
    walk(F)
    bad = []
    text = re.sub(r"S&(?:amp;)?P\s*500|S&P|Q[1-4]|20\d\d", " ", text)   # index name, quarters, years
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        m = m.rstrip(",")
        v = float(m.replace(",", ""))
        if v <= 10 and float(v).is_integer():
            continue  # counts like "3 holdings" / "top 2"
        if not any(abs(v - k) <= max(0.051, 0.006 * k) or abs(v - round(k)) < 0.5 and v == round(v) for k in known):
            bad.append(m)
    return bad


def ai_summary(A):
    """A short narrative from a local or hosted model. Numbers stay in the rule-based tables:
    the model is told to use no digits, and any digit or unknown ticker rejects its text."""
    base = os.environ.get("LLM_BASE_URL")
    if not base:
        return None
    F = facts(A)
    for h, r in zip(F["holdings"], A["holdings"]):
        h["sector"] = r["s"]["sector"]
        h["since_purchase"] = "gain" if r["pnl"] >= 0 else "loss"
    prompt = ("Write a 3-4 sentence note to an investor about their paper portfolio, using ONLY the facts in the JSON. "
              "STRICT RULES: only use numbers that appear in the JSON, copied exactly (rounding is fine); mention only tickers "
              "that appear in the JSON; describe each holding's result using its 'since_purchase' field (gain or loss); use each "
              "holding's 'sector' exactly. Cover: overall picture, the most important risk or point to consider, and what to "
              "watch next. Plain English, no headings, no lists. End with: 'Educational, not financial advice.'\n\nFACTS:\n"
              + json.dumps(F))
    model = os.environ.get("LLM_MODEL", "llama3.2")
    if model.startswith(("qwen3", "deepseek-r1")):   # reasoning models: skip the long hidden reasoning
        prompt = "/no_think\n" + prompt
    messages = [{"role": "user", "content": prompt}]
    ollama = ":11434" in base or os.environ.get("LLM_PROVIDER") == "ollama"
    if ollama:
        # Ollama's native API lets us cap the context window. Without it, a server configured for
        # long contexts can reserve many GB of memory for a prompt of a few thousand tokens.
        url = re.sub(r"/v1/?$", "", base.rstrip("/")) + "/api/chat"
        body = {"model": model, "messages": messages, "stream": False, "think": False,
                "options": {"temperature": 0, "num_ctx": int(os.environ.get("LLM_NUM_CTX", "8192"))}}
    else:
        url = base.rstrip("/") + "/chat/completions"
        body = {"model": model, "temperature": 0, "messages": messages}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ.get('LLM_API_KEY', 'none')}"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            out = json.loads(r.read().decode())
            text = (out["message"]["content"] if ollama else out["choices"][0]["message"]["content"]).strip()
    except Exception as e:  # noqa: BLE001
        print(f"[ai] skipped: {e}", file=sys.stderr)
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()   # reasoning models
    bad = unverified_numbers(text, F)
    if bad:
        print(f"[ai] dropped: number(s) {', '.join(bad)} are not in the facts", file=sys.stderr)
        return None
    why = context_errors(text, F)
    if why:
        print(f"[ai] dropped: {why}", file=sys.stderr)
        return None
    allowed = {h["ticker"] for h in F["holdings"]} | {i["ticker"] for i in F["ideas"]}
    common = {"S", "P", "AI", "ETF", "RSI", "EPS", "CEO", "US", "USA", "OK", "SMA"}
    for tok in re.findall(r"[A-Z]{2,5}", text):
        if tok not in allowed and tok not in common:
            print(f"[ai] dropped: mentions {tok}, which is not in the facts", file=sys.stderr)
            return None
    for h in F["holdings"]:  # a holding described with the wrong direction is rejected
        sentence = " ".join(x for x in re.split(r"(?<=[.!?])\s+", text) if h["ticker"] in x).lower()
        wrong = ("loss" in sentence or "declin" in sentence or "down" in sentence) if h["since_purchase"] == "gain" else ("gain" in sentence)
        if sentence and wrong and len([t for t in allowed if t in sentence.upper()]) == 1:
            print(f"[ai] dropped: {h['ticker']} described against its {h['since_purchase']}", file=sys.stderr)
            return None
    return text


def rule_summary(A):
    pnl = (A["value"] / A["cost"] - 1) * 100 if A["cost"] else 0
    parts = [f"Your paper portfolio is worth ${A['value']:,.0f}, {pnl:+.1f}% versus cost"]
    if A["excess"] is not None:
        parts.append(f"{A['excess']:+.1f} points versus the S&P 500 over the same dates")
    s = ". ".join([", ".join(parts)])
    if A["signal"] is not None:
        s += f". The value-weighted FinSTR signal is {A['signal']:.0f}."
    if A["considerations"]:
        s += " Top point to consider: " + A["considerations"][0]
    return s


def chat_text(A, mode, as_of, ai_text, flips):
    """Short plain-text version for Telegram, Slack, Discord and phone push."""
    pnl = (A["value"] / A["cost"] - 1) * 100 if A["cost"] else 0
    head = {"weekly": "FinSTR weekly digest", "daily": "FinSTR daily summary", "alerts": "FinSTR alert"}[mode]
    lines = [f"{head} ({as_of})",
             f"Value ${A['value']:,.0f} | day {'-' if A['day'] < 0 else '+'}${abs(A['day']):,.0f} | P&L {pnl:+.2f}%"
             + ("" if A["excess"] is None else f" | vs S&P 500 {A['excess']:+.2f} pts")
             + ("" if A["signal"] is None else f" | signal {A['signal']:.0f}")]
    alerts = A["alerts"] + flips
    if alerts:
        lines += ["", "Alerts:"] + [f"- {x}" for x in alerts]
    if mode != "alerts":
        lines += ["", ai_text or rule_summary(A)]
        if A["considerations"]:
            lines += ["", "Points to consider:"] + [f"- {x}" for x in A["considerations"][:4]]
    if mode == "weekly" and A["ideas"]:
        lines += ["", "Ideas to diversify:"] + [f"- {s['ticker']} ({s['sector']}), signal {s['signal']}" for s in A["ideas"][:4]]
    if A["earnings"]:
        lines += ["", "Earnings soon: " + ", ".join(f"{t} {d}" for t, d in A["earnings"])]
    lines += ["", f"{DEFAULT_DATA}/  ·  educational, not investment advice"]
    return "\n".join(lines)


# ───────────────────────── render ─────────────────────────
def render(A, mode, as_of, ai_text, flips, ai_tried=False):
    e = html.escape
    pct = lambda v: "" if v is None else f'<span style="color:{"#16a34a" if v >= 0 else "#dc2626"}">{v:+.2f}%</span>'
    money = lambda v: f"{'-' if v < 0 else ''}${abs(v):,.0f}"
    title = {"weekly": "Weekly portfolio digest", "daily": "Daily portfolio summary", "alerts": "Portfolio alert"}[mode]
    pnl = (A["value"] / A["cost"] - 1) * 100 if A["cost"] else 0
    rows = "".join(
        f'<tr><td style="padding:6px 8px;border-bottom:1px solid #eee"><b>{e(r["s"]["ticker"])}</b><br><span style="color:#888;font-size:11px">{e(r["s"]["name"])}</span></td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right">{r["s"]["price"]:.2f}<br>{pct(r["s"].get("changePercent"))}</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right">{pct(r["pnlPct"])}<br><span style="color:#888;font-size:11px">{r["weight"]:.1f}%</span></td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right"><b>{r["s"].get("signal", "-")}</b><br><span style="color:#888;font-size:11px">{e(r["s"].get("signalLabel") or "")}</span></td></tr>'
        for r in A["holdings"])
    li = lambda items: "".join(f'<li style="margin:4px 0">{e(x)}</li>' for x in items)
    ideas = "".join(f'<li style="margin:4px 0"><b>{e(s["ticker"])}</b> {e(s["name"])} ({e(s["sector"])}) · signal {s["signal"]} · {e((s.get("reasons") or [""])[0])}</li>' for s in A["ideas"])
    alerts = A["alerts"] + flips
    sec = lambda h, body: f'<h3 style="font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:#666;margin:20px 0 6px">{h}</h3>{body}' if body else ""
    return f"""<!doctype html><html><body style="margin:0;background:#f5f6f8;font-family:Segoe UI,Arial,sans-serif;color:#111">
<div style="max-width:620px;margin:0 auto;padding:18px">
<div style="font-size:20px;font-weight:800">Fin<span style="color:#3b6fe0">STR</span> · {title}</div>
<div style="color:#888;font-size:12px;margin-bottom:12px">Data as of {e(as_of)} · paper portfolio</div>
<div style="background:#fff;border-radius:10px;padding:14px;border:1px solid #e6e8ec">
<table width="100%" style="font-size:13px"><tr>
<td><div style="color:#888;font-size:11px">Value</div><b style="font-size:17px">{money(A["value"])}</b></td>
<td><div style="color:#888;font-size:11px">Day</div><b>{money(A["day"])}</b></td>
<td><div style="color:#888;font-size:11px">P&amp;L</div><b>{pct(pnl)}</b></td>
<td><div style="color:#888;font-size:11px">vs S&amp;P 500</div><b>{"-" if A["excess"] is None else f"{A['excess']:+.2f} pts"}</b></td>
<td><div style="color:#888;font-size:11px">Signal</div><b>{"-" if A["signal"] is None else f"{A['signal']:.0f}"}</b></td></tr></table>
<p style="font-size:14px;line-height:1.55;margin:12px 0 0">{e(ai_text or rule_summary(A))}</p>
{'<div style="color:#999;font-size:11px">Written by an AI model from the numbers below; every number it uses was checked against them.</div>' if ai_text else ('<div style="color:#999;font-size:11px">AI note unavailable this run (model unreachable or its text failed the fact check); rule-based summary shown.</div>' if ai_tried else '')}
{sec("Alerts", f'<ul style="padding-left:18px;font-size:13px;margin:0">{li(alerts)}</ul>' if alerts else "")}
{sec("Holdings", f'<table width="100%" cellspacing="0" style="font-size:13px;border-collapse:collapse"><tr style="color:#888;font-size:11px"><td style="padding:4px 8px">Stock</td><td style="padding:4px 8px;text-align:right">Price / day</td><td style="padding:4px 8px;text-align:right">P&amp;L / weight</td><td style="padding:4px 8px;text-align:right">Signal</td></tr>{rows}</table>')}
{sec("Points to consider", f'<ul style="padding-left:18px;font-size:13px;margin:0">{li(A["considerations"])}</ul>' if A["considerations"] and mode != "alerts" else "")}
{sec("Ideas to diversify", f'<ul style="padding-left:18px;font-size:13px;margin:0">{ideas}</ul>' if ideas and mode == "weekly" else "")}
{sec("Earnings in the next 2 weeks", f'<ul style="padding-left:18px;font-size:13px;margin:0">{li([f"{t}: {d}" for t, d in A["earnings"]])}</ul>' if A["earnings"] else "")}
</div>
<p style="color:#999;font-size:11px;line-height:1.5">FinSTR is open source. Signals are rule-based and educational, not investment advice.
<a href="{DEFAULT_DATA}/" style="color:#3b6fe0">Open FinSTR</a> · change what you receive by re-exporting your portfolio file.</p>
</div></body></html>"""


def should_send(mode, prefs, A, flips):
    if mode == "alerts":
        return bool(A["alerts"] or flips)
    return True


def resolve_mode(mode, prefs, slot):
    """auto: the morning run sends the weekly digest on Mondays; the evening run (after the close)
    sends the daily summary on weekdays and trigger alerts, as the portfolio's preferences ask."""
    if mode != "auto":
        return [mode]
    out, wd = [], dt.date.today().weekday()
    if slot in ("morning", "any") and prefs.get("weekly") and wd == 0:
        out.append("weekly")
    if slot in ("evening", "any") and prefs.get("daily") and wd < 5:
        out.append("daily")
    if slot in ("evening", "any") and prefs.get("alerts") and "daily" not in out:
        out.append("alerts")
    return out


def post_json(url, payload, headers=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def deliver(subject, html_body, text_body, chat):
    """Send to every configured channel. Returns the channels that worked; raises if none is configured."""
    env = os.environ.get
    sent, errors = [], []
    channels = []
    if all(env(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO")):
        channels.append(("email", lambda: send(subject, html_body, text_body)))
    if env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
        api = env("FINSTR_TELEGRAM_API", "https://api.telegram.org")
        channels.append(("telegram", lambda: post_json(f"{api}/bot{env('TELEGRAM_BOT_TOKEN')}/sendMessage",
                                                       {"chat_id": env("TELEGRAM_CHAT_ID"), "text": chat[:4000],
                                                        "disable_web_page_preview": True})))
    if env("WEBHOOK_URL"):
        url = env("WEBHOOK_URL")
        body = {"content": chat[:1900]} if "discord" in url else {"text": chat[:3900]}   # Discord vs Slack-style
        channels.append(("webhook", lambda: post_json(url, body)))
    if env("NTFY_TOPIC"):
        def ntfy():
            url = env("NTFY_SERVER", "https://ntfy.sh").rstrip("/") + "/" + env("NTFY_TOPIC")
            req = urllib.request.Request(url, data=chat[:3900].encode(), method="POST",
                                         headers={"Title": subject.replace("·", "-").encode("ascii", "ignore").decode(), "Tags": "chart_with_upwards_trend",
                                                  "Click": DEFAULT_DATA + "/#view=portfolio", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status
        channels.append(("ntfy", ntfy))
    if not channels:
        raise SystemExit("No delivery channel configured: set SMTP_* + MAIL_TO, TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, "
                         "WEBHOOK_URL or NTFY_TOPIC (see the docstring).")
    for name, fn in channels:
        try:
            fn()
            sent.append(name)
        except Exception as e:  # noqa: BLE001  one failing channel must not stop the others
            errors.append(f"{name}: {e}")
            print(f"[deliver] {name} failed: {e}", file=sys.stderr)
    if not sent:
        raise SystemExit("Every delivery channel failed: " + "; ".join(errors))
    return sent


def send(subject, html_body, text_body):
    host, user, pw, to = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO"))
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("MAIL_FROM", user), to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=60) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pw)
        s.send_message(msg)


def main():
    ap = argparse.ArgumentParser(description="FinSTR portfolio digest")
    ap.add_argument("--portfolio", help="portfolio JSON exported from FinSTR (or env PORTFOLIO_JSON)")
    ap.add_argument("--data", default=os.environ.get("FINSTR_DATA", DEFAULT_DATA))
    ap.add_argument("--mode", default="auto", choices=["auto", "weekly", "daily", "alerts"])
    ap.add_argument("--state", default=os.environ.get("FINSTR_STATE"))
    ap.add_argument("--slot", default=os.environ.get("FINSTR_SLOT", "any"), choices=["morning", "evening", "any"],
                    help="which scheduled run this is (auto mode)")
    ap.add_argument("--out", help="write the HTML here instead of (or as well as) sending")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()

    raw = os.environ.get("PORTFOLIO_JSON")
    if a.portfolio:
        with open(a.portfolio, encoding="utf-8") as f:
            raw = f.read()
    if not raw:
        print("No portfolio given (use --portfolio or PORTFOLIO_JSON). Nothing to do.")
        return
    pf = json.loads(raw)
    rows = load(a.data, "data/screener.json")
    meta = load(a.data, "data/meta.json")
    try:
        bench = load(a.data, "data/benchmark.json")
    except Exception:  # noqa: BLE001
        bench = None
    A = analyse(pf, rows, bench, a.data)
    state = load_state(a.state)
    flips = signal_flips(A, state) + screen_alerts(pf, rows, state)
    if a.state:
        with open(a.state, "w", encoding="utf-8") as f:
            json.dump(state, f)
    as_of = dt.datetime.fromtimestamp(meta["generatedAt"], dt.timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    modes = resolve_mode(a.mode, pf.get("prefs") or {}, a.slot)
    if not modes:
        print("No email preference matches today. Nothing sent.")
    for mode in modes:
        if not should_send(mode, pf.get("prefs"), A, flips):
            print(f"[{mode}] no triggers today; nothing sent.")
            continue
        ai = ai_summary(A) if mode == "weekly" else None
        body = render(A, mode, as_of, ai, flips, ai_tried=bool(os.environ.get('LLM_BASE_URL')) and mode == 'weekly')
        text = (ai or rule_summary(A)) + "\n\n" + "\n".join(A["alerts"] + flips + A["considerations"])
        subject = {"weekly": "FinSTR weekly digest", "daily": "FinSTR daily summary", "alerts": "FinSTR alert"}[mode] + f" · {dt.date.today():%b %d}"
        if a.out:
            out = a.out if len(modes) == 1 else a.out.replace(".html", f"-{mode}.html")
            with open(out, "w", encoding="utf-8") as f:
                f.write(body)
            print(f"[{mode}] wrote {out}")
        if a.send:
            sent = deliver(subject, body, text, chat_text(A, mode, as_of, ai, flips))
            print(f"[{mode}] sent via {', '.join(sent)}")


if __name__ == "__main__":
    main()
