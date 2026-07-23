# FinSTR — Knowledge Base for Next Session

Read this before starting work. It captures what's true about this repo as of 2026-07-23,
what was built this session, and what's still open — so the next session doesn't have to
re-derive it.

---

## What this repo is

FinSTR (`Punch-k/FinSTR-app`) is a Flask app cloned/adapted from an existing "MyShare"
portfolio manager (original author: Vlad Litvak). Two parts:

1. **Screener** — a finviz-style stock screener/heatmap. `app.py` serves it locally with
   live `yfinance` data; GitHub Pages serves it statically (no Flask on Pages).
2. **MyShare** — a portfolio/lot-tracking manager under `/myshare/*`. Only works when
   Flask is running locally; not reachable on GitHub Pages at all.

The public URL is https://punch-k.github.io/FinSTR-app/ — always the source of truth for
what's actually live. Don't assume a local clone reflects it without checking; the two
have drifted before (see "Two ticker lists" below).

---

## What changed this session (2026-07-23)

**Problem:** GitHub Pages can't run Flask, so the live site had no way to show real,
precise data for 100+ companies with news — it only had whatever was hardcoded into
`index.html` as demo seed data.

**Solution:** a static data pipeline that requires no server:

- `scripts/tickers.py` — single source of truth for the ticker universe (126 tickers,
  all 11 GICS sectors). Imported by both `app.py` (live Flask mode) and
  `scripts/fetch_data.py` (static mode), so they can't drift apart again.
- `scripts/fetch_data.py` — standalone script (no Flask, no SQLite) that fetches
  fundamentals + top-3 news per ticker via `yfinance` and writes `data/screener.json` +
  `data/meta.json`.
- `.github/workflows/update-data.yml` — GitHub Action, cron `0 */6 * * *` (every 6h) +
  manual `workflow_dispatch`, runs `fetch_data.py` and commits the JSON back to `main`
  as `github-actions[bot]`. Free on GitHub-hosted runners for a public repo.
- `index.html` `init()` — now tries `/api/screener` (local Flask) → `data/screener.json`
  (GitHub Pages) → hardcoded seed data (last resort), in that order.

**Result:** 126/126 tickers fetch successfully with real prices, market caps, and news
(verified locally before pushing — see "yfinance version pin" below).

**Committed as 4 separate GitHub web-UI uploads** (user's explicit choice, to keep this
attributable to their account rather than a git-CLI push): scripts/, workflow, data/,
then the 5 modified root files (`app.py`, `index.html`, `README.md`, `requirements.txt`,
`PipInstalls.sh`).

---

## Two ticker lists — do not conflate them

There were, and still partially are, **two separate, independent ticker datasets**:

1. **`app.py` / `scripts/tickers.py`** (the "real" backend list) — was 74, now 126.
   Real fundamentals fetched live via `yfinance`.
2. **`index.html`'s hardcoded SEED_DATA** (the offline last-resort fallback) — a Jul 13
   commit ("Expand screener to 278 tickers...") expanded this to 278 tickers, but
   explicitly used **synthetically generated** valuation fields (PE, PEG, beta, etc.)
   "deterministically synthesized... within realistic per-sector ranges" — i.e. fake
   numbers dressed to look plausible, only ticker/name/sector/marketCap were real.

These were never the same list and never synced. As of this session, `data/screener.json`
(126 real tickers, no synthesized fields) sits **above** SEED_DATA in the fallback chain,
so on a working deploy SEED_DATA should rarely if ever be what users see. But SEED_DATA
(278, partly fake) is still in `index.html` as the last-resort fallback — worth deciding
whether to shrink/replace it later so a fetch failure doesn't silently show fabricated
numbers.

---

## Known trap: yfinance version

The sandbox environment had `yfinance==0.2.41` pinned, which fails **100% of requests**
with HTTP 429 against Yahoo's current crumb/cookie auth — looks like a rate limit but
isn't; it's simply too old. Upgrading to `yfinance>=1.5.1` fixed it completely (126/126
succeeded). `requirements.txt`, `PipInstalls.sh`, and the GitHub Actions workflow are all
pinned to `>=1.5.1` now. **If fetches start failing with 429s again, check the yfinance
version first** before assuming Yahoo is blocking the CI runner's IP.

