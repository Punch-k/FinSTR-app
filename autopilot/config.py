"""Loads and checks rules.toml (standard-library TOML reader, Python 3.11+)."""

import os
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = os.path.join(HERE, "rules.example.toml")


class RulesError(ValueError):
    pass


def _merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load(path=None):
    with open(DEFAULTS, "rb") as f:
        rules = tomllib.load(f)
    if path:
        with open(path, "rb") as f:
            rules = _merge(rules, tomllib.load(f))
    check(rules)
    return rules


def check(r):
    core, sat, lim = r["core"], r["satellites"], r["limits"]
    errs = []
    for name, v in (("core.weight", core["weight"]), ("satellites.weight", sat["weight"]),
                    ("limits.cash_buffer", lim["cash_buffer"]), ("limits.max_turnover", lim["max_turnover"]),
                    ("satellites.max_position", sat["max_position"])):
        if not 0 <= v <= 1:
            errs.append(f"{name} must be between 0 and 1 (got {v})")
    total = core["weight"] + sat["weight"] + lim["cash_buffer"]
    if total > 1.0001:
        errs.append(f"core.weight + satellites.weight + limits.cash_buffer = {total:.2f}, more than 100%")
    if sat["count"] < 0 or int(sat["count"]) != sat["count"]:
        errs.append("satellites.count must be a whole number")
    if sat["count"] and sat["weight"] / sat["count"] > sat["max_position"] + 1e-9:
        errs.append("satellites.weight / satellites.count is above satellites.max_position; raise count or lower weight")
    if r["account"]["broker"] not in ("manual", "questrade", "ibkr"):
        errs.append("account.broker must be manual, questrade or ibkr")
    if r["report"]["llm"] not in ("none", "openai", "anthropic"):
        errs.append("report.llm must be none, openai or anthropic")
    if errs:
        raise RulesError("rules.toml: " + "; ".join(errs))
