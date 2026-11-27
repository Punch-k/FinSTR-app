"""
Backtest of the Autopilot rules on FinSTR's 5 years of daily prices.

Each month-end it runs the same engine as the live job: score stocks, pick satellites, build whole-share orders
within the limits, deposit the monthly contribution, pay the fee per order. It compares the result with putting
the same deposits into the core fund alone.

Honest limits (also written into the output):
  - The signal is FinSTR's price-only composite (trend, momentum, low risk). Value, quality, growth, analyst ratings
    and headline tone have no free point-in-time history, so the live plan uses more information than the test.
  - Today's S&P 500 members are used for the whole period (survivorship bias flatters stock picking).
  - US-dollar prices; no currency effects, no CDR price differences, no taxes (a TFSA has none on gains).
"""

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pipeline"))
import signals  # noqa: E402

from .engine import make_orders, pick_satellites, target_weights  # noqa: E402

WARMUP = 253   # a year of prices before the first decision (the momentum score needs it)


def run(hist, universe, rules, proxy=None):
    """hist: {ticker: DataFrame with Close}; universe: [{ticker, sector}]. Returns the JSON-ready result or None."""
    proxy = proxy or rules["core"].get("backtest_proxy", "SPY")
    sector = {u["ticker"]: u["sector"] for u in universe}
    col = lambda df: df["Adj Close"] if "Adj Close" in df else df["Close"]   # dividends reinvested where Yahoo gives it
    closes = pd.DataFrame({t: col(hist[t]) for t in sector if t in hist}).sort_index().ffill()
    closes = closes.loc[:, closes.notna().sum() > 300]
    if proxy not in hist or len(closes) < WARMUP + 60:
        return None
    core_px = col(hist[proxy]).reindex(closes.index).ffill()
    month_ends = closes.groupby([closes.index.year, closes.index.month]).tail(1).index
    month_ends = [d for d in month_ends if closes.index.get_loc(d) >= WARMUP]
    if len(month_ends) < 12:
        return None
    contrib = float(rules["account"]["monthly_contribution"])
    fee = rules["limits"].get("fee_per_order", 0.0)
    core_only_rules = {**rules, "satellites": {**rules["satellites"], "weight": 0.0, "count": 0},
                       "core": {**rules["core"], "weight": 1 - rules["limits"]["cash_buffer"]}}
    A = {"cash": 0.0, "h": {}}       # Autopilot
    B = {"cash": 0.0, "h": {}}       # core only
    months, trades, n_orders = [], [], 0
    held_sats = set()
    for d in month_ends:
        i = closes.index.get_loc(d)
        px = closes.iloc[i].dropna().to_dict()
        px[proxy] = float(core_px.iloc[i])
        comp = signals._price_factors(closes, i)["composite"].dropna()
        rows = {t: {"signal": float(v), "sector": sector.get(t), "signalLabel": signals.label(float(v))} for t, v in comp.items()}
        A["cash"] += contrib
        B["cash"] += contrib
        keep, exits, buys = pick_satellites(rows, held_sats, rules)
        exit_syms = [t for t, _ in exits]
        sats = [t for t, _ in keep] + [t for t, _ in buys]
        tw = target_weights(proxy, sats, rules)
        orders, _ = make_orders(A["h"], A["cash"], px, tw, rules, exits=exit_syms)
        for o in orders:
            q = o["qty"] if o["side"] == "BUY" else -o["qty"]
            A["h"][o["symbol"]] = A["h"].get(o["symbol"], 0) + q
            A["cash"] -= q * o["price"] + fee
            if A["h"][o["symbol"]] == 0:
                del A["h"][o["symbol"]]
        n_orders += len(orders)
        held_sats = {t for t in A["h"] if t != proxy}
        o_b, _ = make_orders(B["h"], B["cash"], px, {proxy: core_only_rules["core"]["weight"]}, core_only_rules)
        for o in o_b:
            B["h"][proxy] = B["h"].get(proxy, 0) + o["qty"]
            B["cash"] -= o["qty"] * o["price"] + fee
        va = A["cash"] + sum(q * px.get(t, 0) for t, q in A["h"].items())
        vb = B["cash"] + sum(q * px.get(t, 0) for t, q in B["h"].items())
        months.append({"d": d.strftime("%Y-%m-%d"), "a": round(va, 2), "b": round(vb, 2),
                       "in": round(contrib * (len(months) + 1), 2)})
        trades.append({"d": d.strftime("%Y-%m-%d"), "buy": [t for t, _ in buys], "sell": exit_syms})

    def twr(key):
        """Time-weighted return: strips out the deposits so it measures the rules, not the saving."""
        idx, peak, dd, prev = 1.0, 1.0, 0.0, None
        for m in months:
            if prev:
                idx *= (m[key] - contrib) / prev
            prev = m[key]
            peak = max(peak, idx)
            dd = min(dd, idx / peak - 1)
        years = (len(months) - 1) / 12
        return {"total": round((idx - 1) * 100, 1), "perYear": round((idx ** (1 / years) - 1) * 100, 1) if years > 0 else None,
                "maxDrawdown": round(dd * 100, 1)}

    last = months[-1]
    return {
        "start": months[0]["d"], "end": last["d"], "months": months, "decisions": trades[-12:],
        "stats": {"autopilot": {**twr("a"), "endValue": last["a"]}, "coreOnly": {**twr("b"), "endValue": last["b"]},
                  "contributed": last["in"], "orders": n_orders, "ordersPerMonth": round(n_orders / len(months), 1)},
        "rules": {"core": f"{rules['core']['weight']:.0%} core ({proxy} in the test)",
                  "satellites": f"{rules['satellites']['weight']:.0%} in up to {rules['satellites']['count']} stocks "
                                f"(signal {rules['satellites']['min_signal']}+ to buy, below {rules['satellites']['exit_signal_below']} to sell)",
                  "limits": f"at most {rules['limits']['max_turnover']:.0%} of the account sold per month, "
                            f"{rules['limits']['cash_buffer']:.0%} cash, ${contrib:.0f} deposited monthly, whole shares, "
                            f"${fee:.2f} per order"},
        "method": "Same engine as the live monthly plan, run at every month-end. Signal = FinSTR's price-only composite "
                  "(trend, momentum, low risk): value, quality, growth, analyst ratings and headline tone have no free "
                  "point-in-time history, so the live plan uses more information than this test. Today's S&P 500 members "
                  "are used throughout (survivorship bias flatters stock picking). US-dollar prices with dividends reinvested, no currency effects. "
                  "Returns are time-weighted (deposits removed). Past results do not predict future results.",
    }
