"""
Wraps NeoQuasar/Kronos-mini (4.1M params, the smallest/most CPU-friendly Kronos
variant — confirmed against the official repo README at
https://github.com/shiyu-coder/Kronos rather than guessed) for per-ticker
directional forecasts.

Kronos is a K-line (OHLCV candlestick) foundation model: it forecasts a
distribution over future OHLCV bars from a window of historical bars via its
own tokenizer (NeoQuasar/Kronos-Tokenizer-2k), rather than returning a text
sentiment. This module turns that OHLCV forecast into the summary stats the
allocator needs: a directional confidence, an expected return, a low/high
interval, and a volatility estimate.

Honesty convention (matches hasRealFundamentals / estTag() in index.html):
every result carries model_name. If the real Kronos model/tokenizer can't be
loaded (no network access to Hugging Face Hub, missing torch, etc.) this
module falls back to a simple, clearly-labeled statistical heuristic
(recent-window momentum + realized volatility) instead of pretending to be
a model forecast. Callers must never blur the two — the frontend tags
model_name != "Kronos-mini" as an estimate, exactly like SEED_DATA rows.
"""

import math
import statistics

MODEL_ID = "NeoQuasar/Kronos-mini"
TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-2k"
FALLBACK_MODEL_NAME = "heuristic-fallback"
HORIZON_HOURS = 24
CONTEXT_LOOKBACK_DAYS = 90

_model_cache = {"predictor": None, "load_attempted": False, "available": False}


def _try_load_kronos():
    """Loads Kronos once per process. Returns True if the real model is usable."""
    if _model_cache["load_attempted"]:
        return _model_cache["available"]
    _model_cache["load_attempted"] = True
    try:
        import torch  # noqa: F401
        from model import Kronos, KronosTokenizer, KronosPredictor  # provided by the kronos pip package

        tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_ID)
        model = Kronos.from_pretrained(MODEL_ID)
        device = "cpu"  # hard requirement: this pipeline must not assume GPU access
        predictor = KronosPredictor(model, tokenizer, device=device, max_context=512)
        _model_cache["predictor"] = predictor
        _model_cache["available"] = True
    except Exception:
        # Missing torch/kronos package, no network to Hugging Face Hub, or an
        # incompatible environment — fall back rather than crash the fetch cycle.
        _model_cache["available"] = False
    return _model_cache["available"]


def _heuristic_forecast(ohlcv_window):
    """
    No-model fallback: momentum over the lookback window + realized volatility.
    Deliberately simple and explicitly NOT presented as a Kronos forecast —
    model_name distinguishes it so the frontend can label it accordingly.
    """
    closes = [bar["close"] for bar in ohlcv_window if bar.get("close") is not None]
    if len(closes) < 2:
        return {
            "direction_confidence": 0.5,
            "expected_return_pct": 0.0,
            "interval_low_pct": -1.0,
            "interval_high_pct": 1.0,
            "volatility_forecast": 0.0,
            "model_name": FALLBACK_MODEL_NAME,
        }
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1]]
    mean_ret = statistics.mean(returns) if returns else 0.0
    vol = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    expected_return_pct = round(mean_ret * 100, 3)
    vol_pct = round(vol * 100, 3)
    # direction_confidence: how consistently the recent window trended one way,
    # scaled into [0.5, 0.95] so a flat/noisy window reads as "no edge" (0.5)
    # rather than false certainty.
    if returns:
        same_sign = sum(1 for r in returns if (r > 0) == (mean_ret > 0))
        consistency = same_sign / len(returns)
    else:
        consistency = 0.5
    direction_confidence = round(min(0.95, max(0.5, consistency)), 3)
    return {
        "direction_confidence": direction_confidence,
        "expected_return_pct": expected_return_pct,
        "interval_low_pct": round(expected_return_pct - 1.96 * vol_pct, 3),
        "interval_high_pct": round(expected_return_pct + 1.96 * vol_pct, 3),
        "volatility_forecast": round(vol, 5),
        "model_name": FALLBACK_MODEL_NAME,
    }


def _kronos_forecast(ohlcv_window):
    """Real Kronos inference path. Only reached if _try_load_kronos() succeeded."""
    predictor = _model_cache["predictor"]
    import pandas as pd

    df = pd.DataFrame(ohlcv_window)
    df["timestamps"] = pd.to_datetime(df["timestamp"])
    pred_df = predictor.predict(
        df=df[["open", "high", "low", "close", "volume"]],
        x_timestamp=df["timestamps"],
        y_timestamp=None,
        pred_len=max(1, HORIZON_HOURS // 24 * 6),  # ~intraday bars covering the horizon
        T=1.0,
        top_p=0.9,
        sample_count=8,  # multiple samples -> distribution, not a single point forecast
    )
    last_close = float(df["close"].iloc[-1])
    forecast_closes = pred_df["close"].to_numpy()
    if len(forecast_closes) == 0 or not last_close:
        return _heuristic_forecast(ohlcv_window)
    rets_pct = (forecast_closes - last_close) / last_close * 100
    mean_ret = float(rets_pct.mean())
    vol = float(rets_pct.std(ddof=0)) if len(rets_pct) > 1 else 0.0
    up_frac = float((rets_pct > 0).mean())
    direction_confidence = round(max(up_frac, 1 - up_frac), 3)
    return {
        "direction_confidence": direction_confidence,
        "expected_return_pct": round(mean_ret, 3),
        "interval_low_pct": round(float(rets_pct.min()), 3),
        "interval_high_pct": round(float(rets_pct.max()), 3),
        "volatility_forecast": round(vol / 100, 5),
        "model_name": MODEL_ID,
    }


def forecast_ticker(ticker, ohlcv_window):
    """
    ohlcv_window: list of dicts [{timestamp, open, high, low, close, volume}, ...]
    ordered oldest -> newest, ideally covering CONTEXT_LOOKBACK_DAYS.

    Returns the shared forecast shape documented in QUANT_DESK.md, always
    including model_name so downstream consumers (allocator, API, frontend)
    can tell a real Kronos forecast from the heuristic fallback.
    """
    if not ohlcv_window:
        return {
            "direction_confidence": 0.5,
            "expected_return_pct": 0.0,
            "interval_low_pct": 0.0,
            "interval_high_pct": 0.0,
            "volatility_forecast": 0.0,
            "model_name": FALLBACK_MODEL_NAME,
        }
    if _try_load_kronos():
        try:
            return _kronos_forecast(ohlcv_window)
        except Exception:
            # A real-model failure at inference time (bad shapes, OOM, etc.)
            # degrades to the heuristic rather than failing the whole cycle.
            return _heuristic_forecast(ohlcv_window)
    return _heuristic_forecast(ohlcv_window)
