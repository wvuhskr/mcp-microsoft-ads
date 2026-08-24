"""Advertiser settings: one optional YAML file, loaded once at server startup.

Path resolution: explicit arg -> MS_ADS_ADVERTISER_CONFIG env var -> default
~/.mcp-microsoft-ads/advertiser.yaml (~ expanded). Missing file, or a file that
yaml loads as None (empty), means all defaults. Any other shape problem is a
loud ValueError naming the offending key and the file path -- this fails at
server startup (see server.main), not mid-tool-call.

No consumer wiring here (rails/insight/audiences/pmax reads are a later task) --
this module only loads, validates, and exposes the settings.
"""
import os

import yaml

ENV_VAR = "MS_ADS_ADVERTISER_CONFIG"
DEFAULT_PATH = "~/.mcp-microsoft-ads/advertiser.yaml"

_KEYWORD_RESEARCH_DEFAULTS = {
    "location_id": 190,
    "language": "English",
    "network": "OwnedAndOperatedAndSyndicatedSearch",
}
_ALLOWED_KEYS = frozenset({"blocked_terms", "advertiser_domain", "keyword_research",
                           "require_pmax_target_cpa"})
_ALLOWED_KEYWORD_RESEARCH_KEYS = frozenset(_KEYWORD_RESEARCH_DEFAULTS)

_cache: dict | None = None


def _defaults() -> dict:
    return {
        "blocked_terms": [],
        "advertiser_domain": "",
        "keyword_research": dict(_KEYWORD_RESEARCH_DEFAULTS),
        "require_pmax_target_cpa": True,
    }


def _validate(raw: dict, path: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: root must be a mapping, got {type(raw).__name__}")
    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {sorted(unknown)}")

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


def blocked_terms() -> tuple[str, ...]:
    if _cache is None:
        load()
    return tuple(_cache["blocked_terms"])


def advertiser_domain() -> str:
    if _cache is None:
        load()
    return _cache["advertiser_domain"]


def keyword_research() -> dict:
    if _cache is None:
        load()
    return dict(_cache["keyword_research"])


def require_pmax_target_cpa() -> bool:
    if _cache is None:
        load()
    return _cache["require_pmax_target_cpa"]


def config_path(path: str | None = None) -> str:
    """Resolved settings file path (same resolution as load()) -- for error messages
    naming where to fix a setting, whether or not the file exists."""
    return os.path.expanduser(path or os.environ.get(ENV_VAR) or DEFAULT_PATH)
