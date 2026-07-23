# FinSTR — Knowledge Base for Next Session

Read this before starting work. It captures what's true about this repo as of 2026-07-23
(two sessions that day), what was built, and what's still open — so the next session
doesn't have to re-derive it.

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

## Session 2 (2026-07-23, later same day): UX audit + 10-task roadmap

Did a live user-journey audit of https://punch-k.github.io/FinSTR-app/ against real
finviz.com, found concrete drop-off points, then implemented a 10-item roadmap. Findings
and what got built:

**Drop-off points found (ranked by how many visitors hit them):**
1. Register/Login (top-right primary CTA position) were fully non-functional — silent
   dead end for every visitor's first instinct.
2. Portfolio nav item dead-ended to "run Python locally" with no warning.
3. News page rendered near-duplicate headlines in both columns (root cause: the Yahoo
   Finance fallback path returns overlapping top stories for two differently-worded
   queries — not literally duplicated code, but no cross-column dedup existed).
4. Futures/Forex showed plain "Loading…" text with no visual progress for ~5-8s
   (client-side yfinance-via-proxy fetch, no server cache) — reads as broken before it
   resolves.
5. No global ticker search reachable from Home/Maps/Groups — only inside Screener/Charts.

**Also found while reading COLUMN_DEFS (not part of the original 10, fixed anyway):**
P/E, Forward P/E, PEG, ROE, Profit Margin, Dividend Yield, EPS, and Beta were all
**hardcoded** with a static "~ Estimated, not live" column header, written back when the
only data source was a client-side fetch blocked by Yahoo's auth wall. That's no longer
true — Session 1's `data/screener.json` pipeline fetches these fields for real, server-side,
via GitHub Actions. Fixed: `estTag(s)` now marks a row as estimated only when
`s.hasRealFundamentals` is false (true for every row from `/api/screener` or
`data/screener.json`; false only for the hardcoded SEED_DATA fallback). See `estTag()` and
the `hasRealFundamentals` stamping in `init()` in `index.html`.

**All 10 roadmap tasks — status:**

| # | Task | Status |
|---|---|---|
| 1 | Fix News duplicate content | Done — `dedupeAgainst()` filters the Blogs column against whatever the main column already showed |
| 2 | Loading skeleton for Futures/Forex | Done — `skeletonTiles()` + `.skeleton-tile` pulse animation |
| 3 | "Demo" badges on Register/Portfolio | Done — `.demo-badge` pill + tooltip, so the dead-end is expected not a surprise |
| 4 | Deep-linkable stock URLs | Done — `#...&stock=AAPL` opens that stock's modal on load; added a "Copy Link" button in the modal (reuses existing `copyShareLink()`) |
| 5 | Watchlist persistence | Was already working via `localStorage('finstr_watchlist')` — added a one-time toast confirming it persists, since there's no account system to otherwise reassure the user |
| 6 | Global ticker search | Done — search box in the top nav bar, visible on every view, jumps straight to a stock's modal |
| 7 | Expand column presets | Done — added "Volume" (volume/avgVolume/relative volume — `avgVolume` was already fetched but never surfaced anywhere) and "All" (every real field in one view) |
| 8 | Insider Trading tab | Done — new data field via `yfinance.Ticker(t).insider_transactions` (real SEC Form 4 data), new nav tab, flattened/sorted feed across all tickers |
| 9 | Real accounts / server-side saved screeners | **Not done — blocked, needs a decision from the user.** This requires either standing up a real backend + auth (contradicts the "free GitHub Pages, no server" constraint the whole pipeline was built around) or picking a third-party service (Supabase/Firebase free tier, etc.) which needs credentials/account setup only the user can provide. Flagged rather than faked. |
| 10 | Crypto + Calendar tabs | Done, split into two — **Crypto**: reuses the existing `fetchMktDirect()`/`ftTile()` pattern already used for Futures, real live prices via Yahoo chart endpoint, no new data source. **Calendar**: new `yfinance.Ticker(t).calendar` field (earnings date + ex-dividend date), explicitly labeled in the UI as scoped to FinSTR's own 126 tickers, not a full market economic calendar (that needs a paid/keyed API this project doesn't have) |

**Data pipeline additions this session:** `insiderTransactions` (top 5 per ticker) and
`earningsDate`/`exDividendDate` added to `scripts/fetch_data.py`'s `fetch_ticker_data()`,
mirrored into `app.py` (imports `fetch_insider_transactions`/`fetch_calendar` from
`scripts.fetch_data` rather than duplicating the logic). Re-ran the full fetch and verified
124/126 tickers had insider data, 85/126 had an upcoming earnings date, before committing.

**Still open for task 9:** ask the user which path they want — (a) add a real lightweight
backend/auth service (their choice of provider, needs their credentials), or (b) skip
accounts entirely and lean further into localStorage-based "this browser only" persistence
patterns (already used for watchlist/theme), clearly labeled as such.

---

## Design roadmap — from Session 1, mostly still open

These are earlier next steps not yet done. Ordered by leverage.

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
- **GitHub's upload-file commit message box shifts position after a file finishes
  uploading/processing** — clicking a coordinate captured before the upload settled can
  land on the wrong element entirely (hit the global "/" search shortcut and a Copilot
  panel by accident this session). Always re-run `read_page` (or at least re-screenshot)
  *after* the upload confirms, and click the textbox by its fresh element ref, not a
  remembered coordinate.
- **Verify JS syntax before pushing hand-edited `<script>` blocks**: `node -e "new
  Function(...)"` against the extracted script content catches syntax errors for free
  without needing a browser. Used this before every push this session.
- When adding a new real data field to the pipeline, **re-run `scripts/fetch_data.py`
  locally and spot-check the output JSON** before committing — this is what caught that
  the P/E-etc. "estimated" labels were stale, and confirmed insider/calendar coverage
  numbers (124/126, 85/126) before claiming the feature worked.
