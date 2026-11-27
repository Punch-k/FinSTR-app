# FinSTR Autopilot: a monthly, rules-based investing workflow

Autopilot turns FinSTR's research into **one plan a month** for a long-term account (built around a Canadian TFSA,
works for any account): which stocks to keep, buy or sell, how many shares, and why, all within limits you set.
You read the plan and place the orders yourself; Autopilot logs what you did.

> **It cannot trade.** The Questrade link is read-only and there is no order code in this project. It is not
> investment advice: you write the rules, Autopilot applies them the same way every month and shows its working.

## How a month works

```
every hour (FinSTR site)    prices, analyst ratings, insider trades, headlines for the S&P 500
every day  (FinSTR site)    headline tone per stock and for the market       → data/sentiment.json
1st trading day, 9 AM ET    YOUR private repo runs `autopilot plan`:
                              read your account (Questrade read-only, or holdings.json)
                              apply rules.toml → whole-share orders with a reason each
                              optional AI paragraph on market mood, fact-checked against the plan
                              email / Telegram / phone push  +  ledger/plans/YYYY-MM.md
you, ~5 minutes             place the orders in your broker's app (limit orders near the prices shown)
every weekday evening       `autopilot check`: which orders were filled, deposits so far this year
```

## The rules (rules.toml)

| Rule | Example | What it does |
|---|---|---|
| `core.symbol`, `core.weight` | a broad index fund, 60% | The part never sold because of news |
| `satellites.weight`, `count` | 35% in up to 5 stocks | Individual stocks picked from FinSTR data |
| `min_signal` | 70 | FinSTR Signal (0-100) needed to buy |
| `exit_signal_below` | 45 | Sell a satellite whose signal falls below this |
| `min_sentiment` | -0.10 | Skip stocks whose 30-day headline tone is worse than this |
| `exit_after_negative_months` | 2 | Sell after this many months of negative headline tone |
| `max_per_sector`, `max_position` | 2, 10% | Concentration limits |
| `max_turnover` | 20% | Most of the account sold in one month (new deposits are extra) |
| `cash_buffer`, `min_order`, `fee_per_order` | 3%, $50, $0 | Housekeeping |
| `cad_only`, `trade_as = "cdr"` | true | Buy US companies through Canadian Depositary Receipts (`TICKER.NE`) to avoid currency fees |
| `tfsa_room`, `monthly_contribution` | 20000, 400 | Warns before a deposit would go over your TFSA room |

The numbers in `rules.example.toml` are examples of the format, not recommendations.

**How satellites are ranked:** FinSTR Signal + 20 × 30-day headline tone + 4 × (3 − analyst rating), among stocks that
pass the rules and actually trade in your currency. Empty satellite slots go to the core fund, so a month with nothing
worth buying simply adds to the core.

**Headline tone** (`autopilot/sentiment.py`) uses a finance word list on every company headline FinSTR collects: no
model download, the same answer every run, and each score can be traced to the words that produced it. Tone from few
headlines is pulled toward zero so one cheerful article cannot outrank a month of coverage. It describes the news; it
does not predict prices.

## The backtest (published on the site, rebuilt hourly)

`autopilot/backtest.py` runs **the same engine** at every month-end of the last ~4 years (the first year of FinSTR's
5-year history is needed to compute momentum), deposits the monthly contribution, buys whole shares, and compares the
result with putting the same deposits into the core fund alone. Results: [`data/autopilot.json`](https://punch-k.github.io/FinSTR-app/data/autopilot.json)
and the **Autopilot** page on the site. Test your own rules with `python -m autopilot backtest --rules rules.toml`.

What it cannot test, stated plainly:
- The signal is FinSTR's **price-only** composite (trend, momentum, low risk). Value, quality, growth, analyst ratings
  and headline tone have no free point-in-time history, so the live plan uses more information than the test.
- Today's S&P 500 members are used throughout (**survivorship bias** flatters stock picking).
- US-dollar prices with dividends reinvested; no currency effects or CDR pricing differences.

## Set it up (about 20 minutes)

1. **Create a private repo** (for example `my-autopilot`) and copy everything in
   [`autopilot/template/`](../autopilot/template) into it, including the hidden `.github` folder.
2. **Edit `rules.toml`:** your core fund, limits, `tfsa_room` (CRA My Account) and monthly deposit.
3. **Choose how Autopilot reads your account:**
   - `broker = "manual"`: keep `holdings.json` up to date after you place orders (cash, positions, deposits this year).
   - `broker = "questrade"`: in Questrade, *App Hub → Register a personal app*, generate a token, and save it as the
     repo secret `QUESTRADE_REFRESH_TOKEN`. Questrade replaces that token every time it is used, so also create a
     fine-grained GitHub token limited to your private repo with **Secrets: read and write**, saved as
     `GH_SECRETS_TOKEN`; the job uses it only to store the new Questrade token. The weekday check keeps the token
     from expiring; if it ever does, generate a new one in Questrade.
4. **Choose where the plan goes** (repo secrets, same as the FinSTR digest): `RESEND_API_KEY` + `MAIL_TO`, or
   `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_TO` (a Gmail app password works), or `TELEGRAM_BOT_TOKEN` +
   `TELEGRAM_CHAT_ID`, or `NTFY_TOPIC` for phone push. The plan is also always saved in `ledger/plans/`.
5. **Optional AI paragraph:** set `[report] llm = "openai"` (any OpenAI-compatible server: OpenAI, Ollama, LM Studio)
   or `"anthropic"`, plus secrets `LLM_BASE_URL` / `LLM_API_KEY` and variable `LLM_MODEL`. The model only describes the
   market mood; if it writes a number or ticker that is not in the plan, the plain version is sent instead.
6. **Try it:** Actions → FinSTR Autopilot → Run workflow → `plan`. Then let the schedule take over.

Run it on your own computer instead: `pip install yfinance pandas`, then from the FinSTR folder
`python -m autopilot plan --rules path/to/rules.toml --ledger path/to/ledger --holdings path/to/holdings.json`.

## Privacy

Your rules, holdings and plans live only in your private repo and its encrypted secrets. Autopilot reads FinSTR's
public data and Yahoo quotes; it sends your plan only to the channels you configure.

## TFSA notes (Canada)

- Gains, dividends and trades inside a TFSA are tax-free and not reported on your return; what matters is staying
  within your contribution room. Autopilot subtracts this year's deposits from `tfsa_room` and warns before a
  deposit would go over (the penalty is 1% of the excess per month).
- Withdrawals give the room back only on 1 January of the next year.
- US dividends lose 15% to US withholding inside a TFSA.
- Very frequent trading can be treated as business income; a monthly plan with a turnover limit is far from that.

## Moving to Interactive Brokers later

The broker is one small class in `autopilot/brokers.py`. An Interactive Brokers version would read positions the same
way and, only after you approve a plan, send its orders through IB Gateway running on a machine you control. It is not
built yet: until it is, Autopilot stays read-only.
