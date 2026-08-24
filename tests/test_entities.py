from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client
from mcp_microsoft_ads.tools import entities

CAMP = NS(Id=524066222, Name="Plumbing - Search - MS", Status="Active",
          DailyBudget=150.0, CampaignType="Search", BiddingScheme=None)
AG = NS(Id=111, Name="Leak Detection", Status="Active",
        BiddingScheme=NS(Type="InheritFromParent", InheritedBidStrategyType="MaxConversions"),
        CpcBid=NS(Amount=20.0))

def fake_campaign_svc(name):
    assert name == "CampaignManagementService"
    return NS(
        GetCampaignsByAccountId=lambda AccountId, CampaignType: NS(Campaign=[CAMP]),
        GetAdGroupsByCampaignId=lambda CampaignId: NS(AdGroup=[AG]),
        GetKeywordsByAdGroupId=lambda AdGroupId: NS(Keyword=[NS(Id=9, Text="leak detection", Status="Active")]),
        GetAdsByAdGroupId=lambda AdGroupId, AdTypes: NS(Ad=[NS(Id=7, Type="ResponsiveSearchAd", Status="Active")]),
        GetAssetGroupsByCampaignId=lambda CampaignId: NS(AssetGroups=NS(AssetGroup=[NS(Id=3, Name="AG1", Status="Active")])),
        GetCampaignCriterionsByIds=lambda CampaignId, CampaignCriterionIds, CriterionType: NS(
            CampaignCriterions=NS(CampaignCriterion=[NS(Id=55, Criterion=NS(Type="DayTime", Day="Monday"))])),
        factory=NS(create=lambda t: NS(string=None, long=None)),
    )

@pytest.fixture(autouse=True)
def patch(monkeypatch):
    monkeypatch.setattr(client, "svc", fake_campaign_svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)

def test_campaigns():
    out = entities.get_entities("campaigns")
    assert out["entities"][0]["Name"] == "Plumbing - Search - MS"

def test_ad_groups_effective_strategy():
    out = entities.get_entities("ad_groups", parent_id=524066222)
    e = out["entities"][0]
    assert e["BiddingScheme"]["InheritedBidStrategyType"] == "MaxConversions"

def test_parent_required():
    with pytest.raises(ValueError, match="parent_id"):
        entities.get_entities("keywords")

def test_unknown_entity():
    with pytest.raises(ValueError, match="unknown entity"):
        entities.get_entities("bananas")

def test_campaign_criterions():
    out = entities.get_entities("campaign_criterions", parent_id=524066222)
    assert out["entities"][0]["Criterion"]["Type"] == "DayTime"

def test_bare_single_item_responses(monkeypatch):
    """suds collapses single-item arrays to a bare object, not a list (client.as_list) —
    a single-campaign account or single-ad-group campaign must not crash len()/iteration."""
    monkeypatch.setattr(client, "svc", lambda name: NS(
        GetCampaignsByAccountId=lambda AccountId, CampaignType: NS(Campaign=CAMP),  # bare, not [..]
        GetAdGroupsByCampaignId=lambda CampaignId: NS(AdGroup=AG),  # bare, not [..]
        factory=NS(create=lambda t: NS(string=None, long=None)),
    ))
    assert entities.get_entities("campaigns")["count"] == 1
    assert entities.get_entities("ad_groups", parent_id=524066222)["entities"][0]["Id"] == 111
