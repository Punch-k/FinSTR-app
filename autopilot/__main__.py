"""
FinSTR Autopilot command line. Run from the FinSTR folder (or with it on PYTHONPATH):

    python -m autopilot plan     --rules rules.toml --ledger ledger [--send]   # this month's proposal
    python -m autopilot check    --rules rules.toml --ledger ledger            # what was actually filled
    python -m autopilot refresh  --rules rules.toml                            # keep the Questrade token alive
    python -m autopilot backtest --rules rules.toml [--out backtest.json]      # 5-year test of your rules

Reads FinSTR's public data (screener.json, sentiment.json) from the live site unless --data points at a folder.
Writes only to --ledger (plans, a log, a small state file) in YOUR private repo.
"""

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pipeline"))

from . import brokers, config, engine, report, sentiment  # noqa: E402

SITE = "https://punch-k.github.io/FinSTR-app"


def load_data(base, path):
    import digest   # noqa: E402  same loader the digest uses (URL or folder)
    return digest.load(base, path)


def prices_for(symbols, pairs=None, held=()):
    """Latest close for each symbol (Yahoo via yfinance); symbols without a usable price are left out.
    pairs {cad_symbol: us_ticker}: a Canadian listing (CDR) counts only if it trades most days and its daily moves
    track the US stock (Yahoo keeps stale or odd quotes for listings that do not really trade).
    held: symbols you own get their last close even if they fail those checks, so they are never ignored."""
    import logging
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)   # missing CDRs are expected, not errors
    pairs = pairs or {}
    want = sorted(set(symbols) | set(pairs.values()))
    if not want:
        return {}
    df = yf.download(want, period="2mo", interval="1d", progress=False, auto_adjust=False, group_by="ticker", threads=True)

    def series(s):
        try:
            d = (df[s] if len(want) > 1 else df).dropna(subset=["Close"])
        except KeyError:
            return None, None
        c, v = d["Close"], d["Volume"]
        if hasattr(c, "columns"):
            c, v = c.iloc[:, 0], v.iloc[:, 0]
        return (c, v) if len(c) else (None, None)

    out = {}
    for s in symbols:
        c, v = series(s)
        if c is None:
            continue
        last = float(c.iloc[-1])
        if s in pairs:
            # Yahoo keeps only the latest quote for most real CDRs (one bar). With a longer history, the listing
            # must move with the US stock (a different company sharing the symbol does not); with one bar, the
            # price must look like a CDR's (they are issued around $10-50) and have traded.
            u, _ = series(pairs[s])
            if len(c) >= 10 and u is not None:
                both = __import__("pandas").concat([c.pct_change(), u.pct_change()], axis=1, join="inner").dropna()
                ok = len(both) >= 10 and float(both.corr().iloc[0, 1]) >= 0.6 and last >= 5
            else:
                ok = 5 <= last <= 500 and float(v.iloc[-1] or 0) > 0
        else:
            ok = last >= 1 and float((v > 0).mean()) >= 0.6
        if ok or s in held:
            out[s] = round(float(c.iloc[-1]), 2)
    return out


def ledger_paths(d):
    os.makedirs(os.path.join(d, "plans"), exist_ok=True)
    return os.path.join(d, "state.json"), os.path.join(d, "log.jsonl")


def read_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def log(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **rec}) + "\n")


