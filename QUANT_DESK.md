# Quant Desk — Kronos → skfolio → NautilusTrader

A self-contained subsystem added to FinSTR that generates a per-ticker directional
forecast, turns it into target portfolio weights, and simulates paper (not real)
fills against those weights on a running schedule. Read this before touching
anything in `quant/`.

## Why batch, not a live daemon

A live NautilusTrader deployment for real equities needs a persistent process and a
broker adapter (e.g. Interactive Brokers) — real infrastructure this project doesn't
have and shouldn't fake. Instead, Quant Desk piggybacks on the exact same
infrastructure the screener already uses:

- **Same cron.** `.github/workflows/update-data.yml` now runs `quant/run_quant_cycle.py`
  right after `scripts/fetch_data.py`, on the existing 6-hour schedule. No new
  scheduler, no new hosting.
- **Same persistence model.** State (forecasts, target weights, paper account, equity
  history) is written to SQLite and to a static JSON snapshot
  (`data/quant_snapshot.json`) — the same two-tier pattern `data/screener.json`
  already established for GitHub Pages (which runs no server, so it reads the JSON;
  a local `python app.py` reads the live `/api/quant/*` endpoints, which read SQLite).
- **No always-on server required** beyond what already runs the site.

This ships a working v1 with zero new hosting and zero new operational surface.

## The pipeline, end to end

```
scripts/fetch_data.py's OHLCV pull
        │
        ▼
quant/kronos_client.py   — NeoQuasar/Kronos-mini forecasts a distribution over
                            future OHLCV bars; summarized into direction_confidence,
                            expected_return_pct, an interval, and volatility.
        │
        ▼
quant/allocator.py       — skfolio's BlackLitterman: prior = equal-weight (or
                            market-cap, if wired with real caps), views = Kronos
                            forecasts, view_confidences = direction_confidence
                            (Idzorek's method — a low-confidence Kronos call gets
                            correspondingly little weight in the posterior).
        │
        ▼
quant/paper_engine.py    — NautilusTrader BacktestEngine simulates the fills
                            needed to move from current paper positions to the
                            new target weights, at the latest known price.
        │
        ▼
quant/db.py (database/quant.db)  +  data/quant_snapshot.json
        │
        ▼
quant/routes.py  →  /api/quant/forecast/<ticker>, /allocation, /paper/pnl, /scoreboard
        │
        ▼
index.html — forecast card in the stock modal, "Quant Desk" nav section
```

## Why this is a separate SQLite file, not `database/MyShare.db`

`database/MyShare.db` is gitignored on purpose — it holds real user accounts and
holdings, and must never be committed. But CI needs to *commit* the quant state so
the GitHub Pages demo and any fresh `python app.py` checkout can read it back
immediately. `database/quant.db` holds only public, model-derived data (forecasts,
allocation targets, the paper account) — never user data — so it's the one `.db`
file this repo intentionally commits (see the explicit exception in `.gitignore`).

## Paper-only, by construction

- `quant/paper_engine.py` only ever imports `nautilus_trader.backtest.engine.BacktestEngine`
  — never a live execution client, never a broker adapter, never API keys for a real
  venue. `tests/test_quant.py::test_paper_engine_never_touches_a_broker` asserts the
  module's source contains no reference to a live broker integration, so this can't
  silently regress.
- The starting paper balance (`quant.paper_engine.STARTING_EQUITY`, $100,000) is
  fictitious and never connected to any funding source.
- Every model-derived surface in the UI is labeled — the forecast card, the equity
  curve, and the positions table all carry "simulated"/"paper trading, not real
  money" language, mirroring the existing `hasRealFundamentals`/`estTag()`
  convention that already distinguishes real screener data from `SEED_DATA`.

## The forecast-accuracy scoreboard (the differentiating piece)

Most "AI stock picker" surfaces never expose whether their own calls were right.
Quant Desk does, automatically: every forecast is recorded in
`quant_forecast_outcomes` with its predicted direction, and
`quant/run_quant_cycle.py::score_due_forecasts()` grades it against the realized
outcome once its horizon (24h) has elapsed — no manual curation, no cherry-picking.
`GET /api/quant/scoreboard` and the "Forecast Accuracy Scoreboard" panel in the
Quant Desk UI surface the running direction-accuracy rate, overall and per ticker.

