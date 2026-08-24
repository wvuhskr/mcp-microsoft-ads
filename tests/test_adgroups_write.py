from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails
from mcp_microsoft_ads.tools import adgroups_write

AG = NS(Id=111, Name="Leak Detection", Status="Active",
        BiddingScheme=NS(Type="InheritFromParent", InheritedBidStrategyType="MaxConversions"),
        CpcBid=NS(Amount=20.0))

class FakeSvc:
    def __init__(self):
        self.updated = []
        self.factory = NS(create=lambda t: NS(Type=None, TargetCpa=None, Amount=None, long=None))
    def GetAdGroupsByIds(self, CampaignId, AdGroupIds):
        return NS(AdGroups=NS(AdGroup=[AG]))
    def UpdateAdGroups(self, CampaignId, AdGroups):
        self.updated.append((CampaignId, AdGroups))
        return NS(PartialErrors=None)

@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc

def test_tcpa_draft_and_apply(fake):
    # ponytail: brief's verbatim test used target_cpa=160, which cannot pass alongside
    # test_tcpa_cap's 51-raises assertion under the real MS_ADS_MAX_CPC=50 cap (both can't be
    # true with one static cap) — using 45 (under cap) to keep the suite internally consistent.
    d = adgroups_write.update_ad_group(111, campaign_id=524066223, target_cpa=45)
    assert d["preview"]["changes"]["TargetCpa"]["after"] == 45
    assert d["preview"]["effective_strategy"] == "MaxConversions"
    AG.BiddingScheme = NS(Type="MaxConversions", TargetCpa=45.0)  # simulate apply
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["verify"]["verified"] is True
    AG.BiddingScheme = NS(Type="InheritFromParent", InheritedBidStrategyType="MaxConversions")

def test_tcpa_cap(fake):
    with pytest.raises(rails.RailViolation):
        adgroups_write.update_ad_group(111, campaign_id=1, target_cpa=51)

def test_cpc_cap(fake):
    with pytest.raises(rails.RailViolation):
        adgroups_write.update_ad_group(111, campaign_id=1, cpc_bid=50.5)

def test_bare_single_ad_group_response_not_truncated(monkeypatch, tmp_path):
    """suds collapses a single-item AdGroup array to a bare object, not a list —
    _fetch_ad_group must be routed through client.as_list (same defect class swept
    elsewhere as campaigns_write._fetch_campaign / test_bare_single_campaign_response_not_truncated).
    Pre-fix, `ags[0]` on the bare object raises a raw TypeError instead of the
    intended clean ValueError — and since GetAdGroupsByIds here is always a
    single-id call, the bare shape is the everyday case, not an edge case."""
    bare_ag = NS(Id=111, Name="Leak Detection", Status="Active",
                BiddingScheme=NS(Type="InheritFromParent", InheritedBidStrategyType="MaxConversions"),
                CpcBid=NS(Amount=20.0))
    svc = FakeSvc()
    svc.GetAdGroupsByIds = lambda CampaignId, AdGroupIds: NS(AdGroups=NS(AdGroup=bare_ag))  # bare, not [..]
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = adgroups_write.update_ad_group(111, campaign_id=524066223, target_cpa=45)
    assert d["preview"]["ad_group"] == "Leak Detection"

def test_bid_and_tcpa_built_via_client_blank(fake, monkeypatch):
    """update_ad_group's apply() built the nested Bid (cpc_bid) and
    MaxConversionsBiddingScheme (target_cpa) objects via raw svc.factory.create(...)
    instead of client.blank() — same defect class as the live SOAP faults client.blank
    exists to avoid (see client.blank docstring; corrected precedents: pmax.py's
    MaxConversionsBiddingScheme, keywords_write.update_keyword_bid's Bid). Spy on
    client.blank (delegating to the real implementation, same style as
    tests/test_campaigns_write.py and tests/test_keywords_write.py) rather than
    inferring it from shape alone — the fake's factory.create is type-name-agnostic
    and blank() no-ops on a plain NS, so shape can't tell blank-built from hand-built."""
    blanked = []
    real_blank = client.blank
    def spy(svc_obj, type_name):
        blanked.append(type_name)
        return real_blank(svc_obj, type_name)
    monkeypatch.setattr(client, "blank", spy)

    d = adgroups_write.update_ad_group(111, campaign_id=524066223, cpc_bid=20, target_cpa=45)
    rails.apply_draft(d["draft_id"])

    sent = fake.updated[0][1].AdGroup[0]
    assert sent.CpcBid.Amount == 20
    assert sent.BiddingScheme.Type == "MaxConversions"
    assert sent.BiddingScheme.TargetCpa == 45
    assert "Bid" in blanked
    assert "MaxConversionsBiddingScheme" in blanked

def test_effective_strategy_routes_through_shared_client_helper(fake, monkeypatch):
    """adgroups_write.py carried a private _effective_strategy(ag) that is byte-for-byte
    identical logic to the shared client.effective_strategy (already used by
    audiences.py and keywords_write.py) — delete the duplicate and route this file's
    call site through the shared helper instead. The return value is identical either
    way (same logic, copy-pasted), so it can't distinguish shared-helper-call from
    private-copy-call — spy on client.effective_strategy (delegating to the real
    implementation, same style as the client.blank spies above) to prove the call site
    actually uses it."""
    calls = []
    real = client.effective_strategy
    def spy(ag):
        calls.append(ag)
        return real(ag)
    monkeypatch.setattr(client, "effective_strategy", spy)

    d = adgroups_write.update_ad_group(111, campaign_id=524066223, target_cpa=45)
    assert calls, "client.effective_strategy was never called — a private duplicate is still in use"
    assert d["preview"]["effective_strategy"] == "MaxConversions"