def build_plan(rules, acct, rows_list, sent, state, price_fn=prices_for, today=None):
    today = today or dt.date.today()
    core = rules["core"]["symbol"]
    rows = {r["ticker"]: {k: r.get(k) for k in ("signal", "signalLabel", "sector", "recMean", "reasons", "name")}
            for r in rows_list}
    tick_sent = (sent or {}).get("tickers") or {}
    held_us = {engine.us_ticker_of(s, rules): s for s in acct.positions if s != core}
    # Which buy candidates can actually be bought in your currency: one price lookup for all of them.
    s_rules = rules["satellites"]
    cands = [t for t, r in rows.items() if (r.get("signal") or 0) >= s_rules["min_signal"] and t not in held_us]
    sym = {t: engine.cdr_symbol(t, rules) for t in cands}
    sym.update(held_us)
    pairs = {v: t for t, v in sym.items() if v and v != t}
    px = price_fn([core] + list(acct.positions) + [v for v in sym.values() if v], pairs, set(acct.positions))
    lost = [p for p in acct.positions if p not in px]
    unavailable = {t for t in cands if not sym.get(t) or sym[t] not in px}
    keep, exits, buys = engine.pick_satellites({t: r for t, r in rows.items() if t not in unavailable},
                                               set(held_us), rules, tick_sent, state.get("neg_streak"))
    warnings = []
    if lost:
        warnings.append("No price found for " + ", ".join(lost) + ": left out of this plan; check the symbol in your holdings")
    if core not in px:
        raise SystemExit(f"No price for the core fund {core}; check core.symbol in rules.toml")
    if unavailable:
        warnings.append(f"{len(unavailable)} stocks met your signal level but have no Canadian-dollar listing (CDR) on "
                        "Yahoo, so they were left out; add them under [satellites.map] if you know their CAD symbol.")
    sats = [sym[t] for t, _ in keep + buys if sym.get(t) in px]
    if any(o.endswith(".NE") for o in sats):
        warnings.append("Canadian listings (.NE) are matched from Yahoo quotes: confirm each symbol and price in your broker "
                        "before you order.")
    tw = engine.target_weights(core, sats, rules)
    exit_syms = [sym[t] for t, _ in exits if sym.get(t)]
    orders, summary = engine.make_orders(acct.positions, acct.cash, px, tw, rules, exits=exit_syms)
    why = {sym[t]: w for t, w in keep + exits + buys if sym.get(t)}
    for o in orders:
        base = {"exit": "Sell: ", "exit-partial": "Sell part (monthly limit): ", "trim": "Trim back to target: ",
                "new": "New: ", "add": "Top up to target: "}[o["why_code"]]
        o["why"] = base + (why.get(o["symbol"]) or (f"core fund, target {tw.get(o['symbol'], 0):.0%}" if o["symbol"] == core else ""))
    traded = {o["symbol"] for o in orders}
    holds = [(sym[t], w) for t, w in keep if sym.get(t) and sym[t] not in traded]
    room = rules["account"]["tfsa_room"]
    if acct.deposits_ytd is not None:
        left = room - acct.deposits_ytd
        if left < rules["account"]["monthly_contribution"]:
            warnings.append(f"TFSA room left this year is about ${left:,.0f}: a ${rules['account']['monthly_contribution']:,.0f} "
                            "deposit could go over it (penalty 1% per month). Check CRA My Account.")
    m = (sent or {}).get("market") or {}
    mood = {"label": sentiment.mood_label(m.get("score")), "score": m.get("score"), "pos": m.get("pos", 0), "neg": m.get("neg", 0)}
    streak = {}
    for t, _ in keep + buys:
        a30 = (tick_sent.get(t) or {}).get("avg30")
        streak[t] = (state.get("neg_streak", {}).get(t, 0) + 1) if a30 is not None and a30 < 0 else 0
    return {"month": today.strftime("%Y-%m"), "date": today.isoformat(), "source": acct.source, "cash": acct.cash,
            "positions": acct.positions, "prices": px, "orders": orders, "holds": holds, "summary": summary,
            "mood": mood, "warnings": warnings, "targets": tw, "neg_streak": streak}


