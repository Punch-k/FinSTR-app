"""
Wraps skfolio's Black-Litterman estimator to turn per-ticker Kronos forecasts
into target portfolio weights.

Prior: equal-weight across CANDIDATE_TICKERS by default (documented, honest
placeholder — the screener has no existing benchmark-weighting logic to
reuse, so this does not pretend to be market-cap-weighted unless
marketcap_weights() is explicitly wired with real market caps from
scripts/fetch_data.py's output).

Views: one absolute view per ticker with a forecast, taken directly from
Kronos's expected_return_pct. View confidence = direction_confidence,
passed straight into skfolio's Idzorek's-method view_confidences parameter
(0..1), so a low-confidence Kronos forecast gets correspondingly little
weight in the posterior — no separate uncertainty model invented here.
"""

CANDIDATE_TICKERS_FALLBACK_WEIGHT = None  # set by caller; see equal_weight_prior()


def equal_weight_prior(tickers):
    n = len(tickers)
    if n == 0:
        return {}
    w = 1.0 / n
    return {t: w for t in tickers}


def marketcap_weights(ticker_marketcaps):
    """ticker_marketcaps: {ticker: marketCap}. Returns normalized weights."""
    total = sum(v for v in ticker_marketcaps.values() if v and v > 0)
    if not total:
        return equal_weight_prior(list(ticker_marketcaps.keys()))
    return {t: (v / total if v and v > 0 else 0.0) for t, v in ticker_marketcaps.items()}


def _try_skfolio_allocate(tickers, prior_weights, forecasts, historical_returns_df):
    """
    Real skfolio Black-Litterman path.

    historical_returns_df: pandas DataFrame of historical daily returns,
    columns = tickers, used to fit the empirical prior estimator that
    Black-Litterman updates with the Kronos views (per skfolio's
    prior-estimator composition pattern: BlackLitterman wraps a fitted
    prior and layers analyst views on top of it).
    """
    from skfolio.prior import BlackLitterman, EmpiricalPrior
    from skfolio.optimization import MeanRisk, ObjectiveFunction

    views = []
    view_confidences = []
    view_tickers = []
    for t in tickers:
        f = forecasts.get(t)
        if not f:
            continue
        views.append(f"{t} == {f['expected_return_pct'] / 100.0:.6f}")
        view_confidences.append(max(0.01, min(0.99, f["direction_confidence"])))
        view_tickers.append(t)

    if not views:
        return prior_weights

    bl = BlackLitterman(
        views=views,
        view_confidences=view_confidences,
        prior_estimator=EmpiricalPrior(),
    )
    model = MeanRisk(objective_function=ObjectiveFunction.MAXIMIZE_RATIO, prior_estimator=bl)
    model.fit(historical_returns_df[tickers])
    weights = model.weights_
    return {t: float(w) for t, w in zip(tickers, weights)}


def _naive_tilt_allocate(tickers, prior_weights, forecasts):
    """
    No-skfolio fallback: tilts the prior toward tickers with a positive,
    high-confidence Kronos view and away from negative ones, then
    renormalizes to sum to 1. Simpler than real Black-Litterman covariance
    shrinkage, but keeps the pipeline functional (and honestly labeled via
    the "benchmark" field in the API response) if skfolio isn't installed.
    """
    tilted = {}
    for t in tickers:
        base = prior_weights.get(t, 0.0)
        f = forecasts.get(t)
        if f:
            tilt = (f["expected_return_pct"] / 100.0) * f["direction_confidence"]
        else:
            tilt = 0.0
        tilted[t] = max(0.0, base * (1.0 + tilt))
    total = sum(tilted.values())
    if not total:
        return prior_weights
    return {t: w / total for t, w in tilted.items()}


def allocate(tickers, prior_weights, forecasts, historical_returns_df=None):
    """
    Returns {ticker: target_weight}, weights summing to ~1.0.

    Tries the real skfolio Black-Litterman path first (requires
    historical_returns_df); falls back to a simple confidence-weighted tilt
    of the prior if skfolio isn't installed or there isn't enough return
    history yet to fit a covariance matrix.
    """
    if historical_returns_df is not None and len(tickers) >= 2:
        try:
            return _try_skfolio_allocate(tickers, prior_weights, forecasts, historical_returns_df)
        except Exception:
            pass
    return _naive_tilt_allocate(tickers, prior_weights, forecasts)
