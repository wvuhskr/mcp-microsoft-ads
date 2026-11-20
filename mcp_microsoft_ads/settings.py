"""Advertiser settings: one optional YAML file, loaded once at server startup.

Path resolution: explicit arg -> MS_ADS_ADVERTISER_CONFIG env var -> default
~/.mcp-microsoft-ads/advertiser.yaml (~ expanded). Missing file, or a file that
yaml loads as None (empty), means all defaults. Any other shape problem is a
loud ValueError naming the offending key and the file path -- this fails at
server startup (see server.main), not mid-tool-call.

Agencies: an optional `accounts:` map keyed by account id gives one account its own
advertiser_domain, blocked_terms (ADDED to the top-level list, never replacing it),
require_pmax_target_cpa, keyword_research and spend limits (max_daily_budget, max_cpc).
Top-level values are the default for every account not listed. Which account's rules
apply is whichever account the current tool call runs in (client.account_id()); without
an accounts section the account is never consulted.
"""
import math
import os
import re
import sys

import yaml

from . import client

ENV_VAR = "MS_ADS_ADVERTISER_CONFIG"
DEFAULT_PATH = "~/.mcp-microsoft-ads/advertiser.yaml"

_KEYWORD_RESEARCH_DEFAULTS = {
    "location_id": 190,
    "language": "English",
    "network": "OwnedAndOperatedAndSyndicatedSearch",
}
_RULE_KEYS = frozenset({"blocked_terms", "advertiser_domain", "keyword_research",
                        "require_pmax_target_cpa"})
_CAP_KEYS = ("max_daily_budget", "max_cpc")
_ALLOWED_KEYS = _RULE_KEYS | {"accounts"}
_ALLOWED_ACCOUNT_KEYS = _RULE_KEYS | set(_CAP_KEYS)
_ACCOUNT_ID = re.compile(r"[1-9][0-9]*")
_ALLOWED_KEYWORD_RESEARCH_KEYS = frozenset(_KEYWORD_RESEARCH_DEFAULTS)

_cache: dict | None = None


def _defaults() -> dict:
    return {
        "blocked_terms": [],
        "advertiser_domain": "",
        "keyword_research": dict(_KEYWORD_RESEARCH_DEFAULTS),
        "require_pmax_target_cpa": True,
        "accounts": {},
    }


def _validate_accounts(accounts, path: str) -> dict:
    if not isinstance(accounts, dict):
        raise ValueError(f"{path}: 'accounts' must be a mapping of account id to rules")
    out = {}
    for key, rules in accounts.items():
        aid = str(key).strip()
        if isinstance(key, bool) or not isinstance(key, (int, str)) or not _ACCOUNT_ID.fullmatch(aid):
            raise ValueError(
                f"{path}: accounts key {key!r} must be a Microsoft Ads account id (digits only)")
        if aid in out:
            raise ValueError(f"{path}: account {aid} is listed more than once in 'accounts'")
        where = f"{path}: accounts[{aid}]"
        if not isinstance(rules, dict):
            raise ValueError(f"{where} must be a mapping")
        unknown = set(rules) - _ALLOWED_ACCOUNT_KEYS
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {sorted(unknown, key=str)}")
        checked = _validate({k: v for k, v in rules.items() if k in _RULE_KEYS}, where,
                            allowed=_RULE_KEYS)
        entry = {k: checked[k] for k in _RULE_KEYS if k in rules}
        if "keyword_research" in rules:
            # Keep only what this account actually set; the rest comes from the top level.
            entry["keyword_research"] = {k: checked["keyword_research"][k]
                                         for k in rules["keyword_research"]}
        if any(cap in rules for cap in _CAP_KEYS) and not all(cap in rules for cap in _CAP_KEYS):
            # A missing limit would silently come from the environment number, which is
            # written for some other account's currency.
            raise ValueError(
                f"{where}: an account with its own limits must set max_daily_budget and "
                "max_cpc together")
        for cap in _CAP_KEYS:
            if cap in rules:
                value = rules[cap]
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or value <= 0):
                    raise ValueError(f"{where}: '{cap}' must be a positive number")
                entry[cap] = value
        out[aid] = entry
    return out


