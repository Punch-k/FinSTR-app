# FinSTR — Code Quality Report

**Repo:** `Punch-k/FinSTR-app` (live at https://punch-k.github.io/FinSTR-app/)
**Reviewed at commit:** `ab3fbb6`
**Scope:** `app.py` (2,177 lines), `index.html` (3,180 lines), `scripts/`, `templates/`, `static/`, data pipeline, GitHub Actions workflow.

---

## Summary

FinSTR is a two-part Flask application: a stock screener (with a static-data fallback for GitHub Pages) and "MyShare," a portfolio/lot-tracking manager. The data pipeline and screener frontend are in solid shape — real data, no fabricated fields, sensible fallback chain, validated before each push. The MyShare backend (`app.py`) is functionally complete but has real gaps for anything beyond a local demo: no session-based auth, no rate limiting, no automated tests, and a monolithic single file. None of these are "broken" — the app works as designed for local/demo use — but they're the gap between "personal project" and "production-ready."

---

## Strengths

- **SQL injection–safe.** Every query goes through `executeDatabaseQuery`/`executeDatabaseUpdate`, which use parameterized `?` placeholders throughout. Some statements build column lists dynamically via f-strings (e.g. `app.py:639`), but only from fixed, hardcoded column-name literals — user input never reaches the SQL string itself, only the parameter tuple. No injection risk found.
- **Passwords are hashed**, not stored in plaintext (`passlib.hash.sha256_crypt`), and never logged or returned in API responses.
- **Consistent input validation.** Every endpoint validates required params and formats (`checkEmailFormat`, `checkPasswordFormat`, etc.) before touching the database, with consistent error codes/messages.
- **`.env` handling is correct** — real credentials are gitignored, only `.env.example` with placeholders is tracked; verified no secrets in git history.
- **Data pipeline is honest.** `scripts/fetch_data.py` pulls real fundamentals via `yfinance`, ticker lists are deduplicated into one shared module (`scripts/tickers.py`) instead of drifting across two copies, and the frontend's `estTag()` correctly distinguishes real fetched data from the synthetic offline fallback (`hasRealFundamentals` flag) rather than mislabeling everything as "estimated."
- **CI data refresh works.** `.github/workflows/update-data.yml` runs a real script on a schedule and commits results — not a stub.

---

## Issues, ranked by severity

### 1. No session/token-based authentication (High)
Every MyShare API call — `GET /myshare/user`, `Holdings`, `Lots`, `SellLots`, etc. — re-validates the user by accepting `id` and `password` as request parameters on *every single call* (`app.py:462` on) and checking them against the DB hash each time. There is no login endpoint that issues a session cookie or token; the client is expected to keep resending the raw password.
- **Risk:** if any of these are ever called via `GET` (Flask-RESTful's `reqparse` reads from query string), the password ends up in the URL — logged by the web server, visible in browser history, and leaked via the `Referer` header to any third-party resource the page loads.
- **Fix:** issue a short-lived session token (Flask's `session` + `SECRET_KEY`, or a JWT) at login, and require it — not the password — on subsequent requests.

### 2. No rate limiting on login/register (Medium-High)
`User.get()` (login) and `User.post()` (register) have no throttling. Combined with issue #1, this makes offline password-hash cracking less relevant but online brute-force guessing straightforward — nothing currently slows down repeated login attempts.
- **Fix:** add `Flask-Limiter` (or equivalent) on the auth endpoints at minimum.

### 3. No automated tests (Medium)
No `tests/` directory, no test files anywhere in the repo. All validation before pushing is manual (per `knowledge.md`: "run fetch script and spot-check output locally"). This works but doesn't scale — a future change to `app.py`'s ~30 endpoints has no safety net, and regressions would only surface live.
- **Fix:** even a lightweight `pytest` suite covering the format validators (`checkEmailFormat` etc.) and the CRUD endpoints against a throwaway SQLite DB would catch most regressions cheaply.

### 4. `app.py` is a 2,177-line monolith (Medium)
Everything — auth, holdings, lots, sell-lots, password reset, page-serving routes — lives in one file as ~20 `Resource` classes. No blueprints, no separation between the screener API and the MyShare API.
- **Fix:** not urgent functionally, but splitting into blueprints (`screener.py`, `myshare_auth.py`, `myshare_holdings.py`, etc.) would make the size manageable and reduce merge-conflict surface as it grows.

### 5. Four bare `except:` clauses (Low-Medium)
Bare excepts swallow all exceptions indiscriminately (including `KeyboardInterrupt`/`SystemExit` in edge cases), making failures silent and hard to debug.
- **Fix:** narrow to the specific exception type expected at each site (e.g. `except sqlite3.Error:`, `except (KeyError, ValueError):`).

### 6. No CSRF protection on state-changing endpoints (Low-Medium, MyShare-local-only)
POST/PATCH/DELETE endpoints don't set or check a CSRF token. Lower severity today since MyShare only runs locally (not reachable from the hosted GitHub Pages demo), but relevant before this is ever deployed as a real multi-user service.

### 7. `requirements.txt` mostly unpinned (Low)
Only `yfinance` has a version floor (`>=1.5.1`, intentionally, per the documented 429-error incident). `flask`, `flask-restful`, `passlib`, `lxml`, `yahoo_fin`, `yagmail` have no version constraints at all — a future `pip install` could pull a breaking major version with no warning.
- **Fix:** pin at least major versions (`flask>=3,<4`) for reproducible installs.

### 8. `index.html` is a single 3,180-line file (Low)
All markup, ~600 lines of CSS, and the entire JS application logic live inline in one HTML file. Fine for a GitHub Pages static deploy (no build step needed), but there's no minification/bundling, and any JS syntax error requires manually extracting the script block to catch (which is exactly what the documented workflow already does by hand before every push — a sign this is a known friction point, not a hypothetical one).
- **Fix:** optional — could split into `style.css`/`app.js` + a lightweight build/concat step, trading GitHub Pages simplicity for maintainability. Not necessary if the file stays this size, but worth revisiting if it keeps growing.

### 9. Known, self-documented gap: SEED_DATA fallback (Low, already tracked)
`index.html`'s last-resort offline fallback (278 tickers) still contains synthetically generated valuation fields dressed to look real. Low risk in practice (only reached if both the live API and the GitHub Actions JSON snapshot fail), and already flagged as open work in `knowledge.md` — noting here for completeness, not as a new finding.

---

## Not evaluated

- `templates/*.html` (Jinja2) were spot-checked for `|safe` filters (none found — autoescaping is intact) but not reviewed line-by-line.
- `static/js/` and `static/css/` for the MyShare portfolio UI were not reviewed in this pass.
- No load/performance testing was done against the `yfinance`-backed endpoints.

---

## Bottom line for the consultant

The codebase is competently written for what it is — a solo-built demo project with real data, no shortcuts on SQL safety or password hashing, and a genuinely automated data pipeline. The gaps are exactly the ones expected at this stage: no auth session model, no tests, no rate limiting, one large file. None block the current GitHub Pages demo use case. They would need to be addressed before this handles real user accounts/money-adjacent data (portfolio tracking) at any scale beyond local single-user use.
