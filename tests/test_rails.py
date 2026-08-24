import re

import pytest

from mcp_microsoft_ads import rails, settings


def test_budget_cap(monkeypatch):
    rails.check_budget(999)
    with pytest.raises(rails.RailViolation, match="exceeds cap"):
        rails.check_budget(1001)
    monkeypatch.setenv("MS_ADS_MAX_DAILY_BUDGET", "300")
    with pytest.raises(rails.RailViolation):
        rails.check_budget(301)

def test_bid_cap():
    rails.check_bid(50)
    with pytest.raises(rails.RailViolation):
        rails.check_bid(50.01)

# --- Numeric rails validation (plan item 9, Task C2) ---
# Every numeric env rail must parse to a finite positive float, fail closed at call
# time; check_budget/check_bid must also reject non-finite/non-positive/bool amounts
# before the cap comparison.

@pytest.mark.parametrize("env_var,accessor", [
    ("MS_ADS_MAX_DAILY_BUDGET", rails.max_daily_budget),
    ("MS_ADS_MAX_CPC", rails.max_cpc),
    ("MS_ADS_DRAFT_TTL_SECONDS", rails.draft_ttl_seconds),
])
@pytest.mark.parametrize("bad_value", ["abc", "nan", "inf", "-5", "0"])
def test_numeric_rail_rejects_bad_values(monkeypatch, env_var, accessor, bad_value):
    monkeypatch.setenv(env_var, bad_value)
    with pytest.raises(rails.RailViolation, match=env_var):
        accessor()


@pytest.mark.parametrize("env_var,accessor,default", [
    ("MS_ADS_MAX_DAILY_BUDGET", rails.max_daily_budget, 1000),
    ("MS_ADS_MAX_CPC", rails.max_cpc, 50),
    ("MS_ADS_DRAFT_TTL_SECONDS", rails.draft_ttl_seconds, 3600),
])
def test_numeric_rail_unset_returns_default(monkeypatch, env_var, accessor, default):
    monkeypatch.delenv(env_var, raising=False)
    assert accessor() == default


@pytest.mark.parametrize("env_var,accessor", [
    ("MS_ADS_MAX_DAILY_BUDGET", rails.max_daily_budget),
    ("MS_ADS_MAX_CPC", rails.max_cpc),
    ("MS_ADS_DRAFT_TTL_SECONDS", rails.draft_ttl_seconds),
])
def test_numeric_rail_valid_override_works(monkeypatch, env_var, accessor):
    monkeypatch.setenv(env_var, "42.5")
    assert accessor() == 42.5


@pytest.mark.parametrize("bad_amount", [float("nan"), float("inf"), -1, 0, True])
def test_check_budget_rejects_bad_amounts(bad_amount):
    with pytest.raises(rails.RailViolation):
        rails.check_budget(bad_amount)


@pytest.mark.parametrize("bad_amount", [float("nan"), float("inf"), -1, 0, True])
def test_check_bid_rejects_bad_amounts(bad_amount):
    with pytest.raises(rails.RailViolation):
        rails.check_bid(bad_amount)


def test_check_budget_valid_amount_under_cap_passes():
    rails.check_budget(999)


def test_check_bid_valid_amount_under_cap_passes():
    rails.check_bid(50)


def test_check_budget_over_cap_still_raises_existing_message():
    with pytest.raises(rails.RailViolation, match="exceeds cap"):
        rails.check_budget(1001)


def test_check_bid_over_cap_still_raises_existing_message():
    with pytest.raises(rails.RailViolation, match="exceeds cap"):
        rails.check_bid(50.01)

def test_blocklist_empty_by_default_blocks_nothing():
    # conftest's autouse clean_advertiser_settings fixture forces empty blocked_terms
    rails.check_content(["ac repair springfield", "sewage cleanup", "Sewer BACKUP help", None])


