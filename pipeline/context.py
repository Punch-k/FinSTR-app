"""
Session timing, volume pace and "why it moved" context. Deterministic rules; no AI.

- session_pace(): share of a normal day's volume that has traded so far, so relative volume read mid-session
  compares like with like (a stock at 50% of its usual volume by noon is busy, not quiet).
- move_context(): for each stock that moved 3% or more, checks whether it reported earnings in the last
  trading day, how its industry moved, and the latest company headline. It describes; it does not claim a cause.
"""

import datetime as dt
import math
import re

# Approximate cumulative share of a US trading day's volume, by minutes after the 9:30 ET open
# (U-shaped intraday volume, closing auction included).
VOLUME_CURVE = [(0, 0.0), (15, 0.07), (30, 0.12), (60, 0.20), (90, 0.27), (120, 0.33), (180, 0.43),
                (240, 0.53), (300, 0.63), (330, 0.69), (360, 0.77), (375, 0.83), (390, 1.0)]
HEADLINE_HOURS = 36
MOVE_MIN = 3.0


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def et_now(now_utc=None):
    """Current time in New York (DST-aware, with a fallback when the tz database is missing)."""
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        y, naive = now_utc.year, now_utc.replace(tzinfo=None)
        mar8, nov1 = dt.datetime(y, 3, 8), dt.datetime(y, 11, 1)
        start = mar8 + dt.timedelta(days=(6 - mar8.weekday()) % 7, hours=7)   # 2nd Sunday of March, 2:00 EST
        end = nov1 + dt.timedelta(days=(6 - nov1.weekday()) % 7, hours=6)     # 1st Sunday of November, 2:00 EDT
        off = -4 if start <= naive < end else -5
        return now_utc.astimezone(dt.timezone(dt.timedelta(hours=off)))


def session_pace(last_bar_date, now=None):
    """1.0 unless last_bar_date is today's session still in progress; then the expected share traded so far."""
    now = now or et_now()
    if last_bar_date != now.date() or now.weekday() >= 5:
        return 1.0
    mins = now.hour * 60 + now.minute - (9 * 60 + 30)
    if mins >= 390:
        return 1.0
    mins = max(mins, 15)        # the first minutes are too noisy to scale
    for (m0, f0), (m1, f1) in zip(VOLUME_CURVE, VOLUME_CURVE[1:]):
        if m0 <= mins <= m1:
            return f0 + (f1 - f0) * (mins - m0) / (m1 - m0)
    return 1.0


NAME_DROP = {"inc", "corp", "corporation", "company", "co", "the", "holdings", "group", "plc", "ltd", "incorporated",
             "class", "a", "b", "c", "&", "of", "and"}
GENERIC = {"bank", "home", "general", "american", "america", "first", "united", "advanced", "international", "global",
           "national", "digital", "western", "southern", "northern", "energy", "financial", "technologies", "technology",
           "systems", "devices", "micro", "motor", "motors", "health", "healthcare", "services", "industries", "capital",
           "communications", "entertainment", "solutions", "brands", "resources", "partners", "trust", "insurance",
           "power", "electric", "pharmaceuticals", "therapeutics", "labs", "laboratories", "data", "software",
           "semiconductor", "networks", "platforms", "foods", "properties", "realty", "water", "gas", "oil", "airlines",
           "products", "enterprises", "natural", "materials", "chemical", "scientific", "medical", "instruments", "mobile",
           "walt", "street", "state", "new", "north", "south", "east", "west", "central", "pacific", "atlantic"}
ALIASES = {"GOOGL": "Google", "GOOG": "Google", "BRK-B": "Berkshire", "META": "Facebook"}
SPAM = re.compile(r"market (report|size|share|to reach|research)|cagr|forecast to 20\d\d|press release", re.I)