def cmd_plan(a, rules):
    state_p, log_p = ledger_paths(a.ledger)
    state = read_json(state_p, {})
    if state.get("last_plan") == dt.date.today().strftime("%Y-%m") and not a.force:
        print(f"Plan for {state['last_plan']} already made (use --force to redo it)")
        return
    acct = brokers.from_rules(rules, a.holdings).account()
    rows = load_data(a.data, "data/screener.json")
    try:
        sent = load_data(a.data, "data/sentiment.json")
    except Exception:  # noqa: BLE001  older site without sentiment: plan without the tone rules
        sent = None
    plan = build_plan(rules, acct, rows, sent, state)
    facts = {"mood": plan["mood"], "orders": [{k: o[k] for k in ("side", "symbol", "qty", "value")} for o in plan["orders"]],
             "after": plan["summary"]["weights_after"]}
    plan["ai"], plan["ai_note"] = report.ai_mood(facts, rules)
    text, md, body = report.render(plan)
    with open(os.path.join(a.ledger, "plans", plan["month"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1)
    with open(os.path.join(a.ledger, "plans", plan["month"] + ".md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")
    state.update({"neg_streak": plan["neg_streak"], "last_plan": plan["month"]})
    with open(state_p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    log(log_p, {"type": "plan", "month": plan["month"], "orders": len(plan["orders"]), "total": plan["summary"]["total"]})
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("```\n" + text + "\n```\n")
    if a.send:
        import digest   # noqa: E402
        n = len(plan["orders"])
        digest.deliver(f"FinSTR Autopilot · {plan['month']} · {n} order{'s' * (n != 1)} to review", body, text, text)


def cmd_check(a, rules):
    state_p, log_p = ledger_paths(a.ledger)
    state = read_json(state_p, {})
    month = state.get("last_plan")
    if not month:
        print("No plan yet: nothing to check")
        return
    plan = read_json(os.path.join(a.ledger, "plans", month + ".json"), None)
    acct = brokers.from_rules(rules, a.holdings).account()
    results = []
    for o in plan["orders"]:
        before = plan["positions"].get(o["symbol"], 0)
        now = acct.positions.get(o["symbol"], 0)
        moved = (now - before) if o["side"] == "BUY" else (before - now)
        status = "done" if moved >= o["qty"] else "partial" if moved > 0 else "not done"
        fill = [f for f in acct.fills if f["symbol"] == o["symbol"] and f["side"] == o["side"] and f["date"] >= plan["date"]]
        results.append({**{k: o[k] for k in ("side", "symbol", "qty", "price")}, "status": status,
                        "filled_at": fill[-1]["price"] if fill else None})
    plan["check"] = {"date": dt.date.today().isoformat(), "results": results}
    with open(os.path.join(a.ledger, "plans", month + ".json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1)
    log(log_p, {"type": "check", "month": month, "done": sum(r["status"] == "done" for r in results), "of": len(results),
                "deposits_ytd": acct.deposits_ytd})
    for r in results:
        print(f"{r['side']:<4} {r['qty']:g} × {r['symbol']:<9} {r['status']}" + (f" at {r['filled_at']}" if r["filled_at"] else ""))


def cmd_refresh(a, rules):
    b = brokers.from_rules(rules, a.holdings)
    if isinstance(b, brokers.QuestradeBroker):
        b._auth()
        print("Questrade token refreshed and saved")
    else:
        print("Nothing to refresh (broker is not Questrade)")


def cmd_backtest(a, rules):
    import csv
    import fetch   # noqa: E402  reuse the pipeline's history download
    from . import backtest
    with open(os.path.join(os.path.dirname(HERE), "pipeline", "universe.csv"), encoding="utf-8") as f:
        uni = list(csv.DictReader(f))
    proxy = rules["core"].get("backtest_proxy", "SPY")
    hist = fetch.download_history([u["ticker"] for u in uni] + [proxy])
    res = backtest.run(hist, uni, rules, proxy)
    if not res:
        raise SystemExit("Not enough price history for a backtest")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    s = res["stats"]
    print(f"{res['start']} → {res['end']}: deposited ${s['contributed']:,.0f}")
    for k in ("autopilot", "coreOnly"):
        x = s[k]
        print(f"  {k:<10} end ${x['endValue']:,.0f}  return {x['total']:+.1f}% ({x['perYear']:+.1f}%/yr)  worst drop {x['maxDrawdown']:.1f}%")
    print(f"  {s['orders']} orders ({s['ordersPerMonth']}/month)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m autopilot")
    ap.add_argument("command", choices=["plan", "check", "refresh", "backtest"])
    ap.add_argument("--rules", default="rules.toml" if os.path.exists("rules.toml") else None)
    ap.add_argument("--ledger", default="ledger")
    ap.add_argument("--holdings", default="holdings.json")
    ap.add_argument("--data", default=os.environ.get("FINSTR_DATA", SITE))
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--force", action="store_true", help="plan: redo this month's plan")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rules = config.load(a.rules)
    {"plan": cmd_plan, "check": cmd_check, "refresh": cmd_refresh, "backtest": cmd_backtest}[a.command](a, rules)


if __name__ == "__main__":
    main()