def test_blocklist_from_settings(tmp_path, monkeypatch):
    path = tmp_path / "advertiser.yaml"
    path.write_text("blocked_terms:\n  - sewage\n  - backup\n")
    monkeypatch.setenv(settings.ENV_VAR, str(path))
    settings._reset()
    rails.check_content(["ac repair springfield", None])
    with pytest.raises(rails.RailViolation, match="sewage"):
        rails.check_content(["sewage cleanup"])
    with pytest.raises(rails.RailViolation, match="backup"):
        rails.check_content(["Sewer BACKUP help"])


def test_blocklist_from_settings_case_insensitive_term(tmp_path, monkeypatch):
    """A configured term with capitals (as an advertiser would naturally type it in
    advertiser.yaml) must still match lowercase text -- check_content lowers the text
    side but the configured term must be lowered too, or a capitalized configured term
    silently never matches anything."""
    path = tmp_path / "advertiser.yaml"
    path.write_text("blocked_terms:\n  - Backup\n")
    monkeypatch.setenv(settings.ENV_VAR, str(path))
    settings._reset()
    with pytest.raises(rails.RailViolation, match="backup"):
        rails.check_content(["emergency backup generator"])


def test_blocklist_message_names_settings_path(tmp_path, monkeypatch):
    path = tmp_path / "advertiser.yaml"
    path.write_text("blocked_terms:\n  - foo\n")
    monkeypatch.setenv(settings.ENV_VAR, str(path))
    settings._reset()
    with pytest.raises(rails.RailViolation, match=re.escape(str(path))):
        rails.check_content(["foo bar"])

def test_smart_bidding_guard():
    rails.check_no_pct_adjustment("ManualCpc", {"device_bid_adjustment_pct": 20})
    rails.check_no_pct_adjustment("MaxConversions", {"device_bid_adjustment_pct": None})
    with pytest.raises(rails.RailViolation, match="Smart Bidding"):
        rails.check_no_pct_adjustment("MaxConversions", {"device_bid_adjustment_pct": 20})
    with pytest.raises(rails.RailViolation):
        rails.check_no_pct_adjustment("TargetCpa", {"audience_bid_adjustment_pct": -10})

def test_smart_bidding_guard_fails_closed_on_unknown_strategy():
    """Owner decision (Task 25e): live-demonstrated on add_audience_targeting against a
    campaign with zero ad groups — effective_strategy read back None and a pct adjustment
    would have been allowed through. Fail closed instead: block, and say why (distinct
    message from the known-Smart-Bidding case)."""
    with pytest.raises(rails.RailViolation, match="could not be determined"):
        rails.check_no_pct_adjustment(None, {"audience_bid_adjustment_pct": 20})
    with pytest.raises(rails.RailViolation, match="could not be determined"):
        rails.check_no_pct_adjustment("", {"device_bid_adjustment_pct": 5})
    # known-Smart-Bidding branch must still fire (and not be shadowed by the new branch)
    with pytest.raises(rails.RailViolation, match="Smart Bidding"):
        rails.check_no_pct_adjustment("MaxConversions", {"audience_bid_adjustment_pct": 20})
    # a recognized non-Smart strategy is still allowed through
    rails.check_no_pct_adjustment("ManualCpc", {"audience_bid_adjustment_pct": 20})
    # no adjustment present at all: unknown strategy alone must not raise
    rails.check_no_pct_adjustment(None, {"audience_bid_adjustment_pct": None})

def test_pct_adjustment_allowlist_rejects_unknown_non_smart_strategy():
    """Task C: allowlist inversion. A strategy that is neither known-Smart nor
    known-manual (e.g. TargetImpressionShare) must reject — this is the case the old
    blocklist let slip through as if manual. Must fail on the old SMART_BIDDING-only
    blocklist code."""
    with pytest.raises(rails.RailViolation, match="not a recognized manual"):
        rails.check_no_pct_adjustment("TargetImpressionShare", {"device_bid_adjustment_pct": 20})

def test_pct_adjustment_allowlist_permits_each_manual_bidding_member():
    for strategy in rails.MANUAL_BIDDING:
        rails.check_no_pct_adjustment(strategy, {"device_bid_adjustment_pct": 20})