def _validate(raw: dict, path: str, allowed=_ALLOWED_KEYS) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: root must be a mapping, got {type(raw).__name__}")
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {sorted(unknown, key=str)}")

    result = _defaults()

    if "blocked_terms" in raw:
        terms = raw["blocked_terms"]
        if not isinstance(terms, list):
            raise ValueError(f"{path}: 'blocked_terms' must be a list")
        cleaned = []
        for term in terms:
            if not isinstance(term, str) or not term.strip():
                raise ValueError(f"{path}: 'blocked_terms' entries must be non-empty strings")
            # rails.check_content lowers only the text side of the comparison; a
            # configured term must be lowered here or a capitalized entry (as an
            # advertiser would naturally type "Backup" in advertiser.yaml) never matches.
            cleaned.append(term.strip().lower())
        result["blocked_terms"] = cleaned

    if "advertiser_domain" in raw:
        domain = raw["advertiser_domain"]
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError(f"{path}: 'advertiser_domain' must be a non-empty string")
        result["advertiser_domain"] = domain

    if "keyword_research" in raw:
        kr = raw["keyword_research"]
        if not isinstance(kr, dict):
            raise ValueError(f"{path}: 'keyword_research' must be a mapping")
        unknown_kr = set(kr) - _ALLOWED_KEYWORD_RESEARCH_KEYS
        if unknown_kr:
            raise ValueError(f"{path}: unknown key(s) in 'keyword_research': {sorted(unknown_kr)}")

        if "location_id" in kr:
            location_id = kr["location_id"]
            if isinstance(location_id, bool) or not isinstance(location_id, int) or location_id <= 0:
                raise ValueError(f"{path}: 'keyword_research.location_id' must be a positive int")
            result["keyword_research"]["location_id"] = location_id

        if "language" in kr:
            language = kr["language"]
            if not isinstance(language, str) or not language.strip():
                raise ValueError(f"{path}: 'keyword_research.language' must be a non-empty string")
            result["keyword_research"]["language"] = language

        if "network" in kr:
            network = kr["network"]
            if not isinstance(network, str) or not network.strip():
                raise ValueError(f"{path}: 'keyword_research.network' must be a non-empty string")
            result["keyword_research"]["network"] = network

    if "require_pmax_target_cpa" in raw:
        require_tcpa = raw["require_pmax_target_cpa"]
        if not isinstance(require_tcpa, bool):
            raise ValueError(f"{path}: 'require_pmax_target_cpa' must be a boolean")
        result["require_pmax_target_cpa"] = require_tcpa

    if "accounts" in raw:
        result["accounts"] = _validate_accounts(raw["accounts"], path)

    return result


def load(path: str | None = None) -> dict:
    """Resolve, parse, validate, cache, and return the advertiser settings dict.

    Always re-reads from disk (no short-circuit on an existing cache) so tests
    and repeated startups see the current file; see _reset() for full cache
    isolation between tests."""
    global _cache
    resolved = path or os.environ.get(ENV_VAR) or DEFAULT_PATH
    full_path = os.path.expanduser(resolved)

    try:
        with open(full_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raw = None

    _cache = _defaults() if raw is None else _validate(raw, full_path)
    return _cache


def _reset() -> None:
    """Test-only: clear the cache so tests don't leak state into each other."""
    global _cache
    _cache = None


def _rules() -> tuple[dict, dict]:
    """(top-level defaults, overrides for the account this tool call runs in). The
    account is only looked up when the file has an accounts section."""
    if _cache is None:
        load()
    accounts = _cache["accounts"]
    if not accounts:
        return _cache, {}
    return _cache, accounts.get(str(client.account_id()), {})


def blocked_terms() -> tuple[str, ...]:
    base, own = _rules()
    return tuple(dict.fromkeys([*base["blocked_terms"], *own.get("blocked_terms", [])]))


def advertiser_domain() -> str:
    base, own = _rules()
    return own.get("advertiser_domain", base["advertiser_domain"])


def keyword_research() -> dict:
    base, own = _rules()
    return {**base["keyword_research"], **own.get("keyword_research", {})}


def require_pmax_target_cpa() -> bool:
    base, own = _rules()
    return own.get("require_pmax_target_cpa", base["require_pmax_target_cpa"])


def cap(name: str):
    """This account's own limit (max_daily_budget | max_cpc), or None when it has none
    and the environment number applies. An account with its own limits always has both
    (enforced at load), so one is never mixed with an environment number meant for
    another currency."""
    return _rules()[1].get(name)


def caps_source() -> str:
    if any(name in _rules()[1] for name in _CAP_KEYS):
        return f"advertiser.yaml accounts section for {client.account_id()}"
    return "environment defaults"


def announce_account_caps() -> None:
    """Startup notice on stderr (stdout is the MCP channel): which accounts carry their
    own spend limits. Those REPLACE the environment limits for that account and may be
    higher, since they are in the account's own currency."""
    for aid, own in sorted((_cache or {}).get("accounts", {}).items()):
        caps = ", ".join(f"{name}={own[name]}" for name in _CAP_KEYS if name in own)
        if caps:
            print(f"mcp-microsoft-ads: account {aid} uses its own spend limits ({caps}); "
                  "these replace the environment limits for that account.", file=sys.stderr)


def config_path(path: str | None = None) -> str:
    """Resolved settings file path (same resolution as load()) -- for error messages
    naming where to fix a setting, whether or not the file exists."""
    return os.path.expanduser(path or os.environ.get(ENV_VAR) or DEFAULT_PATH)