**Current limitation, tracked honestly rather than hidden:** the outcome scorer
compares the forecast's *predicted* return to itself as a placeholder for the
*actual* realized return (see the `actual_return_pct = predicted_return_pct`
placeholder in `score_due_forecasts()`). A correct implementation needs a price
snapshot taken at forecast-generation time to diff against the price 24h later —
that join isn't wired yet. Until it is, treat the scoreboard's numbers as
structurally present but not yet trustworthy; this is the first thing to fix before
leaning on the scoreboard for anything real.

## Running it locally

```bash
# 1. Install the pinned deps (torch/transformers/skfolio/nautilus_trader/pandas)
pip install -r requirements.txt

# 2. Install Kronos (not on PyPI — installed from source)
pip install "git+https://github.com/shiyu-coder/Kronos.git@main"

# 3. Run one cycle by hand
python -m quant.run_quant_cycle
# writes/updates database/quant.db and data/quant_snapshot.json

# 4. Run the tests (these pass even without step 1/2 installed — they exercise
#    the heuristic/naive fallback paths deliberately, see tests/test_quant.py's
#    module docstring)
pip install pytest
pytest tests/test_quant.py -v

# 5. Run the full app and hit the live endpoints
python app.py
curl http://localhost:1817/api/quant/allocation
```

No environment variables are required for Quant Desk itself (unlike MyShare's
`ADMIN_EMAIL_ADDRESS`/`ADMIN_EMAIL_PASSWORD` for password reset emails) — everything
here is public market data plus a locally-loaded, CPU-only model.

## What's deliberately NOT here

- **No GPU requirement anywhere.** `NeoQuasar/Kronos-mini` (4.1M params) was chosen
  specifically because it's the smallest, most CPU-friendly Kronos variant — verified
  against the official repo README, not assumed. `quant/kronos_client.py` hardcodes
  `device="cpu"`.
- **No multi-user paper accounts / auth.** All Quant Desk endpoints are public,
  read-only, and derived from data everyone sees — deliberately *not* extending
  MyShare's param-based `id`+`password`-per-request pattern, which the code quality
  report already flags as a gap. If Quant Desk ever needs per-user paper accounts,
  that should wait until MyShare gets real session-token auth, not duplicate the
  current workaround.
- **No new datastore, no new hosting, no new blueprint sprawl in `app.py`.** Quant
  Desk is `app.py`'s first blueprint (`quant/routes.py`) — a template for eventually
  splitting the rest of `app.py`'s ~20 Resource classes the same way.

## Known gaps / next steps

1. Wire the price-snapshot-at-forecast-time join so the accuracy scoreboard reflects
   real outcomes (see "Current limitation" above) — this is the priority before the
   scoreboard is trustworthy.
2. `QUANT_TICKERS` in `run_quant_cycle.py` is capped at the first 20 of
   `SCREENER_TICKERS` to keep each CI run's runtime/inference cost bounded — expand
   once real Kronos inference latency is measured in CI (the "Install Kronos" and
   "Run Quant Desk cycle" workflow steps both run with `continue-on-error: true`
   today specifically because that latency hasn't been measured yet).
3. `quant/allocator.py`'s Black-Litterman prior defaults to equal-weight, not a real
   60/40 or market-cap benchmark — `marketcap_weights()` exists and is ready to wire
   in once `run_quant_cycle.py` passes it real market caps from the screener data.
4. skfolio's Black-Litterman path in `allocator.py` needs a historical returns
   DataFrame to fit a covariance matrix; `run_quant_cycle.py` currently calls
   `allocate()` with `historical_returns_df=None`, so it always takes the naive
   confidence-weighted-tilt fallback today, not the real skfolio posterior. Wiring
   in `yfinance`'s historical closes (already fetched per-ticker in
   `_fetch_ohlcv()`) into a wide returns DataFrame is the next step to exercise the
   real skfolio path in production.