def test_pct_adjustment_bounds():
    rails.check_no_pct_adjustment("ManualCpc", {"device_bid_adjustment_pct": -90})
    rails.check_no_pct_adjustment("ManualCpc", {"device_bid_adjustment_pct": 900})
    with pytest.raises(rails.RailViolation, match="device_bid_adjustment_pct"):
        rails.check_no_pct_adjustment("ManualCpc", {"device_bid_adjustment_pct": -91})
    with pytest.raises(rails.RailViolation, match="device_bid_adjustment_pct"):
        rails.check_no_pct_adjustment("ManualCpc", {"device_bid_adjustment_pct": 901})

def test_keyword_bid_strategy_guard():
    """Change 3's rails-level policy: pre-reject a keyword bid change under Smart Bidding
    (UpdateKeywords silently ignores Bid there — live-verified) and fail closed when the
    strategy can't be read, with messages that distinguish the two cases."""
    with pytest.raises(rails.RailViolation, match="Smart Bidding"):
        rails.check_keyword_bid_allowed("MaxConversions")
    with pytest.raises(rails.RailViolation, match="could not be determined"):
        rails.check_keyword_bid_allowed(None)
    with pytest.raises(rails.RailViolation, match="could not be determined"):
        rails.check_keyword_bid_allowed("")
    rails.check_keyword_bid_allowed("ManualCpc")  # recognized non-Smart: allowed

def test_keyword_bid_allowlist_rejects_unknown_non_smart_strategy():
    """Same allowlist inversion as check_no_pct_adjustment — must fail on old blocklist code."""
    with pytest.raises(rails.RailViolation, match="not a recognized manual"):
        rails.check_keyword_bid_allowed("TargetImpressionShare")

def test_keyword_bid_allowlist_permits_each_manual_bidding_member():
    for strategy in rails.MANUAL_BIDDING:
        rails.check_keyword_bid_allowed(strategy)

def test_draft_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    d = rails.create_draft("update_campaign", {"before": 150, "after": 200}, lambda: {"ok": 1})
    assert d["dry_run"] is True and d["preview"]["after"] == 200
    out = rails.apply_draft(d["draft_id"])
    assert out["applied"] is True and out["result"] == {"ok": 1}
    with pytest.raises(rails.RailViolation, match="unknown or already-applied"):
        rails.apply_draft(d["draft_id"])

