"""Advertiser settings: one optional YAML file, MS_ADS_ADVERTISER_CONFIG-resolved,
strictly validated. See mcp_microsoft_ads/settings.py for the schema."""
import os
import re

import pytest

from mcp_microsoft_ads import settings

_DEFAULT_KEYWORD_RESEARCH = {
    "location_id": 190,
    "language": "English",
    "network": "OwnedAndOperatedAndSyndicatedSearch",
}


@pytest.fixture(autouse=True)
def reset_settings_cache():
    settings._reset()
    yield
    settings._reset()


def _write(tmp_path, content, name="advertiser.yaml"):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


# --- missing / empty file -> defaults ---------------------------------------

def test_missing_file_gives_defaults(tmp_path):
    path = str(tmp_path / "does-not-exist.yaml")
    result = settings.load(path)
    assert result == {
        "blocked_terms": [],
        "advertiser_domain": "",
        "keyword_research": _DEFAULT_KEYWORD_RESEARCH,
        "require_pmax_target_cpa": True,
    }


def test_empty_file_gives_defaults(tmp_path):
    path = _write(tmp_path, "")
    result = settings.load(path)
    assert result["blocked_terms"] == []
    assert result["require_pmax_target_cpa"] is True


# --- malformed root ----------------------------------------------------------

def test_malformed_root_not_a_mapping(tmp_path):
    path = _write(tmp_path, "- one\n- two\n")
    with pytest.raises(ValueError, match=re.escape(path)):
        settings.load(path)


def test_malformed_root_scalar(tmp_path):
    path = _write(tmp_path, "just a string\n")
    with pytest.raises(ValueError, match=re.escape(path)):
        settings.load(path)


# --- unknown keys -------------------------------------------------------------

def test_unknown_top_level_key_rejected(tmp_path):
    path = _write(tmp_path, "totally_unknown_key: 1\n")
    with pytest.raises(ValueError, match="totally_unknown_key"):
        settings.load(path)


def test_keyword_research_unknown_subkey_rejected(tmp_path):
    path = _write(tmp_path, "keyword_research:\n  bogus: 1\n")
    with pytest.raises(ValueError, match="bogus"):
        settings.load(path)


# --- blocked_terms -------------------------------------------------------------

def test_blocked_terms_must_be_list(tmp_path):
    path = _write(tmp_path, "blocked_terms: not-a-list\n")
    with pytest.raises(ValueError, match="blocked_terms"):
        settings.load(path)


def test_blocked_terms_rejects_blank_entry(tmp_path):
    path = _write(tmp_path, 'blocked_terms:\n  - sewage\n  - "   "\n')
    with pytest.raises(ValueError, match="blocked_terms"):
        settings.load(path)


def test_blocked_terms_rejects_non_string_entry(tmp_path):
    path = _write(tmp_path, "blocked_terms:\n  - 5\n")
    with pytest.raises(ValueError, match="blocked_terms"):
        settings.load(path)


# --- advertiser_domain ----------------------------------------------------------

def test_advertiser_domain_rejects_blank_string(tmp_path):
    path = _write(tmp_path, 'advertiser_domain: ""\n')
    with pytest.raises(ValueError, match="advertiser_domain"):
        settings.load(path)


def test_advertiser_domain_wrong_type(tmp_path):
    path = _write(tmp_path, "advertiser_domain: 123\n")
    with pytest.raises(ValueError, match="advertiser_domain"):
        settings.load(path)


# --- keyword_research: location_id / language / network ------------------------

def test_keyword_research_location_id_must_be_positive_int(tmp_path):
    path = _write(tmp_path, "keyword_research:\n  location_id: -5\n")
    with pytest.raises(ValueError, match="location_id"):
        settings.load(path)


def test_keyword_research_location_id_rejects_bool(tmp_path):
    path = _write(tmp_path, "keyword_research:\n  location_id: true\n")
    with pytest.raises(ValueError, match="location_id"):
        settings.load(path)


def test_keyword_research_language_must_be_non_empty_string(tmp_path):
    path = _write(tmp_path, 'keyword_research:\n  language: ""\n')
    with pytest.raises(ValueError, match="language"):
        settings.load(path)


def test_keyword_research_network_must_be_non_empty_string(tmp_path):
    path = _write(tmp_path, "keyword_research:\n  network: 7\n")
    with pytest.raises(ValueError, match="network"):
        settings.load(path)


def test_keyword_research_partial_override_keeps_other_defaults(tmp_path):
    path = _write(tmp_path, "keyword_research:\n  location_id: 200\n")
    result = settings.load(path)
    assert result["keyword_research"] == {
        "location_id": 200,
        "language": "English",
        "network": "OwnedAndOperatedAndSyndicatedSearch",
    }


# --- require_pmax_target_cpa ------------------------------------------------

