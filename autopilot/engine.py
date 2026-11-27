"""
The rules engine: the same code decides the live monthly plan and every month of the backtest.

    pick_satellites()  which stocks to hold, with a plain-English reason for each decision
    target_weights()   core + satellite slots (+ cash buffer); empty slots go to the core
    make_orders()      whole-share orders that move the account toward the targets within the limits
"""

import math


def _tone(sent, t):
    e = (sent or {}).get(t) or {}
    return e.get("avg30"), e.get("prev30")


def pick_satellites(rows, held, rules, sent=None, neg_streak=None):
    """rows: {ticker: {signal, sector, recMean, reasons, name}}; held: set of US tickers held as satellites.
    sent: {ticker: {avg30, prev30}} or None (backtest). neg_streak: {ticker: months of negative tone before now}.
    Returns (keep, exits, buys): lists of (ticker, reason)."""
    s = rules["satellites"]
    keep, exits = [], []
    for t in sorted(held):
        r = rows.get(t)
        if r is None or r.get("signal") is None:
            keep.append((t, "no current data, held until it returns"))
            continue
        a30, p30 = _tone(sent, t)
        streak = 0
        if a30 is not None and a30 < 0:      # months of negative tone, this one included
            prior = (neg_streak or {}).get(t)
            if prior is None:                # no monthly log yet: the 30 days before count as one month
                prior = 1 if p30 is not None and p30 < 0 else 0
            streak = prior + 1
        if r["signal"] < s["exit_signal_below"]:
            exits.append((t, f"FinSTR Signal fell to {r['signal']:.0f}, below your exit level of {s['exit_signal_below']}"))
        elif sent is not None and streak >= s["exit_after_negative_months"]:
            exits.append((t, f"headline tone negative {streak} months running (30-day tone {a30:+.2f})"))
        else:
            why = f"signal {r['signal']:.0f}"
            if a30 is not None:
                why += f", 30-day tone {a30:+.2f}"
            keep.append((t, why + ", still meets your rules"))
    slots = int(s["count"]) - len(keep)
    sectors = {}
    for t, _ in keep:
        sec = (rows.get(t) or {}).get("sector")
        sectors[sec] = sectors.get(sec, 0) + 1
    out_of = {t for t, _ in exits}
    cands = []
    for t, r in rows.items():
        if t in held or t in out_of or r.get("signal") is None or r["signal"] < s["min_signal"]:
            continue
        a30, _ = _tone(sent, t)
        if a30 is not None and a30 < s["min_sentiment"]:
            continue
        rec = r.get("recMean")
        rank = r["signal"] + 20 * (a30 or 0) + (4 * (3 - rec) if rec else 0)
        cands.append((rank, t, r, a30))
    cands.sort(key=lambda x: (-x[0], x[1]))
    buys = []
    for rank, t, r, a30 in cands:
        if len(buys) >= slots:
            break
        sec = r.get("sector")
        if sectors.get(sec, 0) >= s["max_per_sector"]:
            continue
        bits = [f"FinSTR Signal {r['signal']:.0f} ({r.get('signalLabel') or 'n/a'})"]
        if r.get("recMean"):
            bits.append(f"analysts {r['recMean']:.1f} on a 1 (Strong Buy) to 5 (Sell) scale")
        if a30 is not None:
            bits.append(f"30-day headline tone {a30:+.2f}")
        if r.get("reasons"):
            bits.append(r["reasons"][0])
        buys.append((t, "; ".join(bits)))
        sectors[sec] = sectors.get(sec, 0) + 1
    return keep, exits, buys


def target_weights(core_symbol, satellite_symbols, rules):
    s, core_w = rules["satellites"], rules["core"]["weight"]
    slot = s["weight"] / s["count"] if s["count"] else 0
    tw = {sym: slot for sym in satellite_symbols}
    tw[core_symbol] = core_w + slot * (s["count"] - len(satellite_symbols))
    return tw