def test_apply_fn_error_logged(tmp_path, monkeypatch):
    import json
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(audit_path))

    def failing_fn():
        raise ValueError("API error: connection timeout")

    d = rails.create_draft("update_campaign", {"before": 50, "after": 100}, failing_fn)
    draft_id = d["draft_id"]

    # apply_fn error should propagate
    with pytest.raises(ValueError, match="connection timeout"):
        rails.apply_draft(draft_id)

    # audit log should contain error entry
    logs = [json.loads(line) for line in audit_path.read_text().strip().split('\n')]
    error_logs = [log for log in logs if log.get("phase") == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["draft_id"] == draft_id
    assert "connection timeout" in error_logs[0]["error"]

    # draft is consumed (single-use), so retry raises "unknown or already-applied"
    with pytest.raises(rails.RailViolation, match="unknown or already-applied"):
        rails.apply_draft(draft_id)


def test_partial_write_error_logged_with_partial_and_reraised(tmp_path, monkeypatch):
    """Plan item 11: a PartialWriteError from apply_fn must audit-log its .partial dict
    (alongside the existing error string) and re-raise with .partial intact -- the
    landed step-1 state must not vanish just because a later step blew up."""
    import json
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(audit_path))

    partial = {"campaign_ids": [888], "asset_group_ids": []}

    def failing_fn():
        try:
            raise ValueError("SOAP fault: asset group add timed out")
        except ValueError as e:
            raise rails.PartialWriteError("asset group add failed", partial=partial) from e

    d = rails.create_draft("create_pmax_campaign", {"Name": "Test"}, failing_fn)
    draft_id = d["draft_id"]

    with pytest.raises(rails.PartialWriteError) as exc_info:
        rails.apply_draft(draft_id)
    assert exc_info.value.partial == partial
    assert "reconcile" in str(exc_info.value).lower()
    assert "888" in str(exc_info.value)

    logs = [json.loads(line) for line in audit_path.read_text().strip().split('\n')]
    error_logs = [log for log in logs if log.get("phase") == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["partial"] == partial
    assert "asset group add failed" in error_logs[0]["error"]

# --- Draft TTL (Task 2, owner decision 2026-07-31) ---
# test_draft_lifecycle above already exercises "a draft inside the TTL still applies"
# (it creates and immediately applies against the 3600s default) — not duplicated here.

def test_expired_draft_raises_distinct_message_and_is_consumed(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    d = rails.create_draft("update_campaign", {"before": 1, "after": 2}, lambda: {"ok": 1})
    draft_id = d["draft_id"]
    rails._DRAFTS[draft_id].created_at -= (rails.draft_ttl_seconds() + 1)

    with pytest.raises(rails.RailViolation, match="expired") as exc_info:
        rails.apply_draft(draft_id)
    assert "unknown or already-applied" not in str(exc_info.value)

    # gone afterward: not left sitting in _DRAFTS, and a second attempt gets the
    # genuinely-distinct "unknown" message rather than "expired" again
    assert draft_id not in rails._DRAFTS
    with pytest.raises(rails.RailViolation, match="unknown or already-applied"):
        rails.apply_draft(draft_id)

def test_draft_ttl_env_override_honoured(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("MS_ADS_DRAFT_TTL_SECONDS", "5")
    d = rails.create_draft("update_campaign", {"before": 1, "after": 2}, lambda: {"ok": 1})
    draft_id = d["draft_id"]
    # 10s old: within the 3600s default but past the overridden 5s TTL
    rails._DRAFTS[draft_id].created_at -= 10

    with pytest.raises(rails.RailViolation, match="TTL is 5s"):
        rails.apply_draft(draft_id)

def test_expired_drafts_pruned_from_module_dict(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    stale = rails.create_draft("update_campaign", {"a": 1}, lambda: {"ok": 1})
    rails._DRAFTS[stale["draft_id"]].created_at -= (rails.draft_ttl_seconds() + 1)

    # normal draft traffic (an unrelated create_draft) must sweep the stale entry
    fresh = rails.create_draft("update_campaign", {"a": 2}, lambda: {"ok": 2})
    assert stale["draft_id"] not in rails._DRAFTS

    rails.apply_draft(fresh["draft_id"])  # consume so nothing is left for other tests

# --- Audit-write failure must not change a landed write's outcome (Task 2, owner decision 2026-07-31) ---
# create_draft's own audit call (rails.py, pre-mutation) is untouched on purpose: it runs
# before any account mutation, so failing loudly there is defensible (nothing has been
# misreported yet) and the queued finding named only the after-apply case.

def test_apply_audit_failure_does_not_mask_landed_write(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    real_log_event = rails.audit.log_event

    def flaky(tool, phase, data, path=None):
        if phase == "apply":  # fail only the post-apply audit write, not create_draft's
            raise OSError("disk full")
        return real_log_event(tool, phase, data, path)

    monkeypatch.setattr(rails.audit, "log_event", flaky)

    d = rails.create_draft("update_campaign", {"before": 1, "after": 2}, lambda: {"ok": 1})
    out = rails.apply_draft(d["draft_id"])

    assert out["applied"] is True
    assert out["result"] == {"ok": 1}
    assert "disk full" in out["audit_error"]

def test_apply_fn_error_audit_failure_noted_on_stderr(tmp_path, monkeypatch, capsys):
    """Finding 1 fix (owner decision 2026-07-31): when apply_fn raises AND the error-phase audit
    write also fails, the audit failure must leave a trace instead of vanishing — a
    stderr notice (never stdout, which is the MCP JSON-RPC stream). The original
    apply_fn exception must still propagate with its own type and message, unchanged."""
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    real_log_event = rails.audit.log_event

    def flaky(tool, phase, data, path=None):
        if phase == "error":  # fail only the error-phase audit write
            raise OSError("audit disk full")
        return real_log_event(tool, phase, data, path)

    monkeypatch.setattr(rails.audit, "log_event", flaky)

    def failing_fn():
        raise ValueError("API error: connection timeout")

    d = rails.create_draft("update_campaign", {"before": 1, "after": 2}, failing_fn)
    with pytest.raises(ValueError) as exc_info:
        rails.apply_draft(d["draft_id"])
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == "API error: connection timeout"

    captured = capsys.readouterr()
    assert captured.out == ""  # must never land on stdout
    assert "audit disk full" in captured.err

# --- MS_ADS_ENABLE_WRITES write opt-in (Task C1, plan item 8) ---
# conftest's autouse _ms_ads_writes_enabled_by_default fixture sets this to "true" for
# every other test in the suite; these tests explicitly override it off/invalid.

def test_parse_bool_env_unset_returns_default(monkeypatch):
    monkeypatch.delenv("MS_ADS_SOME_FLAG", raising=False)
    assert rails.parse_bool_env("MS_ADS_SOME_FLAG", False) is False
    assert rails.parse_bool_env("MS_ADS_SOME_FLAG", True) is True

def test_parse_bool_env_true_tokens(monkeypatch):
    for token in ["true", "TRUE", "True", "1", "  true  ", "  1  "]:
        monkeypatch.setenv("MS_ADS_SOME_FLAG", token)
        assert rails.parse_bool_env("MS_ADS_SOME_FLAG") is True

def test_parse_bool_env_false_tokens(monkeypatch):
    for token in ["false", "FALSE", "False", "0", "  false  ", "  0  "]:
        monkeypatch.setenv("MS_ADS_SOME_FLAG", token)
        assert rails.parse_bool_env("MS_ADS_SOME_FLAG") is False

def test_parse_bool_env_invalid_token_fails_closed(monkeypatch):
    for bad in ["yes", "on", "", " ", "nope"]:
        monkeypatch.setenv("MS_ADS_SOME_FLAG", bad)
        with pytest.raises(rails.RailViolation, match="MS_ADS_SOME_FLAG"):
            rails.parse_bool_env("MS_ADS_SOME_FLAG")


def test_create_draft_refuses_when_writes_disabled(monkeypatch):
    before = set(rails._DRAFTS)
    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "false")
    with pytest.raises(rails.RailViolation, match="MS_ADS_ENABLE_WRITES"):
        rails.create_draft("update_campaign", {"before": 1, "after": 2}, lambda: {"ok": 1})
    # refusal happens before any draft/audit side effect -- no new draft was created
    assert set(rails._DRAFTS) == before


def test_create_draft_refuses_when_writes_unset(monkeypatch):
    monkeypatch.delenv("MS_ADS_ENABLE_WRITES", raising=False)
    with pytest.raises(rails.RailViolation, match="MS_ADS_ENABLE_WRITES"):
        rails.create_draft("update_campaign", {"before": 1, "after": 2}, lambda: {"ok": 1})


def test_create_draft_refuses_on_invalid_token(monkeypatch):
    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "yes")
    with pytest.raises(rails.RailViolation, match="true/false/1/0"):
        rails.create_draft("update_campaign", {"before": 1, "after": 2}, lambda: {"ok": 1})


def test_create_draft_and_apply_draft_work_when_writes_enabled_variants(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    for token in ["true", "TRUE", "1"]:
        monkeypatch.setenv("MS_ADS_ENABLE_WRITES", token)
        d = rails.create_draft("update_campaign", {"before": 1, "after": 2}, lambda: {"ok": 1})
        out = rails.apply_draft(d["draft_id"])
        assert out["applied"] is True


def test_apply_draft_refuses_when_writes_disabled_and_does_not_consume_draft(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "true")
    d = rails.create_draft("update_campaign", {"before": 1, "after": 2}, lambda: {"ok": 1})
    draft_id = d["draft_id"]

    # Flag flips off (or clears) between draft creation and apply -- belt-and-suspenders:
    # a draft made while enabled must not be applyable once writes are disabled.
    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "false")
    with pytest.raises(rails.RailViolation, match="MS_ADS_ENABLE_WRITES"):
        rails.apply_draft(draft_id)

    # draft must NOT be consumed by the refused attempt
    assert draft_id in rails._DRAFTS

    # re-enabling lets the same draft apply
    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "true")
    out = rails.apply_draft(draft_id)
    assert out["applied"] is True


def test_apply_draft_refuses_on_invalid_token_and_does_not_consume_draft(tmp_path, monkeypatch):
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "true")
    d = rails.create_draft("update_campaign", {"before": 1, "after": 2}, lambda: {"ok": 1})
    draft_id = d["draft_id"]

    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "on")
    with pytest.raises(rails.RailViolation, match="true/false/1/0"):
        rails.apply_draft(draft_id)
    assert draft_id in rails._DRAFTS

