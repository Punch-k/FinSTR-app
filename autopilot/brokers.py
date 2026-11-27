"""
Where Autopilot reads your account. None of these can place an order.

    ManualBroker     holdings.json in your private repo: {"cash": 412.5, "positions": {"VFV.TO": 10}}
    QuestradeBroker  Questrade's API with a personal (read-only) token: balances, positions, deposits, fills
    IBKR             not built yet; see docs/AUTOPILOT.md ("Moving to Interactive Brokers later")

Questrade tokens: you create a refresh token in Questrade (App Hub > Register a personal app) and save it as the
QUESTRADE_REFRESH_TOKEN secret in your private repo. Questrade replaces the refresh token every time it is used,
so the job writes the new one back to that secret (needs GH_SECRETS_TOKEN, a fine-grained token limited to your
private repo with "Secrets: read and write"). An unused token expires after a few days, so the workflow refreshes
it weekly.
"""

import datetime as dt
import json
import os
import subprocess
import urllib.parse
import urllib.request

UA = "FinSTR-autopilot/1.0 (+https://github.com/Punch-k/FinSTR-app)"


class Account:
    def __init__(self, cash, positions, currency="CAD", deposits_ytd=None, fills=None, source="manual"):
        self.cash = float(cash)
        self.positions = {k: float(v) for k, v in positions.items() if v}
        self.currency = currency
        self.deposits_ytd = deposits_ytd      # None when the source cannot tell
        self.fills = fills or []              # [{date, side, symbol, qty, price}]
        self.source = source


class ManualBroker:
    def __init__(self, path):
        self.path = path

    def account(self):
        with open(self.path, encoding="utf-8") as f:
            j = json.load(f)
        return Account(j.get("cash", 0), j.get("positions", {}), j.get("currency", "CAD"),
                       j.get("deposits_ytd"), j.get("fills"), source="holdings.json")


class QuestradeBroker:
    LOGIN = "https://login.questrade.com/oauth2/token"

    def __init__(self, refresh_token, account_type="TFSA", save_token=None):
        self.refresh_token = refresh_token
        self.account_type = account_type
        self.save_token = save_token or save_token_to_github
        self.api = self.access = None

    def _auth(self):
        q = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": self.refresh_token})
        with urllib.request.urlopen(urllib.request.Request(f"{self.LOGIN}?{q}", headers={"User-Agent": UA}), timeout=30) as r:
            j = json.loads(r.read().decode())
        self.api, self.access = j["api_server"].rstrip("/") + "/", j["access_token"]
        self.save_token(j["refresh_token"])   # the old one no longer works, so save the new one straight away

    def _get(self, path, **params):
        if not self.access:
            self._auth()
        url = self.api + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.access}", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    def _account_id(self):
        accts = self._get("v1/accounts")["accounts"]
        match = [a for a in accts if a.get("type") == self.account_type]
        if not match:
            raise SystemExit(f"Questrade: no {self.account_type} account on this login "
                             f"(found: {', '.join(a.get('type', '?') for a in accts) or 'none'})")
        return match[0]["number"]

    def account(self):
        n = self._account_id()
        pos = {p["symbol"]: p["openQuantity"] for p in self._get(f"v1/accounts/{n}/positions")["positions"] if p["openQuantity"]}
        bal = self._get(f"v1/accounts/{n}/balances")["perCurrencyBalances"]
        cad = next((b for b in bal if b["currency"] == "CAD"), {"cash": 0})
        # activities: Questrade allows 31 days per request, so walk the year month by month
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        start = dt.datetime(now.year, 1, 1, tzinfo=dt.timezone.utc)
        deposits, fills = 0.0, []
        while start < now:
            end = min(start + dt.timedelta(days=30), now)
            acts = self._get(f"v1/accounts/{n}/activities", startTime=start.isoformat(), endTime=end.isoformat())
            for a in acts.get("activities", []):
                kind = a.get("type", "")
                if kind == "Deposits":
                    deposits += float(a.get("netAmount") or 0)
                elif kind == "Withdrawals":
                    pass   # withdrawn room only comes back next January, so it is not added back here
                elif kind == "Trades" and a.get("symbol"):
                    q = float(a.get("quantity") or 0)
                    fills.append({"date": (a.get("tradeDate") or "")[:10], "side": "BUY" if q > 0 else "SELL",
                                  "symbol": a["symbol"], "qty": abs(q), "price": a.get("price")})
            start = end
        return Account(cad.get("cash", 0), pos, "CAD", round(deposits, 2), fills, source="Questrade (read-only)")


def save_token_to_github(token):
    """Write Questrade's new refresh token back to the private repo's secret (GitHub CLI on the runner).
    Off GitHub Actions it is written to .questrade_token instead (keep that file out of git)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        if not os.environ.get("GH_TOKEN"):
            raise SystemExit("Questrade issued a new refresh token but GH_SECRETS_TOKEN is not set, so it cannot be "
                             "saved and the next run would fail. Add that secret (see docs/AUTOPILOT.md).")
        subprocess.run(["gh", "secret", "set", "QUESTRADE_REFRESH_TOKEN", "--body", token,
                        "--repo", os.environ["GITHUB_REPOSITORY"]], check=True, capture_output=True)
    else:
        with open(".questrade_token", "w", encoding="utf-8") as f:
            f.write(token)


def from_rules(rules, holdings_path="holdings.json"):
    kind = rules["account"]["broker"]
    if kind == "questrade":
        tok = os.environ.get("QUESTRADE_REFRESH_TOKEN")
        if not tok and os.path.exists(".questrade_token"):
            tok = open(".questrade_token", encoding="utf-8").read().strip()
        if not tok:
            raise SystemExit("broker = questrade but QUESTRADE_REFRESH_TOKEN is not set")
        return QuestradeBroker(tok, rules["account"].get("type", "TFSA"))
    if kind == "ibkr":
        raise SystemExit("Interactive Brokers is not built yet: use broker = \"manual\" for now (docs/AUTOPILOT.md)")
    return ManualBroker(holdings_path)