def make_orders(holdings, cash, prices, targets, rules, exits=()):
    """holdings {symbol: qty}; prices {symbol: price}; targets {symbol: weight}; exits: symbols to sell in full.
    Returns (orders, summary). Orders are dicts {side, symbol, qty, price, value, why_code}."""
    lim = rules["limits"]
    fee = lim.get("fee_per_order", 0.0)
    value = {k: q * prices[k] for k, q in holdings.items() if q and k in prices}
    total = cash + sum(value.values())
    orders = []
    budget = lim["max_turnover"] * total
    proceeds = 0.0

    def sell(sym, qty, code):
        nonlocal budget, proceeds
        if qty <= 0:
            return
        v = qty * prices[sym]
        orders.append({"side": "SELL", "symbol": sym, "qty": qty, "price": prices[sym], "value": round(v, 2), "why_code": code})
        budget -= v
        proceeds += v - fee
    # 1. exits first (weakest first is decided by the caller's order), capped by the monthly turnover limit
    for sym in exits:
        q = holdings.get(sym, 0)
        if q and sym in prices:
            q_ok = q if q * prices[sym] <= budget else max(0, int(budget // prices[sym]))
            sell(sym, q_ok, "exit" if q_ok == q else "exit-partial")
    # 2. trim anything more than 25% above its target (bands avoid tiny monthly trades)
    for sym, v in sorted(value.items(), key=lambda kv: -kv[1]):
        if sym in exits or sym not in targets:
            continue
        tgt = targets[sym] * total
        if v > tgt * 1.25 and budget > 0:
            q = min(int((v - tgt) // prices[sym]), int(budget // prices[sym]))
            if q * prices[sym] >= lim["min_order"]:
                sell(sym, q, "trim")
    # 3. buy the biggest gaps first with cash + proceeds, keeping the cash buffer
    avail = cash + proceeds - lim["cash_buffer"] * total
    after = {k: v for k, v in value.items()}
    for o in orders:
        after[o["symbol"]] = after.get(o["symbol"], 0) - o["value"]
    gaps = sorted(((targets[s] * total - after.get(s, 0), s) for s in targets if s in prices), reverse=True)
    for gap, sym in gaps:
        if gap <= 0 or avail <= 0:
            continue
        q = int(min(gap, avail - fee) // prices[sym])
        if q <= 0 or q * prices[sym] < lim["min_order"]:
            continue
        v = q * prices[sym]
        orders.append({"side": "BUY", "symbol": sym, "qty": q, "price": prices[sym], "value": round(v, 2),
                       "why_code": "new" if not holdings.get(sym) else "add"})
        avail -= v + fee
        after[sym] = after.get(sym, 0) + v
    cash_after = cash + sum(o["value"] for o in orders if o["side"] == "SELL") \
        - sum(o["value"] for o in orders if o["side"] == "BUY") - fee * len(orders)
    weights = {k: (v / total if total else 0) for k, v in after.items() if v > 0.005}
    return orders, {"total": round(total, 2), "cash_after": round(cash_after, 2),
                    "weights_after": {k: round(w, 4) for k, w in sorted(weights.items(), key=lambda kv: -kv[1])},
                    "fees": round(fee * len(orders), 2)}


def cdr_symbol(us_ticker, rules):
    """The CAD listing to try for a US ticker (None when trading in US dollars is allowed)."""
    s = rules["satellites"]
    m = (s.get("map") or {}).get(us_ticker)
    if m:
        return m
    if not rules["account"].get("cad_only"):
        return us_ticker
    return us_ticker.replace(".", "-") + ".NE" if s.get("trade_as") == "cdr" else None


def us_ticker_of(symbol, rules):
    inv = {v: k for k, v in ((rules["satellites"].get("map")) or {}).items()}
    if symbol in inv:
        return inv[symbol]
    if symbol.endswith(".NE"):
        return symbol[:-3].replace("-", ".")
    return symbol


def safe(x):
    return x if x is not None and not (isinstance(x, float) and math.isnan(x)) else None
