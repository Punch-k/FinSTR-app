"""Turns a plan into the email / page: plain-text, Markdown and HTML, plus the optional AI market-mood paragraph."""

import html
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pipeline"))
from digest import unverified_numbers  # noqa: E402

DISCLAIMER = ("Autopilot applies the rules you wrote; it is not investment advice and it cannot place orders. "
              "Check each order before you place it.")


def _call_llm(prompt):
    kind = os.environ.get("LLM_PROVIDER") or "openai"
    base, key, model = os.environ.get("LLM_BASE_URL"), os.environ.get("LLM_API_KEY", "none"), os.environ.get("LLM_MODEL")
    if kind == "anthropic":
        url = (base or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        body = {"model": model or "claude-sonnet-5-5", "max_tokens": 400, "temperature": 0,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    else:
        if not base:
            return None
        url = base.rstrip("/") + "/chat/completions"
        body = {"model": model or "llama3.2", "temperature": 0, "messages": [{"role": "user", "content": prompt}]}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.loads(r.read().decode())
    text = out["content"][0]["text"] if kind == "anthropic" else out["choices"][0]["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def ai_mood(facts, rules):
    """2-3 sentences on market mood from the facts only. Returns (text or None, note)."""
    if rules["report"]["llm"] == "none":
        return None, "AI write-up off"
    os.environ.setdefault("LLM_PROVIDER", rules["report"]["llm"])
    prompt = ("In 2-3 plain-English sentences, describe the market mood and how this month's plan responds to it, using "
              "ONLY these facts. Copy any number exactly; mention only tickers in the facts; no advice beyond the plan; "
              "no headings or lists.\n\nFACTS:\n" + json.dumps(facts))
    try:
        text = _call_llm(prompt)
    except Exception as e:  # noqa: BLE001
        return None, f"AI write-up skipped ({e.__class__.__name__})"
    if not text:
        return None, "AI write-up skipped (no model configured)"
    bad = unverified_numbers(text, facts)
    if bad:
        return None, f"AI write-up dropped: numbers not in the plan ({', '.join(bad[:3])})"
    allowed = set(json.dumps(facts).replace('"', " ").split())
    for tok in re.findall(r"\b[A-Z]{2,5}(?:\.[A-Z]{2})?\b", text):
        if tok not in allowed and tok not in {"AI", "US", "TFSA", "CAD", "USD", "ETF", "CDR", "S", "P"}:
            return None, f"AI write-up dropped: mentions {tok}, which is not in the plan"
    return text, "AI write-up checked against the plan"


def money(v):
    return f"${v:,.2f}"


def render(plan):
    P = plan
    M = P["mood"]
    mood = (f"{M['label']} (headline tone {M['score']:+.2f} today, {M['pos']} positive vs {M['neg']} negative headlines)"
            if M["score"] is not None else "no headline data")
    after = ("After: " + " · ".join(f"{k} {w:.0%}" for k, w in P["summary"]["weights_after"].items())
             + f" · cash {money(P['summary']['cash_after'])} · fees {money(P['summary']['fees'])}")
    lines = [f"FinSTR Autopilot plan: {P['month']}",
             f"Account {money(P['summary']['total'])} · cash {money(P['cash'])} · source: {P['source']}",
             "Market mood: " + mood, ""]
    if P.get("ai"):
        lines += [P["ai"], ""]
    if not P["orders"]:
        lines.append("No orders this month: everything is within your rules.")
    for i, o in enumerate(P["orders"], 1):
        lines.append(f"{i}. {o['side']:<4} {o['qty']:g} × {o['symbol']:<9} ~{money(o['value'])}  {o['why']}")
    for t, why in P["holds"]:
        lines.append(f"   HOLD {t:<9} {why}")
    lines += ["", after]
    for w in P["warnings"]:
        lines.append("! " + w)
    lines += ["", DISCLAIMER]
    text = "\n".join(lines)

    e = html.escape
    rows = "".join(f"<tr><td><b style='color:{'#0a8f3c' if o['side'] == 'BUY' else '#c62828'}'>{o['side']}</b></td>"
                   f"<td>{o['qty']:g}</td><td><b>{e(o['symbol'])}</b></td><td style='text-align:right'>{money(o['value'])}</td>"
                   f"<td>{e(o['why'])}</td></tr>" for o in P["orders"])
    rows += "".join(f"<tr><td style='color:#666'>HOLD</td><td></td><td>{e(t)}</td><td></td><td style='color:#666'>{e(w)}</td></tr>"
                    for t, w in P["holds"])
    warn = "".join(f"<p style='background:#fff4e5;padding:8px 10px;border-radius:6px'>⚠ {e(w)}</p>" for w in P["warnings"])
    body = f"""<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:720px;color:#111">
<h2 style="margin:0 0 4px">FinSTR Autopilot · {e(P['month'])}</h2>
<p style="color:#555;margin:0 0 12px">Account {money(P['summary']['total'])} · cash {money(P['cash'])} · {e(P['source'])}</p>
<p><b>Market mood:</b> {e(mood)}</p>
{f"<p>{e(P['ai'])}</p>" if P.get('ai') else ''}
<table cellpadding="6" style="border-collapse:collapse;width:100%;font-size:14px">
<tr style="background:#f2f4f8;text-align:left"><th>Action</th><th>Qty</th><th>Symbol</th><th style="text-align:right">≈ Value</th><th>Why</th></tr>
{rows or "<tr><td colspan=5>No orders this month: everything is within your rules.</td></tr>"}</table>
<p style="color:#333">{e(after)}</p>{warn}
<p style="color:#777;font-size:12px">{e(DISCLAIMER)} Limit orders near the prices shown are suggested. {e(P.get('ai_note', ''))}</p>
</div>"""
    md = text.replace("\n", "  \n")
    return text, md, body
