# My FinSTR Autopilot (private)

This private repo holds my rules, my holdings and the log of every monthly plan. The code lives in
[FinSTR-app](https://github.com/Punch-k/FinSTR-app) (`autopilot/`); this repo only calls it.

| File | What it is |
|---|---|
| `rules.toml` | My strategy: core fund, satellites, limits, TFSA room. Edit it any time. |
| `holdings.json` | My cash and positions, if `broker = "manual"` (update it after placing orders). |
| `ledger/plans/YYYY-MM.md` | Each month's plan as it was emailed, plus what was filled. |
| `ledger/log.jsonl` | One line per plan and check. |

Setup and secrets: see [docs/AUTOPILOT.md](https://github.com/Punch-k/FinSTR-app/blob/main/docs/AUTOPILOT.md).
Keep this repo **private**: it contains my account values.