# --- apply_recommendation double-gate (Task C3, plan item 11b) ---
# MS_ADS_ALLOW_APPLY_RECOMMENDATION is a second, tool-specific flag on top of
# MS_ADS_ENABLE_WRITES (already true via the autouse fixture in every test below unless
# a test overrides it itself) -- apply_recommendation's monetary effect is unbounded, so
# the global writes flag alone is not enough.

def test_create_draft_refuses_apply_recommendation_when_flag_unset(monkeypatch):
    monkeypatch.delenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", raising=False)
    before = set(rails._DRAFTS)
    with pytest.raises(rails.RailViolation, match="MS_ADS_ALLOW_APPLY_RECOMMENDATION"):
        rails.create_draft("apply_recommendation", {"id": "r1"}, lambda: {"ok": 1})
    assert set(rails._DRAFTS) == before  # no side effect before refusal


def test_create_draft_other_tools_unaffected_by_recommendation_flag(monkeypatch):
    monkeypatch.delenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", raising=False)
    d = rails.create_draft("update_campaign", {"before": 1, "after": 2}, lambda: {"ok": 1})
    out = rails.apply_draft(d["draft_id"])
    assert out["applied"] is True


def test_apply_recommendation_flag_gates_draft_and_apply_independently(monkeypatch):
    monkeypatch.setenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", "true")
    d = rails.create_draft("apply_recommendation", {"id": "r1"}, lambda: {"ok": 1})
    draft_id = d["draft_id"]

    # flag flips off between draft creation and apply -- must refuse AND not consume
    monkeypatch.setenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", "false")
    with pytest.raises(rails.RailViolation, match="MS_ADS_ALLOW_APPLY_RECOMMENDATION"):
        rails.apply_draft(draft_id)
    assert draft_id in rails._DRAFTS

    # re-enabling lets the same draft apply
    monkeypatch.setenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", "true")
    out = rails.apply_draft(draft_id)
    assert out["applied"] is True


