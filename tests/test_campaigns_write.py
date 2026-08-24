from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails
from mcp_microsoft_ads.tools import campaigns_write

CURRENT = NS(Id=524066223, Name="HVAC - Search - MS", Status="Active",
             DailyBudget=300.0, CampaignType="Search", BiddingScheme=None)
CREATED = NS(Id=999, Name="Test - Search", Status="Paused",
             DailyBudget=50.0, CampaignType="Search", BiddingScheme=None)
CURRENT_PMAX = NS(Id=524066230, Name="HVAC - PMax - MS", Status="Active",
                  DailyBudget=100.0, CampaignType="PerformanceMax", BiddingScheme=None)

class FakeSvc:
    def __init__(self):
        self.updated = []
        self.factory = NS(create=self._create)
    def _create(self, t):
        return NS(long=None, Campaign=None, Type=None, TargetCpa=None)
    def GetCampaignsByIds(self, AccountId, CampaignIds, CampaignType):
        wanted = list(CampaignIds.long)
        pool = {CURRENT.Id: CURRENT, CREATED.Id: CREATED, CURRENT_PMAX.Id: CURRENT_PMAX}
        return NS(Campaigns=NS(Campaign=[pool[i] for i in wanted if i in pool]))
    def UpdateCampaigns(self, AccountId, Campaigns):
        self.updated.append(Campaigns)
        return NS(PartialErrors=None)
    def AddCampaigns(self, AccountId, Campaigns):
        self.added = Campaigns
        return NS(CampaignIds=NS(long=[999]), PartialErrors=None)

@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda name: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc

def test_budget_update_previews_before_after(fake):
    d = campaigns_write.update_campaign(524066223, daily_budget=350)
    assert d["dry_run"] is True
    assert d["preview"]["changes"]["DailyBudget"] == {"before": 300.0, "after": 350}
    assert fake.updated == []  # nothing applied yet

def test_budget_cap_enforced(fake):
    with pytest.raises(rails.RailViolation):
        campaigns_write.update_campaign(524066223, daily_budget=1500)

def test_apply_sends_minimal_object_and_verifies(fake):
    d = campaigns_write.update_campaign(524066223, daily_budget=350)
    CURRENT.DailyBudget = 350.0  # simulate MS applying it (read-back sees new value)
    out = rails.apply_draft(d["draft_id"])
    sent = fake.updated[0].Campaign[0]  # Campaigns arg is now a factory-built ArrayOfCampaign, not a dict
    assert sent.Id == 524066223 and sent.DailyBudget == 350
    assert not hasattr(sent, "Settings") or sent.Settings is None  # minimal object
    assert out["result"]["verify"]["verified"] is True
    CURRENT.DailyBudget = 300.0  # reset

def test_target_cpa_scheme_built_via_client_blank(fake, monkeypatch):
    """target_cpa branch must build MaxConversionsBiddingScheme via client.blank(), same
    binding precedent as pmax.py (see test_pmax_created_paused_with_tcpa), not a raw
    factory.create(). The fake's factory.create is type-name-agnostic and blank() no-ops on
    plain NS (no __keylist__ — see test_pmax.py's fake comment), so Type/TargetCpa end up
    identical either way; the only discriminating signal is whether client.blank itself gets
    called. Spy on it (delegating to the real implementation) rather than weakening the
    assertion to something that would pass against a reverted raw factory.create() too.
    """
    blanked = []
    real_blank = client.blank
    def spy(svc_obj, type_name):
        blanked.append(type_name)
        return real_blank(svc_obj, type_name)
    monkeypatch.setattr(client, "blank", spy)

    d = campaigns_write.update_campaign(CURRENT_PMAX.Id, target_cpa=45)
    rails.apply_draft(d["draft_id"])

    sent = fake.updated[0].Campaign[0]
    assert sent.BiddingScheme.Type == "MaxConversions"
    assert sent.BiddingScheme.TargetCpa == 45
    assert "MaxConversionsBiddingScheme" in blanked

def test_no_op_rejected(fake):
    with pytest.raises(ValueError, match="nothing to change"):
        campaigns_write.update_campaign(524066223)

def test_create_campaign_default_paused(fake):
    d = campaigns_write.draft_campaign("Test - Search", "Search", daily_budget=50)
    assert d["preview"]["Status"] == "Paused"
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["campaign_ids"] == [999]
    # real ArrayOfCampaign container (attribute access), not a dict — fails against
    # today's Campaigns={"Campaign": [camp]} both on isinstance and on attribute access.
    assert not isinstance(fake.added, dict)
    sent = fake.added.Campaign[0]
    assert sent.Status == "Paused"
    # Languages must be a real ns3:ArrayOfstring factory object, not a dict standing in.
    assert not isinstance(sent.Languages, dict)
    assert sent.Languages.string == ["English"]
    assert out["result"]["verify"]["verified"] is True

def test_create_campaign_language_param(fake):
    d = campaigns_write.draft_campaign("Test - Search", "Search", daily_budget=50, language="Spanish")
    assert d["preview"]["Language"] == "Spanish"
    rails.apply_draft(d["draft_id"])
    sent = fake.added.Campaign[0]
    assert sent.Languages.string == ["Spanish"]

def test_create_campaign_built_via_client_blank(fake, monkeypatch):
    """draft_campaign's create payload must be built via client.blank(), same binding
    precedent as update_campaign/pmax.py, not a raw SimpleNamespace. Spy on client.blank
    (delegating to the real implementation, same style as
    test_target_cpa_scheme_built_via_client_blank) rather than inferring it from shape
    alone — the fake's factory.create is type-name-agnostic and blank() no-ops on a
    plain NS, so shape can't tell blank-built from hand-built.
    """
    blanked = []
    real_blank = client.blank
    def spy(svc_obj, type_name):
        blanked.append(type_name)
        return real_blank(svc_obj, type_name)
    monkeypatch.setattr(client, "blank", spy)

    d = campaigns_write.draft_campaign("Test - Search", "Search", daily_budget=50)
    rails.apply_draft(d["draft_id"])

    assert "Campaign" in blanked

def test_bare_single_campaign_response_not_truncated(monkeypatch, tmp_path):
    """suds collapses a single-item Campaign array to a bare object, not a
    list — _fetch_campaign must be routed through client.as_list."""
    svc = FakeSvc()
    svc.GetCampaignsByIds = lambda AccountId, CampaignIds, CampaignType: NS(
        Campaigns=NS(Campaign=NS(Id=524066223, Name="HVAC - Search - MS", Status="Active",
                                 DailyBudget=300.0, CampaignType="Search")))  # bare, not [..]
    monkeypatch.setattr(client, "svc", lambda name: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = campaigns_write.update_campaign(524066223, daily_budget=350)
    assert d["preview"]["campaign"] == "HVAC - Search - MS"
