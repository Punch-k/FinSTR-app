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
    weights = allocate(tickers, prior, forecasts, historical_returns_df=None)
    assert set(weights.keys()) == set(tickers)
    assert math.isclose(sum(weights.values()), 1.0, rel_tol=1e-6)
    assert all(w >= 0 for w in weights.values())


def test_allocate_no_forecasts_returns_prior():
    tickers = ["AAPL", "MSFT"]
    prior = equal_weight_prior(tickers)
    weights = allocate(tickers, prior, {}, historical_returns_df=None)
    assert weights == prior


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
