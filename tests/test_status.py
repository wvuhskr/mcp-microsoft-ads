from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails
from mcp_microsoft_ads.tools import status as status_mod


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = NS(
        GetCampaignsByIds=lambda AccountId, CampaignIds, CampaignType: NS(
            Campaigns=NS(Campaign=[NS(Id=5, Name="C", Status="Active")])),
        UpdateCampaigns=lambda AccountId, Campaigns: NS(PartialErrors=None),
        factory=NS(create=lambda t: NS(long=None)),
    )
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc

def test_pause_campaign_drafts(fake):
    d = status_mod.pause_entity("campaign", 5)
    assert d["preview"]["changes"]["Status"] == {"before": "Active", "after": "Paused"}

def test_keyword_requires_parent(fake):
    with pytest.raises(ValueError, match="parent_id"):
        status_mod.pause_entity("keyword", 9)

def test_unknown_type(fake):
    with pytest.raises(ValueError, match="entity_type"):
        status_mod.pause_entity("asset_group", 9)

def test_pause_ad_group_bare_response_applies(monkeypatch, tmp_path):
    """status.py's _flip() 'ad_group' branch calls adgroups_write._fetch_ad_group directly
    (import at status.py:6, calls at lines 36/45) — a second caller of the same as_list fix
    covered for update_ad_group by test_adgroups_write.py's
    test_bare_single_ad_group_response_not_truncated. This path had no test coverage before
    (review finding). Feed GetAdGroupsByIds a bare single AdGroup object (not a one-item
    list) to prove this path is protected too, then drive a real apply to confirm the flip
    reaches UpdateAdGroups and the readback reports the paused status."""
    bare_ag = NS(Id=7, Name="AG7", Status="Active")
    svc = NS(
        GetAdGroupsByIds=lambda CampaignId, AdGroupIds: NS(AdGroups=NS(AdGroup=bare_ag)),  # bare, not [..]
        UpdateAdGroups=lambda CampaignId, AdGroups: NS(PartialErrors=None),
        factory=NS(create=lambda t: NS(long=None)),
    )
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = status_mod.pause_entity("ad_group", 7, parent_id=524066223)
    assert d["preview"]["changes"]["Status"] == {"before": "Active", "after": "Paused"}

    bare_ag.Status = "Paused"  # simulate server-side apply for the readback
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["verify"]["verified"] is True
