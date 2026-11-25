"""
Static, search-friendly page per S&P 500 company + sitemap.xml + robots.txt.

Each page is small, fast on phones and readable without JavaScript: key numbers, the FinSTR
Signal with its reasons, and a link into the full interactive quote page.

    python pipeline/pages.py --data data --out _site
"""

import argparse
import html
import json
import os
from datetime import datetime, timezone

SITE = os.environ.get("FINSTR_SITE", "https://punch-k.github.io/FinSTR-app").rstrip("/")


def fmt(v, nd=2, suf=""):
    return "-" if v is None else f"{v:,.{nd}f}{suf}"


def pct(v, nd=2):
    return "-" if v is None else f"{v:+.{nd}f}%"


def cap(b):
    if b is None:
        return "-"
    return f"{b / 1000:.2f}T" if b >= 1000 else f"{b:.1f}B"


def page(s, as_of):
    e = html.escape
    t, name = s["ticker"], s["name"]
    sig = s.get("signal")
    tone = "#64748b" if sig is None else "#15803d" if sig >= 80 else "#22a05a" if sig >= 60 else "#64748b" if sig >= 40 else "#d9534f" if sig >= 20 else "#b91c1c"
    desc = (f"{name} ({t}) stock: price {fmt(s.get('price'))}, P/E {fmt(s.get('pe'))}, YTD {pct(s.get('perfYTD'), 1)}, "
            f"FinSTR Signal {sig if sig is not None else '-'} ({s.get('signalLabel') or 'n/a'}). Valuation, technicals, analyst targets "
            f"and a six-factor score for this S&P 500 {s.get('sector', '')} company.")
    rows = [
        ("Price", fmt(s.get("price"))), ("Change", pct(s.get("changePercent"))), ("Market Cap", cap(s.get("marketCap"))),
        ("P/E", fmt(s.get("pe"))), ("Forward P/E", fmt(s.get("forwardPE"))), ("PEG", fmt(s.get("peg"))),
        ("EPS (ttm)", fmt(s.get("eps"))), ("EPS next Y", pct(s.get("epsNextYGrowth"), 1)), ("Sales Q/Q", pct(s.get("salesGrowthQ"), 1)),
        ("ROE", fmt(s.get("roe"), 1, "%")), ("Profit Margin", fmt(s.get("profitMargin"), 1, "%")), ("Debt/Eq", fmt(s.get("debtEq"))),
        ("Dividend", fmt(s.get("dividendYield"), 2, "%") if s.get("dividendYield") else "-"), ("Beta", fmt(s.get("beta"))),
        ("RSI (14)", fmt(s.get("rsi"), 1)), ("vs SMA200", pct(s.get("vsSma200"))), ("Perf YTD", pct(s.get("perfYTD"))),
        ("Perf Year", pct(s.get("perfY"))), ("Target Price", fmt(s.get("target"))), ("To Target", pct(s.get("upside"), 1)),
        ("Analysts", str(s.get("analysts") or "-")), ("Earnings", s.get("earningsDate") or "-"),
    ]
    grid = "".join(f"<div><span>{e(k)}</span><b>{e(v)}</b></div>" for k, v in rows)
    f = s.get("factors") or {}
    factors = "".join(f"<div><span>{lab}</span><i style='width:{f.get(k) or 0}%'></i><b>{f.get(k) if f.get(k) is not None else '-'}</b></div>"
                      for k, lab in (("trend", "Trend"), ("momentum", "Momentum"), ("value", "Value"), ("quality", "Quality"), ("growth", "Growth"), ("risk", "Low Risk")))
    reasons = "".join(f"<li>{e(r)}</li>" for r in (s.get("reasons") or [])) or "<li>No standout signals.</li>"
    w, chg = s.get("why"), s.get("changePercent")
    why = ""
    if w and chg is not None:
        h = w.get("headline") or {}
        hl = (f' Latest company headline: <a href="{e(h["link"])}" rel="nofollow noopener">&ldquo;{e(h["title"])}&rdquo;</a> '
              f'<span class="meta">({e(h.get("source") or "")})</span>') if str(h.get("link", "")).startswith("http") else ""
        why = f'<div class="card why"><b>{"Up" if chg >= 0 else "Down"} {abs(chg):.1f}% today.</b> {e(w.get("text", ""))}{hl}</div>'
        desc = f"{t} {'up' if chg >= 0 else 'down'} {abs(chg):.1f}% today. {w.get('text', '')} " + desc
    ld = {"@context": "https://schema.org", "@type": "WebPage", "name": f"{name} ({t}) stock analysis",
          "description": desc, "dateModified": as_of, "about": {"@type": "Corporation", "name": name, "tickerSymbol": t}}
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(t)} {e(name)}: valuation, technicals &amp; signal | FinSTR</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{SITE}/stock/{t}/">
<meta property="og:title" content="{e(t)} {e(name)} | FinSTR"><meta property="og:description" content="{e(desc)}">
<meta property="og:type" content="website"><meta property="og:url" content="{SITE}/stock/{t}/">
<meta property="og:image" content="{SITE}/assets/img/finstr-og.jpg"><meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="../../assets/img/finstr-icon-32.png"><link rel="apple-touch-icon" href="../../assets/img/finstr-icon-180.png">
<script type="application/ld+json">{json.dumps(ld)}</script>
<style>
:root{{--bg:#0b0e14;--panel:#131722;--b:#232838;--t:#e6e6e6;--d:#8b94a3;--a:#4f8cff;--p:#26a65b;--n:#ef5350}}
@media (prefers-color-scheme: light){{:root{{--bg:#f6f7f9;--panel:#fff;--b:#e3e6ec;--t:#1c2331;--d:#6b7280}}}}
body{{margin:0;background:var(--bg);color:var(--t);font:14px/1.5 'Segoe UI',system-ui,sans-serif}}
main{{max-width:880px;margin:0 auto;padding:16px}}
a{{color:var(--a);text-decoration:none}} header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}}
.brand{{font-weight:800;font-size:18px;color:var(--t)}} .brand b{{color:var(--a)}}
h1{{font-size:24px;margin:0}} h1 small{{font-size:15px;color:var(--d);font-weight:500}}
.px{{font-size:26px;font-weight:800;margin:4px 0}} .meta{{color:var(--d);font-size:12.5px}}
.card{{background:var(--panel);border:1px solid var(--b);border-radius:8px;padding:12px;margin:12px 0}}
.sig{{display:flex;gap:14px;align-items:center;flex-wrap:wrap}} .sig .n{{font-size:26px;font-weight:800;color:#fff;border-radius:6px;padding:4px 10px}}
.f{{display:grid;grid-template-columns:repeat(3,1fr);gap:4px 16px;font-size:12.5px;margin-top:8px}}
.f div{{display:grid;grid-template-columns:70px 1fr 26px;align-items:center;gap:6px}} .f span{{color:var(--d)}}
.f i{{display:block;height:6px;background:var(--a);border-radius:3px}} .f b{{text-align:right}}
ul{{margin:8px 0 0;padding-left:18px}}
.g{{display:grid;grid-template-columns:repeat(4,1fr);gap:0;border:1px solid var(--b);border-radius:8px;overflow:hidden}}
.g div{{display:flex;justify-content:space-between;padding:6px 10px;border-bottom:1px solid var(--b);font-size:13px}}
.g span{{color:var(--d)}} .cta{{display:inline-block;background:var(--a);color:#fff;border-radius:6px;padding:9px 14px;font-weight:600;margin:6px 0}}
.brand-word{{display:flex;align-items:center;gap:7px;text-decoration:none}} .brand-word b{{color:var(--a)}}
.bw-ico{{width:24px;height:24px;border-radius:6px;display:block;flex-shrink:0;}}
.bw-tag{{font-weight:700;font-size:14px;letter-spacing:0;margin-left:4px;white-space:nowrap;}}
.bw-tag b{{color:var(--accent, var(--a));font-weight:700;}} .bw-os{{color:var(--dim, var(--d));font-weight:600;}}
@media (max-width:980px){{ .bw-tag{{display:none;}} }}
.site-footer{{border-top:1px solid var(--border, var(--b));margin:28px 0 10px;padding:16px 4px 8px;text-align:center;font-size:12px;color:var(--dim, var(--d));}}
.sf-links{{display:flex;flex-wrap:wrap;justify-content:center;gap:4px 8px;margin-bottom:8px;}}
.sf-links a{{color:var(--dim, var(--d));text-decoration:none;white-space:nowrap;}} .sf-links a:hover{{color:var(--accent, var(--a));text-decoration:underline;}}
.sf-links > span{{opacity:.5;}}
.sf-note{{font-size:11.5px;line-height:1.5;opacity:.85;max-width:900px;margin:0 auto;}}
.why{{border-left:3px solid var(--a);font-size:13.5px}}
@media (max-width:640px){{.g{{grid-template-columns:1fr 1fr}} .f{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<header><a class="brand brand-word" href="../../" aria-label="FinSTR home"><img class="bw-ico" src="../../assets/img/finstr-icon-180.png" alt="" width="24" height="24"><span>Fin<b>STR</b></span><span class="bw-tag">· <b>Stock Trade Recommendation</b> <span class="bw-os">(Open Source)</span></span></a><a href="../">All S&amp;P 500 stocks →</a></header>
<h1>{e(t)} <small>{e(name)}</small></h1>
<div class="px">{fmt(s.get('price'))} <span style="font-size:15px;color:{'var(--p)' if (s.get('changePercent') or 0) >= 0 else 'var(--n)'}">{pct(s.get('changePercent'))}</span></div>
<div class="meta">{e(s.get('sector', ''))} · {e(s.get('industry', ''))} · {e(s.get('exchange') or '')} · data as of {e(as_of[:16].replace('T', ' '))} UTC</div>
<a class="cta" href="../../#stock={t}">Open the full quote page: chart, financials, analysts →</a>
{why}
<div class="card"><div class="sig"><span class="n" style="background:{tone}">{sig if sig is not None else '-'}</span><div><b>FinSTR Signal: {e(s.get('signalLabel') or 'n/a')}</b><br>
<span class="meta">Stronger than {sig if sig is not None else '-'}% of the S&amp;P 500 on six factors. <a href="../../#view=signals">How it is built and how it has performed</a></span></div></div>
<div class="f">{factors}</div><ul>{reasons}</ul></div>
<div class="g">{grid}</div>
<footer class="site-footer">
  <nav class="sf-links"><a href="../../#about">About &amp; Methodology</a><span>·</span><a href="../../#view=screener">Screener</a><span>·</span><a href="../../#view=heatmap">Maps</a><span>·</span><a href="../../#view=signals">Signals</a><span>·</span><a href="../../stock/">All Stocks A–Z</a><span>·</span><a href="../../llms.txt">Data &amp; API</a><span>·</span><a href="https://github.com/Punch-k/FinSTR-app#mcp-server-claude-cursor-and-other-ai-assistants">AI Assistants (MCP)</a><span>·</span><a href="https://github.com/Punch-k/FinSTR-app">Source Code</a><span>·</span><a href="https://github.com/Punch-k/FinSTR-app/issues">Report an Issue</a><span>·</span><a href="../../privacy.html">Privacy</a><span>·</span><a href="../../terms.html">Terms</a></nav>
  <div class="sf-note">© 2026 FinSTR · Open source · Market data from Yahoo Finance, refreshed hourly on US trading days, not real-time · Signals and ideas are educational, not investment advice.</div>
</footer>
</main></body></html>"""


def index_page(rows, as_of):
    e = html.escape
    by = {}
    for s in rows:
        by.setdefault(s.get("sector") or "Other", []).append(s)
    secs = []
    for sec in sorted(by):
        items = "".join(
            f'<a href="{s["ticker"]}/"><b>{e(s["ticker"])}</b><span>{e(s["name"])}</span>'
            f'<i class="{"p" if (s.get("changePercent") or 0) >= 0 else "n"}">{pct(s.get("changePercent"))}</i>'
            f'<em>{s.get("signal") if s.get("signal") is not None else "-"}</em></a>'
            for s in sorted(by[sec], key=lambda x: x["ticker"]))
        secs.append(f"<h2>{e(sec)} <small>{len(by[sec])}</small></h2><div class='l'>{items}</div>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>All S&amp;P 500 Stocks by Sector | FinSTR</title>
<meta name="description" content="Every S&amp;P 500 company by sector with today's change and its FinSTR Signal (0-100). Each links to a page with valuation, technicals and analyst targets.">
<link rel="canonical" href="{SITE}/stock/"><link rel="icon" href="../assets/img/finstr-icon-32.png"><link rel="apple-touch-icon" href="../assets/img/finstr-icon-180.png">
<meta property="og:title" content="All S&amp;P 500 Stocks | FinSTR"><meta property="og:image" content="{SITE}/assets/img/finstr-og.jpg"><meta name="twitter:card" content="summary_large_image">
<style>
:root{{--bg:#0b0e14;--panel:#131722;--b:#232838;--t:#e6e6e6;--d:#8b94a3;--a:#4f8cff}}
@media (prefers-color-scheme: light){{:root{{--bg:#f6f7f9;--panel:#fff;--b:#e3e6ec;--t:#1c2331;--d:#6b7280}}}}
body{{margin:0;background:var(--bg);color:var(--t);font:14px/1.45 'Segoe UI',system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:16px}} a{{color:var(--a);text-decoration:none}}
header{{display:flex;justify-content:space-between;align-items:center}} .brand{{font-weight:800;font-size:18px;color:var(--t)}} .brand b{{color:var(--a)}}
h1{{font-size:22px;margin:14px 0 2px}} h2{{font-size:15px;margin:18px 0 6px;border-bottom:1px solid var(--b);padding-bottom:4px}} h2 small{{color:var(--d);font-weight:500}}
.meta{{color:var(--d);font-size:12.5px}}
.l{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:2px 14px}}
.l a{{display:grid;grid-template-columns:48px 1fr 62px 26px;gap:6px;padding:3px 0;color:var(--t);font-size:13px;border-bottom:1px solid var(--b)}}
.l span{{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:var(--d)}} .l b{{color:var(--a)}}
.l i{{font-style:normal;text-align:right}} .l i.p{{color:#26a65b}} .l i.n{{color:#ef5350}} .l em{{font-style:normal;text-align:right;color:var(--d)}}
.brand-word{{display:flex;align-items:center;gap:7px;text-decoration:none}} .brand-word b{{color:var(--a)}}
.bw-ico{{width:24px;height:24px;border-radius:6px;display:block;flex-shrink:0;}}
.bw-tag{{font-weight:700;font-size:14px;letter-spacing:0;margin-left:4px;white-space:nowrap;}}
.bw-tag b{{color:var(--accent, var(--a));font-weight:700;}} .bw-os{{color:var(--dim, var(--d));font-weight:600;}}
@media (max-width:980px){{ .bw-tag{{display:none;}} }}
.site-footer{{border-top:1px solid var(--border, var(--b));margin:28px 0 10px;padding:16px 4px 8px;text-align:center;font-size:12px;color:var(--dim, var(--d));}}
.sf-links{{display:flex;flex-wrap:wrap;justify-content:center;gap:4px 8px;margin-bottom:8px;}}
.sf-links a{{color:var(--dim, var(--d));text-decoration:none;white-space:nowrap;}} .sf-links a:hover{{color:var(--accent, var(--a));text-decoration:underline;}}
.sf-links > span{{opacity:.5;}}
.sf-note{{font-size:11.5px;line-height:1.5;opacity:.85;max-width:900px;margin:0 auto;}}
</style></head><body><main>
<header><a class="brand brand-word" href="../" aria-label="FinSTR home"><img class="bw-ico" src="../assets/img/finstr-icon-180.png" alt="" width="24" height="24"><span>Fin<b>STR</b></span><span class="bw-tag">· <b>Stock Trade Recommendation</b> <span class="bw-os">(Open Source)</span></span></a><a href="../#view=screener">Screener →</a></header>
<h1>All S&amp;P 500 Stocks</h1><div class="meta">{len(rows)} companies · columns: ticker, name, today's change, FinSTR Signal (0-100) · data as of {e(as_of[:16].replace('T', ' '))} UTC</div>
{"".join(secs)}
<footer class="site-footer">
  <nav class="sf-links"><a href="../#about">About &amp; Methodology</a><span>·</span><a href="../#view=screener">Screener</a><span>·</span><a href="../#view=heatmap">Maps</a><span>·</span><a href="../#view=signals">Signals</a><span>·</span><a href="../stock/">All Stocks A–Z</a><span>·</span><a href="../llms.txt">Data &amp; API</a><span>·</span><a href="https://github.com/Punch-k/FinSTR-app#mcp-server-claude-cursor-and-other-ai-assistants">AI Assistants (MCP)</a><span>·</span><a href="https://github.com/Punch-k/FinSTR-app">Source Code</a><span>·</span><a href="https://github.com/Punch-k/FinSTR-app/issues">Report an Issue</a><span>·</span><a href="../privacy.html">Privacy</a><span>·</span><a href="../terms.html">Terms</a></nav>
  <div class="sf-note">© 2026 FinSTR · Open source · Market data from Yahoo Finance, refreshed hourly on US trading days, not real-time · Signals and ideas are educational, not investment advice.</div>
</footer>
</main></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="_site")
    a = ap.parse_args()
    with open(os.path.join(a.data, "screener.json"), encoding="utf-8") as f:
        rows = json.load(f)
    with open(os.path.join(a.data, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    as_of = datetime.fromtimestamp(meta["generatedAt"], timezone.utc).isoformat()
    urls = [f"{SITE}/"]
    for s in rows:
        d = os.path.join(a.out, "stock", s["ticker"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
            f.write(page(s, as_of))
        urls.append(f"{SITE}/stock/{s['ticker']}/")
    with open(os.path.join(a.out, "stock", "index.html"), "w", encoding="utf-8") as f:
        f.write(index_page(rows, as_of))
    urls.insert(1, f"{SITE}/stock/")
    day = as_of[:10]
    with open(os.path.join(a.out, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        f.writelines(f"<url><loc>{u}</loc><lastmod>{day}</lastmod><changefreq>daily</changefreq></url>\n" for u in urls)
        f.write("</urlset>\n")
    with open(os.path.join(a.out, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")
    print(f"wrote {len(rows)} stock pages, sitemap.xml, robots.txt")


if __name__ == "__main__":
    main()
