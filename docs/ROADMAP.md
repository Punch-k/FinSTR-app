# FinSTR Product Roadmap

**Positioning:** the free, zero-install S&P 500 research tool that explains itself and that AI agents can drive.
Finviz-style depth, built for phones, with automation (digests, alerts, MCP) that other free tools lock behind
paid tiers or developer setup.

## Where FinSTR stands against the alternatives

| | Finviz (free) | xang1234 stock-screener | OracleX | OpenBB (ODP) | **FinSTR** |
|---|---|---|---|---|---|
| Who it is for | traders | momentum traders | self-hosting power users | developers, quants | everyday investors and their AI assistants |
| Install / cost | none / free tier, Elite paid | none (static site) | Docker + keys | pip + Python; UI (Workspace) is commercial | **none, free** |
| Phone experience | no mobile site | desktop-first | desktop app | none | **mobile-first** |
| Explains moves and scores | no | scores ("Watch/Pass") | LLM news summaries | no | **rule-based "why it moved", signal reasons** |
| Proof the signal works | no | scoreboard | no | n/a | **backtest + live out-of-sample record** |
| Portfolio + scheduled digests | Elite | no | in-app | no | **email, Telegram, Slack, push; AI with fact checks** |
| Screen alerts | Elite | no | no | no | **yes (site + digest)** |
| AI-agent access | no | MCP server | in-app chat | MCP / REST | **MCP server + llms.txt + static JSON** |
| Markets | US | 12 countries | US, crypto, Turkey | many (bring keys) | US (S&P 500) |

## Principles

1. Every number is computed by code and published with its timestamp; AI only narrates, and its text is checked.
2. Describe, never claim a cause; mark what is provisional; publish results that look bad too.
3. Free and static first: anything that needs a server must have a free tier and a guest-mode fallback.
4. Phone first, then desktop density.

## Now (no outside accounts needed)

| # | Item | Why | Status |
|---|---|---|---|
| 1 | **MCP server** (`pipeline/mcp_server.py`): market overview, search, stock, screen, signal record, portfolio review | Any AI assistant can use FinSTR; matches OpenBB/xang1234 for agents while staying zero-install | built |
| 2 | **Plain-English screener**: "cheap profitable tech near its 52-week high" → FinSTR rules, shown as editable filters | Removes the learning curve of 100 filters; deterministic, no AI cost | built, then removed from the UI on request (2026-11-25); parser kept for shared links |
| 3 | **Home: followed-stock brief + market internals** (advancers/decliners, median vs cap-weighted move, move histogram that filters, day-vs-week divergence) | Daily reason to open the site; ideas from OracleX, computed from data already published | built |
| 4 | **Design identity pass**: terminal-dark default, one colour system, monospaced numbers, new icon | Stand apart from stock Material-UI dashboards | planned, preview first |
| 5 | **Automation recipes**: n8n / Zapier / GitHub Actions templates on top of the JSON, ntfy and webhooks | Turn data into workflows people already use | planned |
| 6 | **Second market pilot** (Canada or India: official listing + yfinance suffixes) | Test global reach before committing to it | planned |

## Needs the owner's accounts

| Item | Needs |
|---|---|
| Sign in with Google (+ email link), synced portfolios and screens | built (Supabase + Google OAuth) |
| Dependable 3-a-day refresh (GitHub cron runs hours late) | Supabase pg_cron + a GitHub token, see SCHEDULER.md |
| Hosted digests without forking | Supabase + Resend (email) + a verified sending domain |
| Search indexing of the 503 stock pages | Google Search Console |
| Launch (Show HN, Reddit, demo GIF) | Owner posts |

## Metrics

Weekly visitors and returning visitors (needs privacy-friendly analytics, owner's choice), GitHub stars and forks,
digest workflow runs in forks, MCP installs, live-record days and its excess return (reported either way).
