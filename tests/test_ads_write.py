from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails, settings
from mcp_microsoft_ads.tools import ads_write


class FakeSvc:
    def __init__(self):
        self.added = []
        # generic blank stand-in: client.blank() no-ops (no __keylist__) and every
        # attribute get set explicitly afterward by ads_write, so no fields needed here.
        self.factory = NS(create=lambda t: NS())

    def GetAdGroupsByIds(self, CampaignId, AdGroupIds):
        return NS(AdGroups=NS(AdGroup=[NS(Id=111, Name="Leak Detection", Status="Paused")]))

    def AddAds(self, AdGroupId, Ads):
        self.added.append(Ads)
        return NS(AdIds=NS(long=[801]), PartialErrors=None)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


H3 = ["AC Repair Near You", "Fast HVAC Service", "Licensed Techs"]
D2 = ["Call Example for same-week HVAC service.", "Serving Example Region."]


def test_rsa_created(fake):
    d = ads_write.draft_responsive_search_ad(111, campaign_id=1, headlines=H3, descriptions=D2,
                                             final_url="https://example.com/ac-repair")
    assert d["preview"]["warning"] is None  # parent ad group already Paused in fixture
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["ad_ids"] == [801]
    assert out["result"]["partial_errors"] == []
    # factory-built ArrayOfAd/ArrayOfAssetLink (deviation 1), not a bare dict —
    # attribute access, not subscript.
    ad = fake.added[0].Ad[0]
    assert ad.Type == "ResponsiveSearch"  # AdType enum value, not the class name
    assert [al.Asset.Text for al in ad.Headlines.AssetLink] == H3
    assert [al.Asset.Text for al in ad.Descriptions.AssetLink] == D2
    assert all(al.Asset.Type == "TextAsset" for al in ad.Headlines.AssetLink + ad.Descriptions.AssetLink)


def test_warns_when_ad_group_active(fake, monkeypatch):
    class ActiveSvc(FakeSvc):
        def GetAdGroupsByIds(self, CampaignId, AdGroupIds):
            return NS(AdGroups=NS(AdGroup=[NS(Id=111, Name="Leak Detection", Status="Active")]))

    svc = ActiveSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    d = ads_write.draft_responsive_search_ad(111, 1, H3, D2, "https://example.com/ac-repair")
    assert "Active" in d["preview"]["warning"]


def test_counts_enforced(fake):
    with pytest.raises(ValueError, match="3-15 headlines"):
        ads_write.draft_responsive_search_ad(111, 1, H3[:2], D2, "https://x.com")
    with pytest.raises(ValueError, match="2-4 descriptions"):
        ads_write.draft_responsive_search_ad(111, 1, H3, D2[:1], "https://x.com")


def test_blocklist_on_copy(fake, monkeypatch):
    monkeypatch.setattr(settings, "blocked_terms", lambda: ("sewage", "backup"))
    with pytest.raises(rails.RailViolation, match="sewage"):
        ads_write.draft_responsive_search_ad(111, 1, H3 + ["Sewage Backup Help"], D2, "https://x.com")


def test_bare_single_ad_group_response(fake, monkeypatch):
    """suds collapses a single-item AdGroup array to a bare object, not a list
    (client.as_list) — and the GetAdGroupsByIds call here is always single-id,
    so the bare shape is the everyday case (same defect class as
    adgroups_write._fetch_ad_group / test_bare_single_ad_group_response_not_truncated)."""
    class BareSvc(FakeSvc):
        def GetAdGroupsByIds(self, CampaignId, AdGroupIds):
            return NS(AdGroups=NS(AdGroup=NS(Id=111, Name="Leak Detection", Status="Paused")))  # bare, not [..]

    svc = BareSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    d = ads_write.draft_responsive_search_ad(111, 1, H3, D2, "https://example.com/ac-repair")
    assert d["preview"]["warning"] is None


def test_single_ad_bare_id_not_truncated(fake, monkeypatch):
    """suds unmarshals a single-item `long` array as a bare int, not a list
    (client.as_list) — a single-ad add (the common case) must not silently
    drop or mis-handle the id."""
    class SingleFakeSvc(FakeSvc):
        def AddAds(self, AdGroupId, Ads):
            self.added.append(Ads)
            return NS(AdIds=NS(long=801), PartialErrors=None)  # bare int, not [801]

    svc = SingleFakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    d = ads_write.draft_responsive_search_ad(111, 1, H3, D2, "https://example.com/ac-repair")
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["ad_ids"] == [801]