def test_apply_recommendation_flag_false_or_zero_refuses(monkeypatch):
    for token in ["false", "0"]:
        monkeypatch.setenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", token)
        with pytest.raises(rails.RailViolation, match="MS_ADS_ALLOW_APPLY_RECOMMENDATION"):
            rails.create_draft("apply_recommendation", {"id": "r1"}, lambda: {"ok": 1})


def test_apply_recommendation_flag_invalid_token_fails_closed_at_both_gates(monkeypatch):
    monkeypatch.setenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", "yes")
    with pytest.raises(rails.RailViolation, match="true/false/1/0"):
        rails.create_draft("apply_recommendation", {"id": "r1"}, lambda: {"ok": 1})

    # make a valid draft first (flag true), then flip to the invalid token for apply_draft
    monkeypatch.setenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", "true")
    d = rails.create_draft("apply_recommendation", {"id": "r1"}, lambda: {"ok": 1})
    draft_id = d["draft_id"]
    monkeypatch.setenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", "nope")
    with pytest.raises(rails.RailViolation, match="true/false/1/0"):
        rails.apply_draft(draft_id)
    assert draft_id in rails._DRAFTS


def test_dismiss_recommendation_unaffected_by_flag(monkeypatch):
    monkeypatch.delenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", raising=False)
    d = rails.create_draft("dismiss_recommendation", {"id": "r1"}, lambda: {"ok": 1})
    out = rails.apply_draft(d["draft_id"])
    assert out["applied"] is True
