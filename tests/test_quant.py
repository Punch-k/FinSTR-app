"""
First test harness in this repo (there wasn't one before — flagged as a
code-quality gap). Scoped to the new quant/ package per the task: forecast
shape validation, allocator output sums to ~1.0, and paper engine fill
simulation against a fixed fixture.

Run with: pytest tests/test_quant.py -v

These tests exercise the no-heavy-dependency fallback paths (heuristic
forecast, naive tilt allocator, naive rebalance) deliberately — they must
pass in plain CI without torch/skfolio/nautilus_trader installed, since
those are pinned but best-effort (see .github/workflows/update-data.yml's
continue-on-error steps). The real-model paths are exercised only when
those packages are actually importable; see test_kronos_real_model_optional.
"""

import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quant.kronos_client import forecast_ticker, FALLBACK_MODEL_NAME
from quant.allocator import equal_weight_prior, marketcap_weights, allocate
from quant.paper_engine import rebalance, STARTING_EQUITY

FORECAST_KEYS = {
    "direction_confidence", "expected_return_pct", "interval_low_pct",
    "interval_high_pct", "volatility_forecast", "model_name",
}

FIXTURE_OHLCV = [
    {"timestamp": f"2026-08-{d:02d}", "open": 100 + d, "high": 101 + d, "low": 99 + d,
     "close": 100 + d * 1.01, "volume": 1_000_000}
    for d in range(1, 21)  # steady uptrend fixture
]

FLAT_OHLCV = [
    {"timestamp": f"2026-08-{d:02d}", "open": 100, "high": 100.5, "low": 99.5,
     "close": 100 + ((-1) ** d) * 0.1, "volume": 500_000}
    for d in range(1, 21)  # noisy/flat fixture — no real trend
]


def test_forecast_shape_and_bounds():
    f = forecast_ticker("TEST", FIXTURE_OHLCV)
    assert set(f.keys()) == FORECAST_KEYS
    assert 0.0 <= f["direction_confidence"] <= 1.0
    assert f["interval_low_pct"] <= f["interval_high_pct"]
    assert f["volatility_forecast"] >= 0.0
    assert isinstance(f["model_name"], str) and f["model_name"]


def test_forecast_empty_window_is_neutral_and_labeled():
    f = forecast_ticker("EMPTY", [])
    assert f["direction_confidence"] == 0.5
    assert f["expected_return_pct"] == 0.0
    assert f["model_name"] == FALLBACK_MODEL_NAME


def test_forecast_uptrend_has_positive_expected_return():
    f = forecast_ticker("UP", FIXTURE_OHLCV)
    assert f["expected_return_pct"] > 0


def test_forecast_flat_window_confidence_not_overconfident():
    f = forecast_ticker("FLAT", FLAT_OHLCV)
    # A genuinely noisy/flat window shouldn't produce near-certain confidence.
    assert f["direction_confidence"] <= 0.95


def test_equal_weight_prior_sums_to_one():
    tickers = ["AAPL", "MSFT", "GOOGL", "AMZN"]
    weights = equal_weight_prior(tickers)
    assert set(weights.keys()) == set(tickers)
    assert math.isclose(sum(weights.values()), 1.0, rel_tol=1e-9)


def test_equal_weight_prior_empty():
    assert equal_weight_prior([]) == {}


def test_marketcap_weights_sums_to_one_and_ignores_nonpositive():
    caps = {"AAPL": 3000, "MSFT": 3000, "TSLA": -5, "GHOST": 0}
    weights = marketcap_weights(caps)
    assert math.isclose(sum(weights.values()), 1.0, rel_tol=1e-9)
    assert weights["TSLA"] == 0.0
    assert weights["GHOST"] == 0.0
    assert math.isclose(weights["AAPL"], 0.5, rel_tol=1e-9)


def test_allocate_output_sums_to_approximately_one():
    tickers = ["AAPL", "MSFT", "GOOGL"]
    prior = equal_weight_prior(tickers)
    forecasts = {
        "AAPL": {"expected_return_pct": 2.0, "direction_confidence": 0.8, "model_name": "test"},
        "MSFT": {"expected_return_pct": -1.0, "direction_confidence": 0.6, "model_name": "test"},
        "GOOGL": {"expected_return_pct": 0.5, "direction_confidence": 0.55, "model_name": "test"},
    }
    weights, method = allocate(tickers, prior, forecasts, historical_returns_df=None)
    assert set(weights.keys()) == set(tickers)
    assert math.isclose(sum(weights.values()), 1.0, rel_tol=1e-6)
    assert all(w >= 0 for w in weights.values())
    # historical_returns_df=None -> can never take the real Black-Litterman path;
    # the returned method must say so, not silently claim it.
    assert method == "confidence-weighted-tilt"


def test_allocate_no_forecasts_returns_prior():
    tickers = ["AAPL", "MSFT"]
    prior = equal_weight_prior(tickers)
    weights, method = allocate(tickers, prior, {}, historical_returns_df=None)
    assert weights == prior
    assert method == "confidence-weighted-tilt"


def test_paper_engine_rebalance_from_flat_start():
    tickers = ["AAPL", "MSFT"]
    target_weights = {"AAPL": 0.6, "MSFT": 0.4}
    current_positions = {}
    prices = {"AAPL": 200.0, "MSFT": 400.0}
    cash = STARTING_EQUITY

    new_positions, new_cash = rebalance(tickers, target_weights, current_positions, prices, cash)

    assert set(new_positions.keys()) == set(tickers)
    for t in tickers:
        assert new_positions[t]["qty"] > 0
        assert new_positions[t]["avg_price"] == prices[t]

    equity = new_cash + sum(new_positions[t]["qty"] * prices[t] for t in tickers)
    # No fees/slippage modeled in the fallback path — equity should be conserved.
    assert math.isclose(equity, STARTING_EQUITY, rel_tol=1e-6)