---

## Design roadmap — not yet started

These are real next steps, not done this session. Ordered by leverage.

### 1. Close the loop on the news relevancy problem
Per-ticker news currently comes from `yfinance.Ticker(t).news`, which is Yahoo's own
ticker-scoped feed — already relevancy-matched by Yahoo, no keyword filtering built by
us. This avoids the "Apple the fruit vs AAPL" false-positive problem entirely by
construction, but coverage is uneven: some tickers (e.g. LOW, seen this session) return
zero articles at a given point in time. Options if this matters more later: fall back to
Google News RSS filtered by company name for tickers with sparse Yahoo coverage (the Jul
13 commit already integrated Google News RSS as a *general* market news source — could be
adapted per-ticker), or accept sparse coverage as an honest state rather than padding it.

### 2. Precision validation against finviz
Nobody has yet compared FinSTR's fetched numbers against finviz.com's displayed values
ticker-by-ticker to quantify "precision." Pick ~10 tickers spanning sectors, record
finviz's P/E, market cap, dividend yield, etc., and compare against the same fields in
`data/screener.json`. This turns "precision" from a vague goal into a measurable one and
would catch unit mismatches (e.g. `marketCap` here is stored in billions, dividendYield
as a percent not a decimal — worth double-checking these conventions match what the
frontend expects across all consumers).

### 3. SEED_DATA cleanup
Decide whether to shrink `index.html`'s 278-ticker synthetic SEED_DATA down to something
smaller-but-real, or clearly label it as illustrative-only in the UI if it's ever shown.
Right now it's a silent last-resort fallback a user could hit without realizing the
numbers aren't real (only reachable if both `/api/screener` and `data/screener.json`
fail — should be rare, but not impossible, e.g. if a future Action run breaks the fetch
script and nobody notices for a while).

### 4. Ticker list further expansion / maintenance
126 is comfortably over 100 across all 11 sectors, but there's no process yet for adding
or retiring tickers over time (e.g. a company gets delisted or acquired — `yfinance` will
just start failing silently for that symbol, and `fetch_data.py` already logs and skips
failures to `data/meta.json.errors`, which is the place to check periodically).

### 5. Wire up the embedded per-ticker news field (or drop it)
Verified live: the stock detail modal already has its own separate client-side news
fetch (tries Google News RSS via proxy, falls back to Yahoo Finance — this predates this
session and works fine on its own). It does **not** read the `news` array this session
added to each ticker in `data/screener.json` — that field is fetched and stored but
currently unused by the frontend. Either wire it in as an additional fallback tier (useful
if the client-side proxy fetch gets rate-limited, which the Jul 13 commit message notes
happens intermittently), or drop it from `fetch_data.py` to stop paying the extra
Yahoo-request cost for data nothing reads.

### 6. MyShare portfolio manager is untouched
Out of scope this session — still only works with Flask running locally, not reachable
from the GitHub Pages deployment at all. Not addressed by the static-data pipeline built
here, since it involves user accounts/auth which don't fit a static-site model without a
real backend or third-party auth service.

---

## Working conventions established this session

- **Commit method:** GitHub web upload UI (drag/drop-equivalent via file input), not
  `git push` from the terminal, and not editing files one-by-one in GitHub's inline
  editor. The user wants commits to originate from their own GitHub account/browser
  session, not carry any AI attribution or "generated by" framing. New file-input uploads
  must be scoped to the correct target directory via URL
  (`/upload/main/<path>`) — GitHub's flat upload UI does not preserve relative paths for
  new nested folders otherwise.
- **Validate before pushing:** always run the fetch script and check output data quality
  locally before committing anything to the live repo — caught the yfinance version
  issue this way before it went out as a broken pipeline.
- **Don't assume a deploy is live immediately.** GitHub Pages deployments queue and take
  ~30–60s; rapid successive commits cause GitHub to cancel intermediate in-flight
  deployments (shows as a red X — this is normal, not a real failure, only the *final*
  deployment in a rapid sequence needs to be checked for success).