def test_require_pmax_target_cpa_must_be_bool_not_string(tmp_path):
    path = _write(tmp_path, 'require_pmax_target_cpa: "true"\n')
    with pytest.raises(ValueError, match="require_pmax_target_cpa"):
        settings.load(path)


def test_require_pmax_target_cpa_optional_defaults_true(tmp_path):
    path = _write(tmp_path, "advertiser_domain: example.com\n")
    result = settings.load(path)
    assert result["require_pmax_target_cpa"] is True


def test_require_pmax_target_cpa_explicit_false(tmp_path):
    path = _write(tmp_path, "require_pmax_target_cpa: false\n")
    result = settings.load(path)
    assert result["require_pmax_target_cpa"] is False


# --- fully populated file -----------------------------------------------------

def test_populated_file_all_fields(tmp_path):
    path = _write(tmp_path, """
blocked_terms:
  - sewage
  - backup
advertiser_domain: example.com
keyword_research:
  location_id: 200
  language: Spanish
  network: OwnedAndOperatedOnly
require_pmax_target_cpa: false
""")
    result = settings.load(path)
    assert result == {
        "blocked_terms": ["sewage", "backup"],
        "advertiser_domain": "example.com",
        "keyword_research": {
            "location_id": 200,
            "language": "Spanish",
            "network": "OwnedAndOperatedOnly",
        },
        "require_pmax_target_cpa": False,
    }


# --- path resolution: ~ expansion, env-var precedence -------------------------

def test_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MS_ADS_ADVERTISER_CONFIG", raising=False)
    config_dir = tmp_path / ".mcp-microsoft-ads"
    config_dir.mkdir()
    (config_dir / "advertiser.yaml").write_text("advertiser_domain: fromtilde.com\n")
    result = settings.load()
    assert result["advertiser_domain"] == "fromtilde.com"


def test_env_var_used_when_no_explicit_path(tmp_path, monkeypatch):
    path = _write(tmp_path, "advertiser_domain: fromenv.com\n")
    monkeypatch.setenv("MS_ADS_ADVERTISER_CONFIG", path)
    result = settings.load()
    assert result["advertiser_domain"] == "fromenv.com"


def test_explicit_arg_takes_precedence_over_env_var(tmp_path, monkeypatch):
    env_path = _write(tmp_path, "advertiser_domain: fromenv.com\n", name="env.yaml")
    explicit_path = _write(tmp_path, "advertiser_domain: fromexplicit.com\n", name="explicit.yaml")
    monkeypatch.setenv("MS_ADS_ADVERTISER_CONFIG", env_path)
    result = settings.load(explicit_path)
    assert result["advertiser_domain"] == "fromexplicit.com"


# --- accessors ------------------------------------------------------------------

def test_accessors_lazy_load_when_cache_empty(tmp_path, monkeypatch):
    path = _write(tmp_path, "advertiser_domain: lazyload.com\nblocked_terms:\n  - foo\n")
    monkeypatch.setenv("MS_ADS_ADVERTISER_CONFIG", path)
    assert settings.blocked_terms() == ("foo",)
    assert settings.advertiser_domain() == "lazyload.com"
    assert settings.keyword_research() == _DEFAULT_KEYWORD_RESEARCH
    assert settings.require_pmax_target_cpa() is True


def test_blocked_terms_accessor_returns_tuple(tmp_path):
    path = _write(tmp_path, "blocked_terms:\n  - a\n  - b\n")
    settings.load(path)
    result = settings.blocked_terms()
    assert result == ("a", "b")
    assert isinstance(result, tuple)


def test_accessors_use_defaults_when_no_file_ever_loaded(monkeypatch, tmp_path):
    # no cache populated, no real file at the default path in this sandboxed HOME
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MS_ADS_ADVERTISER_CONFIG", raising=False)
    assert settings.advertiser_domain() == ""
    assert settings.blocked_terms() == ()
    assert settings.keyword_research() == _DEFAULT_KEYWORD_RESEARCH
    assert settings.require_pmax_target_cpa() is True


# --- config_path -----------------------------------------------------------------

def test_config_path_explicit_arg():
    assert settings.config_path("/tmp/some/explicit.yaml") == "/tmp/some/explicit.yaml"


def test_config_path_env_var(tmp_path, monkeypatch):
    path = _write(tmp_path, "advertiser_domain: x.com\n")
    monkeypatch.setenv("MS_ADS_ADVERTISER_CONFIG", path)
    assert settings.config_path() == path


def test_config_path_default_when_unset(monkeypatch):
    monkeypatch.delenv("MS_ADS_ADVERTISER_CONFIG", raising=False)
    assert settings.config_path() == os.path.expanduser(settings.DEFAULT_PATH)


def test_config_path_returns_resolved_path_even_when_file_absent(tmp_path):
    missing = str(tmp_path / "does-not-exist.yaml")
    assert settings.config_path(missing) == missing
