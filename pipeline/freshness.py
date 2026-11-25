"""Decide whether a refresh of the published data is due.

FinSTR refreshes hourly on US trading days: 10:30, 11:30, 12:30, 13:30, 14:30 and 15:30
New York time, plus 16:20 after the 16:00 close. GitHub's own scheduler often starts jobs hours
late, so the deploy workflow is triggered often (hourly cron plus an outside timer) and
this check makes the extra triggers cheap: a run only rebuilds the data when the live
site's snapshot is older than the most recent refresh slot.

Prints the decision and writes `due=true|false` to $GITHUB_OUTPUT. Stdlib only.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SLOTS = [(10, 30), (11, 30), (12, 30), (13, 30), (14, 30), (15, 30), (16, 20)]  # New York time, Mon-Fri


def latest_slot(now):
    """Most recent refresh slot at or before `now` (an aware datetime in ET)."""
    d = now.date()
    for _ in range(8):
        if d.weekday() < 5:
            for h, m in reversed(SLOTS):
                t = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=ET)
                if t <= now:
                    return t
        d -= dt.timedelta(days=1)
    return None


def live_generated_at(site):
    url = f"{site.rstrip('/')}/data/meta.json?t={int(time.time())}"  # skip the Pages CDN cache
    req = urllib.request.Request(url, headers={"User-Agent": "finstr-freshness"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["generatedAt"]


def main():
    force = os.environ.get("FINSTR_FORCE", "").lower() == "true"
    now = dt.datetime.now(ET)
    slot = latest_slot(now)
    if force:
        due, why = True, "forced (push or manual run)"
    else:
        try:
            gen = dt.datetime.fromtimestamp(live_generated_at(os.environ["FINSTR_SITE"]), ET)
        except Exception as e:  # unreadable live data: rebuild rather than leave it broken
            due, why = True, f"live data unreadable ({e})"
        else:
            due = slot is not None and gen < slot
            why = (f"live data from {gen:%a %d %b %H:%M} ET, latest slot {slot:%a %d %b %H:%M} ET: "
                   + ("refresh due" if due else "already fresh, skipping"))
    print(why)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"due={'true' if due else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