def _keywords(r):
    """Phrases that show a headline is about this company: full name, distinctive name words, ticker, alias."""
    raw = [w for w in re.sub(r"[(),.]", " ", r.get("name") or "").split()]
    suffix = {"inc", "corp", "corporation", "company", "co", "the", "holdings", "group", "plc", "ltd", "incorporated", "class", "a", "b", "c"}
    while raw and raw[-1].lower() in suffix:
        raw.pop()
    while raw and raw[0].lower() == "the":
        raw.pop(0)
    words = [w for w in raw if w.lower() not in NAME_DROP]
    keys = {" ".join(raw)} if raw else set()
    keys |= {w for w in words if len(w) >= 4 and w.lower() not in GENERIC}
    if r["ticker"] in ALIASES:
        keys.add(ALIASES[r["ticker"]])
    return keys, (r["ticker"] if len(r["ticker"]) > 1 else None)


def _about_company(title, r):
    """True when the first 50 characters of the headline name the company (and it is not market-report spam)."""
    keys, tick = _keywords(r)
    head = title[:50]
    if SPAM.search(title):
        return False
    if any(k and k.lower() in head.lower() for k in keys):
        return True
    return bool(tick and re.search(r"\b" + re.escape(tick) + r"\b", head))


def _fmt_day(d):
    return f"{d:%b} {d.day}"


def move_context(rows, as_of_ts, today, threshold=MOVE_MIN):
    """Sets row["why"] for every stock whose day change is at least `threshold` percent either way."""
    groups = {"industry": {}, "sector": {}}
    for r in rows:
        if _isnum(r.get("changePercent")) and _isnum(r.get("marketCap")) and r["marketCap"] > 0:
            for g in groups:
                groups[g].setdefault(r.get(g), []).append(r)

    def peer_move(g, r):
        peers = [x for x in groups[g].get(r.get(g), []) if x["ticker"] != r["ticker"]]
        w = sum(x["marketCap"] for x in peers)
        return (sum(x["changePercent"] * x["marketCap"] for x in peers) / w, len(peers)) if w else (None, 0)

    last_trading_gap = 3 if today.weekday() == 0 else 1       # Monday: Friday's report counts
    for r in rows:
        chg = r.get("changePercent")
        r.pop("why", None)
        if not _isnum(chg) or abs(chg) < threshold:
            continue
        level, (peer, n) = "industry", peer_move("industry", r)
        if n < 3:
            level, (peer, n) = "sector", peer_move("sector", r)
        group_name = r.get(level) or level

        parts, kind = [], "company"
        earnings = None
        try:
            ed = dt.date.fromisoformat(r.get("earningsDate") or "")
            if 0 <= (today - ed).days <= last_trading_gap:
                earnings = ed.isoformat()
                parts.append(f"Reported earnings on {_fmt_day(ed)}.")
                kind = "earnings"
        except ValueError:
            pass

        if peer is not None:
            same_way = (peer >= 0) == (chg >= 0)
            if same_way and abs(peer) >= 0.5 * abs(chg):
                parts.append(f"Moved with its {level}: {group_name} {peer:+.1f}% on average.")
                if kind != "earnings":
                    kind = "peers"
            elif not same_way and abs(peer) >= 0.5:
                parts.append(f"Moved against its {level} ({group_name} {peer:+.1f}% on average), so this looks company-specific.")
            elif abs(peer) < 0.05:
                parts.append(f"Its {level} was flat ({group_name}), so this looks company-specific.")
            else:
                parts.append(f"Its {level} moved {peer:+.1f}% ({group_name}), so this looks company-specific.")

        fresh = [a for a in (r.get("news") or []) if (a.get("ts") or 0) >= as_of_ts - HEADLINE_HOURS * 3600
                 and _about_company(a.get("title") or "", r)]
        headline = max(fresh, key=lambda a: a.get("ts") or 0) if fresh else None
        if headline is None and kind == "company":
            parts.append(f"No company headline in the last {HEADLINE_HOURS} hours.")
        r["why"] = {
            "kind": kind, "text": " ".join(parts),
            "peerGroup": group_name, "peerLevel": level, "peerChange": round(peer, 2) if peer is not None else None,
            "earnings": earnings,
            "headline": {k: headline.get(k) for k in ("title", "link", "source", "ts")} if headline else None,
        }
