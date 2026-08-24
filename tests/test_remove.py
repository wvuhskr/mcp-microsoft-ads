from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails
from mcp_microsoft_ads.tools import remove


class FakeSvc:
    def __init__(self, campaign=None):
        self.campaign = campaign or NS(Id=5, Name="Old Campaign", Status="Paused")
        self.factory = NS(create=lambda t: NS(long=None))
        self.calls = {}

    def GetCampaignsByIds(self, AccountId, CampaignIds, CampaignType):
        return NS(Campaigns=NS(Campaign=[self.campaign]))

    def DeleteCampaigns(self, AccountId, CampaignIds):
        self.calls["DeleteCampaigns"] = (AccountId, CampaignIds)
        return NS(PartialErrors=None)

    def DeleteAdGroups(self, CampaignId, AdGroupIds):
        self.calls["DeleteAdGroups"] = (CampaignId, AdGroupIds)
        return NS(PartialErrors=None)

    def DeleteKeywords(self, AdGroupId, KeywordIds):
        self.calls["DeleteKeywords"] = (AdGroupId, KeywordIds)
        return NS(PartialErrors=None)

    def DeleteAds(self, AdGroupId, AdIds):
        self.calls["DeleteAds"] = (AdGroupId, AdIds)
        return NS(PartialErrors=None)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


def test_campaign_delete_warns(fake):
    d = remove.remove_entity("campaign", 5)
    assert "irreversible" in d["preview"]["warning"].lower()
    assert d["preview"]["name"] == "Old Campaign"
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["partial_errors"] == []
    assert fake.calls["DeleteCampaigns"][0] == 111222333


def test_parent_required(fake):
    with pytest.raises(ValueError, match="parent_id"):
        remove.remove_entity("keyword", 9)


def test_invalid_entity_type(fake):
    with pytest.raises(ValueError, match="entity_type"):
        remove.remove_entity("banana", 1)


@pytest.mark.parametrize("entity_type,method,parent_kwarg,id_kwarg", [
    ("ad_group", "DeleteAdGroups", "CampaignId", "AdGroupIds"),
    ("keyword", "DeleteKeywords", "AdGroupId", "KeywordIds"),
    ("ad", "DeleteAds", "AdGroupId", "AdIds"),
])
def test_child_entity_delete_happy_path(fake, entity_type, method, parent_kwarg, id_kwarg):
    d = remove.remove_entity(entity_type, 42, parent_id=777)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["partial_errors"] == []
    parent, ids = fake.calls[method]
    assert parent == 777
    assert ids.long == [42]


def test_bare_single_campaign_response_not_truncated(monkeypatch, tmp_path):
    """suds collapses a single-item Campaign array to a bare object, not a
    list — GetCampaignsByIds must be routed through client.as_list (deviation 3)."""
    svc = FakeSvc()
    svc.GetCampaignsByIds = lambda AccountId, CampaignIds, CampaignType: NS(
        Campaigns=NS(Campaign=NS(Id=5, Name="Solo Campaign", Status="Active")))  # bare, not [..]
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = remove.remove_entity("campaign", 5)
    assert d["preview"]["name"] == "Solo Campaign"
