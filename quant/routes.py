"""
Quant Desk blueprint — read-only endpoints over the state quant/run_quant_cycle.py
persists. Deliberately kept out of app.py per the existing recommendation to
move toward blueprints (this is the first one).

All endpoints are GET-only and derived entirely from public market data +
model output — no user accounts, no per-request password param. This is
intentional: MyShare's param-based auth (id+password on every request) is
already flagged as a gap in the code quality report, and this feature must
not spread that pattern. If Quant Desk ever needs multi-user paper accounts,
that should wait for MyShare to get real session auth first, not duplicate
the current workaround.
"""

import json

from flask import Blueprint, Response
from flask_restful import Api, Resource

from quant import db

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_INTERNAL_SERVER_ERROR = 500

quant_bp = Blueprint("quant", __name__)
quant_api = Api(quant_bp)


def _json_response(payload, status=HTTP_OK):
    resp = Response(json.dumps(payload), status=status, mimetype="application/json")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


class QuantForecast(Resource):
    def get(self, ticker):
        ticker = ticker.upper()
        try:
            rows = db.execute_query(
                """SELECT Ticker, GeneratedAt, DirectionConfidence, ExpectedReturnPct,
                          IntervalLowPct, IntervalHighPct, VolatilityForecast, ModelName
                   FROM quant_forecasts WHERE Ticker = ? ORDER BY GeneratedAt DESC LIMIT 1;""",
                (ticker,),
            )
        except Exception:
            return _json_response({"error": "Internal error"}, HTTP_INTERNAL_SERVER_ERROR)

        if not rows:
            return _json_response({"error": f"No forecast available for {ticker}"}, HTTP_NOT_FOUND)

        t, generated_at, confidence, exp_ret, lo, hi, vol, model = rows[0]
        return _json_response({
            "ticker": t,
            "horizon_hours": 24,
            "direction_confidence": confidence,
            "expected_return_pct": exp_ret,
            "interval_low_pct": lo,
            "interval_high_pct": hi,
            "volatility_forecast": vol,
            "model": model,
            "generated_at": generated_at,
        })


class QuantAllocation(Resource):
    def get(self):
        try:
            latest = db.execute_query("SELECT MAX(AsOf) FROM quant_allocation;")
        except Exception:
            return _json_response({"error": "Internal error"}, HTTP_INTERNAL_SERVER_ERROR)

        as_of = latest[0][0] if latest and latest[0][0] else None
        if not as_of:
            return _json_response({"as_of": None, "benchmark": None, "positions": []})

        rows = db.execute_query(
            """SELECT a.Ticker, a.TargetWeight, a.CurrentWeight, a.Benchmark, f.DirectionConfidence
               FROM quant_allocation a
               LEFT JOIN (
                   SELECT Ticker, DirectionConfidence FROM quant_forecasts
                   WHERE (Ticker, GeneratedAt) IN (
                       SELECT Ticker, MAX(GeneratedAt) FROM quant_forecasts GROUP BY Ticker
                   )
               ) f ON f.Ticker = a.Ticker
               WHERE a.AsOf = ?
               ORDER BY a.TargetWeight DESC;""",
            (as_of,),
        )
        benchmark = rows[0][3] if rows else None
        positions = [
            {
                "ticker": t,
                "target_weight": round(target, 4),
                "current_weight": round(current, 4),
                "delta": round(target - current, 4),
                "confidence": confidence if confidence is not None else None,
            }
            for (t, target, current, _bench, confidence) in rows
        ]
        return _json_response({"as_of": as_of, "benchmark": benchmark, "positions": positions})


class QuantPaperPnl(Resource):
    def get(self):
        try:
            account_rows = db.execute_query(
                "SELECT AsOf, Equity, Cash, StartingEquity FROM quant_paper_account ORDER BY AsOf DESC LIMIT 1;"
            )
        except Exception:
            return _json_response({"error": "Internal error"}, HTTP_INTERNAL_SERVER_ERROR)

        if not account_rows:
            return _json_response({
                "as_of": None, "equity": None, "starting_equity": None,
                "pnl_pct": None, "history": [], "open_positions": [],
            })

        as_of, equity, cash, starting_equity = account_rows[0]
        pnl_pct = round((equity - starting_equity) / starting_equity * 100, 3) if starting_equity else 0.0

        history_rows = db.execute_query(
            "SELECT AsOf, Equity FROM quant_paper_account ORDER BY AsOf ASC LIMIT 500;"
        )
        history = [{"date": d, "equity": e} for (d, e) in history_rows]

        position_rows = db.execute_query(
            "SELECT Ticker, Qty, AvgPrice, UnrealizedPnl FROM quant_paper_positions WHERE AsOf = ? AND Qty != 0;",
            (as_of,),
        )
        open_positions = [
            {"ticker": t, "qty": round(qty, 4), "avg_price": round(avg_price, 4), "unrealized_pnl": round(pnl, 2)}
            for (t, qty, avg_price, pnl) in position_rows
        ]

        return _json_response({
            "as_of": as_of,
            "equity": round(equity, 2),
            "starting_equity": starting_equity,
            "pnl_pct": pnl_pct,
            "history": history,
            "open_positions": open_positions,
        })


class QuantScoreboard(Resource):
    """
    Public forecast-accuracy scoreboard: how often Kronos's directional calls
    were actually right, scored automatically once each forecast's horizon
    has elapsed (see quant/run_quant_cycle.py::score_due_forecasts). This is
    the differentiator described in QUANT_DESK.md — most retail "AI stock
    picker" surfaces never expose whether their own predictions were right.
    """

    def get(self):
        try:
            rows = db.execute_query(
                """SELECT COUNT(*), SUM(DirectionCorrect)
                   FROM quant_forecast_outcomes WHERE ScoredAt IS NOT NULL;"""
            )
            per_ticker = db.execute_query(
                """SELECT Ticker, COUNT(*), SUM(DirectionCorrect)
                   FROM quant_forecast_outcomes WHERE ScoredAt IS NOT NULL
                   GROUP BY Ticker ORDER BY Ticker;"""
            )
        except Exception:
            return _json_response({"error": "Internal error"}, HTTP_INTERNAL_SERVER_ERROR)

        total, correct = (rows[0][0] or 0, rows[0][1] or 0) if rows else (0, 0)
        overall_accuracy = round(correct / total, 4) if total else None

        by_ticker = [
            {
                "ticker": t,
                "scored_forecasts": n,
                "direction_accuracy": round((c or 0) / n, 4) if n else None,
            }
            for (t, n, c) in per_ticker
        ]

        return _json_response({
            "scored_forecasts": total,
            "overall_direction_accuracy": overall_accuracy,
            "by_ticker": by_ticker,
            "note": "Direction-only accuracy vs. realized outcome after each forecast's horizon. "
                    "Not investment advice; historical accuracy does not guarantee future results.",
        })


quant_api.add_resource(QuantForecast, "/api/quant/forecast/<string:ticker>")
quant_api.add_resource(QuantAllocation, "/api/quant/allocation")
quant_api.add_resource(QuantPaperPnl, "/api/quant/paper/pnl")
quant_api.add_resource(QuantScoreboard, "/api/quant/scoreboard")