def test_paper_engine_rebalance_toward_target_from_existing_position():
    tickers = ["AAPL"]
    current_positions = {"AAPL": {"qty": 10.0, "avg_price": 150.0, "unrealized_pnl": 0.0}}
    prices = {"AAPL": 200.0}
    cash = 80_000.0  # existing 10 shares @ $200 = $2,000 + $80,000 cash = $82,000 equity

    # Target 100% AAPL -> should buy more shares, not sell.
    new_positions, new_cash = rebalance(tickers, {"AAPL": 1.0}, current_positions, prices, cash)
    assert new_positions["AAPL"]["qty"] > current_positions["AAPL"]["qty"]
    assert new_cash < cash


def test_paper_engine_never_touches_a_broker():
    """
    Structural guardrail, not just a label: paper_engine module must not
    import anything resembling a live execution/broker client.
    """
    import quant.paper_engine as pe
    import inspect

    source = inspect.getsource(pe)
    forbidden = ["ib_insync", "alpaca", "interactive_brokers", "LiveExecClient", "live_execution"]
    for term in forbidden:
        assert term not in source, f"paper_engine.py must stay backtest/paper-only, found reference to {term!r}"


# ---------- scoreboard: real price-based scoring (was a self-comparing placeholder) ----------

def test_build_returns_df_shape_and_no_nans():
    from quant.run_quant_cycle import _build_returns_df

    dates = [f"2026-08-{d:02d}T00:00:00-04:00" for d in range(1, 21)]
    window_a = [{"timestamp": d, "close": 100 + i * 0.5} for i, d in enumerate(dates)]
    window_b = [{"timestamp": d, "close": 200 - i * 0.3} for i, d in enumerate(dates)]

    df = _build_returns_df({"A": window_a, "B": window_b})
    assert df is not None
    assert list(df.columns) == ["A", "B"]
    assert len(df) == len(dates) - 1  # pct_change drops the first row
    assert not df.isna().any().any()


def test_build_returns_df_too_little_history_returns_none():
    from quant.run_quant_cycle import _build_returns_df

    # Only one ticker, or too few bars -> not enough to fit a covariance matrix.
    assert _build_returns_df({}) is None
    assert _build_returns_df({"A": [{"timestamp": "2026-08-01", "close": 100}]}) is None


def test_score_due_forecasts_uses_real_price_not_prediction(monkeypatch, tmp_path):
    """
    Regression test for the original bug: score_due_forecasts() used to set
    actual_return_pct = predicted_return_pct (grading every forecast "correct"
    against itself). This confirms it now computes a real return from
    PriceAtForecast vs. the price fetched at scoring time.
    """
    import quant.db as qdb
    import quant.run_quant_cycle as rqc

    test_db = str(tmp_path / "quant_scoring_test.db")
    qdb.SQLITE_DATABASE = test_db
    qdb.init_quant_schema(test_db)

    past = (rqc.datetime.now(rqc.timezone.utc) - rqc.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qdb.execute_update(
        """INSERT INTO quant_forecast_outcomes
           (Ticker, GeneratedAt, HorizonHours, PredictedDirection, PredictedReturnPct, PriceAtForecast)
           VALUES (?, ?, ?, ?, ?, ?);""",
        ("TEST", past, 24, "up", 5.0, 100.0),  # predicted +5%, forecast-time price $100
        db_path=test_db,
    )

    # Stub the network fetch: realized price is $110 -> real return should be +10%, not the predicted +5%.
    monkeypatch.setattr(rqc, "_fetch_ohlcv", lambda ticker, days=5: [{"close": 110.0}])

    scored = rqc.score_due_forecasts()
    assert scored == 1

    row = qdb.execute_query(
        "SELECT ActualReturnPct, DirectionCorrect FROM quant_forecast_outcomes WHERE Ticker='TEST';",
        db_path=test_db,
    )
    actual_return_pct, direction_correct = row[0]
    assert math.isclose(actual_return_pct, 10.0, rel_tol=1e-6)  # real (100->110), not the predicted 5.0
    assert direction_correct == 1


def test_quant_schema_migration_adds_column_to_preexisting_db(tmp_path):
    """
    Regression test for a real deploy risk: CREATE TABLE IF NOT EXISTS is a
    no-op against an already-existing table, so a pre-existing quant.db
    (e.g. committed by an earlier CI run, before PriceAtForecast existed)
    must still get the new column via migration, not break every insert.
    """
    import sqlite3
    import quant.db as qdb

    test_db = str(tmp_path / "quant_premigration.db")
    conn = sqlite3.connect(test_db)
    conn.execute("""CREATE TABLE quant_forecast_outcomes (
        Ticker TEXT NOT NULL, GeneratedAt TEXT NOT NULL, HorizonHours INTEGER NOT NULL,
        PredictedDirection TEXT NOT NULL, PredictedReturnPct REAL NOT NULL,
        ActualReturnPct REAL, DirectionCorrect INTEGER, ScoredAt TEXT,
        PRIMARY KEY (Ticker, GeneratedAt)
    );""")
    conn.commit()
    conn.close()

    qdb.init_quant_schema(test_db)

    columns = {row[1] for row in qdb.execute_query("PRAGMA table_info(quant_forecast_outcomes);", db_path=test_db)}
    assert "PriceAtForecast" in columns
