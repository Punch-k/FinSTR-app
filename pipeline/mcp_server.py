"""
FinSTR MCP server: lets AI assistants (Claude Desktop, Claude Code, Cursor, any MCP client) use FinSTR.

Standard library only; speaks MCP over stdio (JSON-RPC 2.0, one message per line). It reads FinSTR's
public static data, so there is no server, account or API key.

    python pipeline/mcp_server.py                 # data from the live site
    FINSTR_DATA=. python pipeline/mcp_server.py   # data from a local build

Claude Desktop / Claude Code config:
    {"mcpServers": {"finstr": {"command": "python", "args": ["/path/to/FinSTR-app/pipeline/mcp_server.py"]}}}

Tools: market_overview, search, stock, screen, signal_record, portfolio_review.
Every number comes from FinSTR's published files; nothing is estimated. Educational, not investment advice.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import digest  # noqa: E402  (load, analyse, rule_ok, why_text, isnum)

DATA = os.environ.get("FINSTR_DATA", digest.DEFAULT_DATA)
PROTOCOL = "2025-06-18"
_cache = {}


def data(path, ttl=600):
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = digest.load(DATA, path)
    _cache[path] = (time.time(), val)
    return val


def rows():
    return data("data/screener.json")


def as_of():
    m = data("data/meta.json")
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(m["generatedAt"]))


KEYS = ["ticker", "name", "sector", "industry", "price", "changePercent", "marketCap", "pe", "forwardPE", "peg", "ps",
        "epsNextYGrowth", "salesGrowthQ", "roe", "profitMargin", "debtEq", "dividendYield", "perfW", "perfM", "perfYTD",
        "perfY", "vsSma50", "vsSma200", "rsi", "relVol", "beta", "target", "upside", "recMean", "analysts",
        "earningsDate", "signal", "signalLabel", "factors", "reasons"]


def brief(s, keys=("ticker", "name", "sector", "price", "changePercent", "marketCap", "pe", "signal", "signalLabel")):
    return {k: s.get(k) for k in keys}


# ───────────────────────── tools ─────────────────────────
def t_market_overview(_):
    r = rows()
    chg = [s["changePercent"] for s in r if digest.isnum(s.get("changePercent"))]
    secs = {}
    for s in r:
        if digest.isnum(s.get("changePercent")) and digest.isnum(s.get("marketCap")):
            a = secs.setdefault(s["sector"], [0.0, 0.0])
            a[0] += s["changePercent"] * s["marketCap"]
            a[1] += s["marketCap"]
    movers = sorted((s for s in r if s.get("why")), key=lambda s: -abs(s["changePercent"]))[:10]
    return {
        "asOf": as_of(),
        "breadth": {"advancers": sum(c > 0 for c in chg), "decliners": sum(c < 0 for c in chg),
                    "medianChange": sorted(chg)[len(chg) // 2] if chg else None},
        "sectors": sorted(({"sector": k, "capWeightedChange": round(v[0] / v[1], 2)} for k, v in secs.items() if v[1]),
                          key=lambda x: -x["capWeightedChange"]),
        "biggestMovesWithContext": [dict(brief(s, ("ticker", "name", "changePercent")), context=digest.why_text(s)) for s in movers],
    }


def t_search(a):
    q = str(a.get("query", "")).strip().lower()
    if not q:
        raise ValueError("query is required")
    hits = [s for s in rows() if q == s["ticker"].lower()] + \
           [s for s in rows() if q != s["ticker"].lower() and (q in s["ticker"].lower() or q in (s.get("name") or "").lower())]
    return {"asOf": as_of(), "results": [brief(s) for s in hits[:15]]}


def t_stock(a):
    t = str(a.get("ticker", "")).strip().upper()
    s = next((x for x in rows() if x["ticker"] == t), None)
    if not s:
        raise ValueError(f"{t or 'ticker'} is not in FinSTR's S&P 500 universe; try the search tool")
    out = {k: s.get(k) for k in KEYS}
    out["moveContext"] = digest.why_text(s) or None
    out["headlines"] = [{k: n.get(k) for k in ("title", "source", "link")} for n in (s.get("news") or [])[:5]]
    out["asOf"] = as_of()
    out["page"] = f"{digest.DEFAULT_DATA}/stock/{t}/"
    return out


def t_screen(a):
    rules = a.get("rules") or []
    for r in rules:
        if not isinstance(r, list) or len(r) < 2:
            raise ValueError("each rule is [field, op, value, value2?], e.g. [\"signal\", \">=\", 80]")
    sort, desc = a.get("sort", "signal"), a.get("descending", True)
    limit = max(1, min(int(a.get("limit", 25)), 100))
    hits = [s for s in rows() if all(digest.rule_ok(s, r) for r in rules)]
    hits.sort(key=lambda s: (s.get(sort) is None, -(s.get(sort) or 0) if desc else (s.get(sort) or 0)))
    cols = ("ticker", "name", "sector", "price", "changePercent", "marketCap", "pe", "signal", "signalLabel", sort)
    return {"asOf": as_of(), "matches": len(hits), "results": [brief(s, cols) for s in hits[:limit]]}


def t_signal_record(_):
    sig = data("data/signals.json")
    try:
        tr = data("data/track.json")
    except Exception:  # noqa: BLE001
        tr = None
    bt = sig.get("backtest") or {}
    return {
        "liveRecord": {"started": tr.get("started"), "stats": tr.get("stats"), "method": tr.get("method")} if tr else None,
        "backtest": {"stats": bt.get("stats"), "method": bt.get("method")},
        "weights": sig.get("weights"),
        "note": "The live record is out-of-sample (picks saved before returns); the backtest is not. Weeks of data are noise.",
    }


def t_portfolio_review(a):
    pf = a.get("portfolio")
    if isinstance(pf, str):
        pf = json.loads(pf)
    if not isinstance(pf, dict) or not pf.get("holdings"):
        raise ValueError('portfolio is required: {"holdings":[{"ticker":"AAPL","shares":10,"cost":190,"date":"2025-06-02"}]}')
    try:
        bench = data("data/benchmark.json")
    except Exception:  # noqa: BLE001
        bench = None
    A = digest.analyse(pf, rows(), bench, DATA)
    return {
        "asOf": as_of(),
        "value": round(A["value"], 2), "cost": round(A["cost"], 2),
        "pnlPct": round((A["value"] / A["cost"] - 1) * 100, 2) if A["cost"] else None,
        "vsSP500Pts": round(A["excess"], 2) if A.get("excess") is not None else None,
        "signal": round(A["signal"], 1) if A.get("signal") is not None else None,
        "beta": round(A["beta"], 2) if A.get("beta") is not None else None,
        "holdings": [{"ticker": r["s"]["ticker"], "weightPct": round(r["weight"], 1), "pnlPct": round(r["pnlPct"], 2),
                      "signal": r["s"].get("signal"), "signalLabel": r["s"].get("signalLabel")} for r in A["holdings"]],
        "alerts": A["alerts"], "pointsToConsider": A["considerations"], "movesWithContext": A.get("moves"),
        "ideas": [brief(s, ("ticker", "name", "sector", "signal", "reasons")) for s in A["ideas"]],
        "upcomingEarnings": A["earnings"],
        "note": "Rule-based and educational, not investment advice.",
    }


RULE_HELP = ("FinSTR rule format: [field, op, a, b?]. ops: < <= > >= (missing values never pass), abs<=, eq, in, falsy, "
             "text, days (date within a..b days), >f >=f <f (compare with another field). Fields include signal (0-100), pe, "
             "forwardPE, peg, ps, roe, profitMargin, debtEq, dividendYield, epsNextYGrowth, salesGrowthQ, perfW, perfM, "
             "perfYTD, perfY, vsSma50, vsSma200, rsi, relVol, beta, upside, recMean (1=Strong Buy), marketCap ($B), "
             "sector, industry, earningsDate.")
TOOLS = {
    "market_overview": (t_market_overview, "S&P 500 breadth, sector moves and the biggest moves with context (earnings, industry move, headline).", {}),
    "search": (t_search, "Find S&P 500 companies by ticker or name.", {"query": {"type": "string"}}),
    "stock": (t_stock, "Everything FinSTR has on one stock: valuation, growth, technicals, analyst targets, FinSTR Signal with factors and reasons, today's move context and headlines.",
              {"ticker": {"type": "string"}}),
    "screen": (t_screen, "Screen the S&P 500 with rules. " + RULE_HELP,
               {"rules": {"type": "array", "items": {"type": "array"}}, "sort": {"type": "string"},
                "descending": {"type": "boolean"}, "limit": {"type": "integer"}}),
    "signal_record": (t_signal_record, "How the FinSTR Signal has performed: live out-of-sample record and backtest, with method.", {}),
    "portfolio_review": (t_portfolio_review, "Review a paper portfolio (the JSON exported from the FinSTR Portfolio page): P&L, vs S&P 500, risk flags with trim amounts, moves with context, diversification ideas.",
                         {"portfolio": {"type": "object"}}),
}
REQUIRED = {"search": ["query"], "stock": ["ticker"], "portfolio_review": ["portfolio"]}


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", PROTOCOL),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "finstr", "version": "1.0.0"},
                "instructions": "FinSTR: S&P 500 data, signals and portfolio review from published snapshots. Quote numbers as given; cite asOf. Not investment advice."}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": REQUIRED.get(n, [])}}
                          for n, (_, d, p) in TOOLS.items()]}
    if method == "tools/call":
        p = msg.get("params") or {}
        fn = TOOLS.get(p.get("name"), (None,))[0]
        if not fn:
            return {"content": [{"type": "text", "text": f"Unknown tool {p.get('name')}"}], "isError": True}
        try:
            return {"content": [{"type": "text", "text": json.dumps(fn(p.get("arguments") or {}), default=str)}]}
        except Exception as e:  # noqa: BLE001
            return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    if method == "ping":
        return {}
    if mid is not None:
        raise LookupError(method)
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:          # notification
            continue
        try:
            out = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except LookupError as e:
            out = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"Method not found: {e}"}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
