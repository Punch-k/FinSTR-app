"""
FinSTR signal engine.

Six factor scores per stock, each a 0-100 percentile rank within the S&P 500
(value and quality are ranked within the stock's sector, so a bank is compared
with banks, not with software companies):

    trend      price vs 50- and 200-day averages, 50-day vs 200-day
    momentum   12-month return excluding the last month, 6-month return
    value      earnings yield, free-cash-flow yield, EBITDA/EV        (sector-relative)
    quality    ROE, gross margin, operating margin, low debt/equity     (sector-relative)
    growth     next-year EPS growth, sales growth, EPS surprise, net analyst upgrades
    risk       low volatility, small drawdown from the 52-week high     (higher = calmer)

The composite FinSTR Signal is a weighted average of whichever factors a stock has data for.

Backtest: only the price-based factors (trend, momentum, risk) can be rebuilt at
past dates from price history alone, so the backtest tests that price composite.
At each month-end in the last year it buys the top fifth and bottom fifth (equal
weight) and measures the next month's return against the equal-weight universe.
Known bias: today's S&P 500 members are used for the whole period (survivorship).
"""

import math

import numpy as np
import pandas as pd

WEIGHTS = {"trend": 0.20, "momentum": 0.20, "value": 0.15, "quality": 0.15, "growth": 0.20, "risk": 0.10}
LABELS = [(80, "Strong"), (60, "Positive"), (40, "Neutral"), (20, "Weak"), (0, "Very Weak")]


def _pct_rank(s, higher_better=True):
    s = pd.to_numeric(s, errors="coerce")
    r = s.rank(pct=True, ascending=higher_better)
    return r * 100


def _blend(cols):
    df = pd.concat(cols, axis=1)
    return df.mean(axis=1, skipna=True)


def _sector_rank(df, col, higher_better=True):
    return df.groupby("sector")[col].transform(lambda x: _pct_rank(x, higher_better))


def label(score):
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return None
    for cut, name in LABELS:
        if score >= cut:
            return name
    return LABELS[-1][1]


def score_rows(rows, deep_by_ticker):
    """Adds factor scores, composite signal, label and plain-English reasons to each row (in place)."""
    df = pd.DataFrame(rows)
    num = lambda c: pd.to_numeric(df.get(c), errors="coerce")
    ey = 1 / num("pe").where(num("pe") > 0)
    fcfy = 1 / num("pfcf").where(num("pfcf") > 0)
    ebitda_ev = 1 / num("evEbitda").where(num("evEbitda") > 0)
    df["_ey"], df["_fcfy"], df["_ebitdaev"] = ey, fcfy, ebitda_ev
    df["_deq"] = num("debtEq")
    ups = []
    for t in df["ticker"]:
        r = ((deep_by_ticker.get(t) or {}).get("ratings")) or []
        recent = [x for x in r if (pd.Timestamp.now() - pd.Timestamp(x["date"])).days <= 90]
        ups.append(sum(1 for x in recent if x["action"] == "up") - sum(1 for x in recent if x["action"] == "down") if r else np.nan)
    df["_netUps"] = ups
    mom12_1 = (1 + num("perfY") / 100) / (1 + num("perfM") / 100) - 1

    f = {}
    f["trend"] = _blend([_pct_rank(num("vsSma50")), _pct_rank(num("vsSma200")),
                         _pct_rank(num("sma50") / num("sma200"))])
    f["momentum"] = _blend([_pct_rank(mom12_1), _pct_rank(num("perfH"))])
    f["value"] = _blend([_sector_rank(df, "_ey"), _sector_rank(df, "_fcfy"), _sector_rank(df, "_ebitdaev")])
    df["_roe"], df["_gm"], df["_om"] = num("roe"), num("grossMargin"), num("operMargin")
    f["quality"] = _blend([_sector_rank(df, "_roe"), _sector_rank(df, "_gm"), _sector_rank(df, "_om"),
                           _sector_rank(df, "_deq", higher_better=False)])
    f["growth"] = _blend([_pct_rank(num("epsNextYGrowth")), _pct_rank(num("salesGrowthQ")),
                          _pct_rank(num("epsSurprise")), _pct_rank(df["_netUps"])])
    f["risk"] = _blend([_pct_rank(num("volM"), higher_better=False), _pct_rank(num("fromHigh52"))])

    fac = pd.DataFrame(f)
    w = pd.Series(WEIGHTS)
    have = fac.notna()
    raw = (fac.fillna(0) * w).sum(axis=1) / (have * w).sum(axis=1).replace(0, np.nan)
    comp = _pct_rank(raw)   # percentile: "Signal 85" = stronger than 85% of the S&P 500

    for i, row in enumerate(rows):
        sc = {k: (None if pd.isna(fac.iloc[i][k]) else int(round(fac.iloc[i][k]))) for k in WEIGHTS}
        c = None if pd.isna(comp.iloc[i]) else int(round(comp.iloc[i]))
        row["factors"] = sc
        row["signal"] = c
        row["signalLabel"] = label(c)
        row["reasons"] = reasons(row, sc)
    return rows


def reasons(r, sc):
    """Short, checkable reasons: each states the number behind it."""
    g = lambda k: r.get(k)
    out = []
    v50, v200 = g("vsSma50"), g("vsSma200")
    if v50 is not None and v200 is not None:
        if v50 > 0 and v200 > 0:
            out.append(f"Uptrend: {v50:+.1f}% vs 50-day and {v200:+.1f}% vs 200-day average")
        elif v50 < 0 and v200 < 0:
            out.append(f"Downtrend: {v50:+.1f}% vs 50-day and {v200:+.1f}% vs 200-day average")
    m, py = sc.get("momentum"), g("perfY")
    if m is not None and py is not None:
        if m >= 80:
            out.append(f"Top-fifth momentum: {py:+.1f}% over 12 months")
        elif m <= 20:
            out.append(f"Bottom-fifth momentum: {py:+.1f}% over 12 months")
    v, pe = sc.get("value"), g("pe")
    if v is not None and pe:
        if v >= 80:
            out.append(f"Cheap for its sector (P/E {pe:.1f})")
        elif v <= 20:
            out.append(f"Expensive for its sector (P/E {pe:.1f})")
    q, roe = sc.get("quality"), g("roe")
    if q is not None and roe is not None and q >= 80:
        out.append(f"High quality for its sector (ROE {roe:.0f}%)")
    eg = g("epsNextYGrowth")
    if eg is not None and eg >= 15:
        out.append(f"Analysts expect {eg:+.0f}% EPS growth next year")
    su = g("epsSurprise")
    if su is not None and su >= 5:
        out.append(f"Beat the last EPS estimate by {su:.1f}%")
    elif su is not None and su <= -5:
        out.append(f"Missed the last EPS estimate by {abs(su):.1f}%")
    rsi = g("rsi")
    if rsi is not None and rsi >= 70:
        out.append(f"Overbought (RSI {rsi:.0f})")
    elif rsi is not None and rsi <= 30:
        out.append(f"Oversold (RSI {rsi:.0f})")
    fh = g("fromHigh52")
    if fh is not None and fh <= -30:
        out.append(f"{fh:.0f}% below its 52-week high")
    return out[:5]


# ───────────────────────── backtest ─────────────────────────
def _price_factors(closes, t):
    """Price-only factor scores at row t (cross-sectional percentiles, 0-100)."""
    c = closes.iloc[: t + 1]
    last = c.iloc[-1]
    sma50, sma200 = c.tail(50).mean(), c.tail(200).mean()
    trend = pd.concat([_pct_rank(last / sma50), _pct_rank(last / sma200), _pct_rank(sma50 / sma200)], axis=1).mean(axis=1)
    mom = pd.concat([_pct_rank(c.iloc[-22] / c.iloc[-253] - 1), _pct_rank(last / c.iloc[-127] - 1)], axis=1).mean(axis=1)
    rets = c.pct_change().tail(21)
    risk = pd.concat([_pct_rank(rets.std(), False), _pct_rank(last / c.tail(252).max() - 1)], axis=1).mean(axis=1)
    w = {k: WEIGHTS[k] for k in ("trend", "momentum", "risk")}
    comp = (trend * w["trend"] + mom * w["momentum"] + risk * w["risk"]) / sum(w.values())
    return {"composite": comp, "trend": trend, "momentum": mom, "risk": risk}


def backtest(hist, tickers, months=12):
    closes = pd.DataFrame({t: hist[t]["Close"] for t in tickers if t in hist}).sort_index().ffill()
    closes = closes.loc[:, closes.notna().sum() > 300]
    if len(closes) < 300:
        return None
    me = closes.groupby([closes.index.year, closes.index.month]).tail(1).index   # month-end trading days
    me = [d for d in me if closes.index.get_loc(d) >= 253][-(months + 1):]
    per = {k: [] for k in ("composite", "trend", "momentum", "risk")}
    for a, b in zip(me[:-1], me[1:]):
        ia = closes.index.get_loc(a)
        scores = _price_factors(closes, ia)
        fwd_all = (closes.loc[b] / closes.loc[a] - 1)
        for k, sc in scores.items():
            sc = sc.dropna()
            fwd = fwd_all.reindex(sc.index).dropna()
            sc = sc.reindex(fwd.index)
            q = len(sc) // 5
            top = fwd[sc.sort_values(ascending=False).index[:q]].mean()
            bot = fwd[sc.sort_values().index[:q]].mean()
            per[k].append({"start": a.strftime("%Y-%m-%d"), "end": b.strftime("%Y-%m-%d"),
                           "top": round(float(top) * 100, 2), "bottom": round(float(bot) * 100, 2),
                           "universe": round(float(fwd.mean()) * 100, 2)})

    def stats(rows):
        df = pd.DataFrame(rows)
        cum = lambda col: round(float(((1 + df[col] / 100).prod() - 1) * 100), 2)
        return {"periods": len(df), "hitRate": round(float((df["top"] > df["universe"]).mean() * 100), 1),
                "avgExcess": round(float((df["top"] - df["universe"]).mean()), 2),
                "spread": round(float((df["top"] - df["bottom"]).mean()), 2),
                "cumTop": cum("top"), "cumBottom": cum("bottom"), "cumUniverse": cum("universe")}

    if not per["composite"]:
        return None
    return {
        "months": per["composite"],
        "stats": stats(per["composite"]),
        "factors": {k: stats(v) for k, v in per.items() if k != "composite"},
        "method": "Monthly rebalance, equal-weight top and bottom fifth by each price-based score, next-month return vs "
                  "the equal-weight S&P 500. Uses current index members (survivorship bias) and no trading costs. Value, "
                  "quality and growth are not backtested because point-in-time fundamentals are not in free data.",
    }
