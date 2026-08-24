from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails, settings
from mcp_microsoft_ads.tools import audiences


class FakeSvc:
    def __init__(self):
        self.factory = NS(create=lambda t: NS())  # blank() no-ops on NS (no __keylist__)

    def GetUetTagsByIds(self, TagIds):
        return NS(UetTags=NS(UetTag=[NS(Id=333, Name="Example UET")]))

    def AddAudiences(self, Audiences):
        return NS(AudienceIds=NS(long=[501]), PartialErrors=None)

    def AddCampaignCriterions(self, CampaignCriterions, CriterionType):
        return NS(CampaignCriterionIds=NS(long=[601]), PartialErrors=None, NestedPartialErrors=None)

    def GetAdGroupsByCampaignId(self, CampaignId):
        # live-verified (deviation 5): unwrapped .AdGroup, not .AdGroups.AdGroup
        return NS(AdGroup=[NS(Id=1, Status="Active",
                              BiddingScheme=NS(Type="InheritFromParent",
                                               InheritedBidStrategyType="MaxConversions"))])


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


def test_create_audience(fake):
    d = audiences.create_custom_audience("Site Visitors 30d", "all visitors", url_contains="example.com")
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["audience_ids"] == [501]


def test_create_audience_payload_shape(fake):
    """Payload must be a blank-built factory object with the discriminators
    re-set after client.blank() (binding deviations 1+2), not a bare SimpleNamespace."""
    captured = {}
    orig = fake.AddAudiences

    def spy(Audiences):
        captured["Audiences"] = Audiences
        return orig(Audiences)

    fake.AddAudiences = spy
    d = audiences.create_custom_audience("Site Visitors 30d", "all visitors", membership_duration_days=45,
                                         url_contains="example.com")
    rails.apply_draft(d["draft_id"])
    aud = captured["Audiences"].Audience[0]
    assert aud.Type == "RemarketingList"
    assert aud.Name == "Site Visitors 30d"
    assert aud.MembershipDuration == 45
    assert aud.TagId == 333
    assert aud.Rule.Type == "PageVisitorsRule"
    group = aud.Rule.RuleItemGroups.RuleItemGroup[0]
    item = group.Items.RuleItem[0]
    assert item.Type == "StringRuleItem"
    assert item.Operator == "Contains"
    assert item.Value == "example.com"


def test_create_audience_no_uet_tag_errors(monkeypatch, tmp_path):
    class NoTagSvc(FakeSvc):
        def GetUetTagsByIds(self, TagIds):
            return NS(UetTags=None)

    svc = NoTagSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    with pytest.raises(ValueError, match="no UET tag"):
        audiences.create_custom_audience("x", "y", url_contains="example.com")


def test_targeting_pct_blocked_on_smart_bidding(fake):
    with pytest.raises(rails.RailViolation, match="Smart Bidding"):
        audiences.add_audience_targeting(524066223, 501, bid_adjustment_pct=20)


def test_targeting_without_pct_ok(fake):
    d = audiences.add_audience_targeting(524066223, 501)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["criterion_ids"] == [601]


def test_targeting_payload_discriminators(fake):
    captured = {}
    orig = fake.AddCampaignCriterions

    def spy(CampaignCriterions, CriterionType):
        captured["payload"] = CampaignCriterions
        captured["CriterionType"] = CriterionType
        return orig(CampaignCriterions, CriterionType)

    fake.AddCampaignCriterions = spy
    d = audiences.add_audience_targeting(524066223, 501)
    rails.apply_draft(d["draft_id"])
    cc = captured["payload"].CampaignCriterion[0]
    assert captured["CriterionType"] == "Audience"
    assert cc.Type == "BiddableCampaignCriterion"
    assert cc.CampaignId == 524066223
    assert cc.Criterion.Type == "AudienceCriterion"
    assert cc.Criterion.AudienceId == 501
    assert cc.Criterion.AudienceType == "RemarketingList"  # default


def test_targeting_audience_type_override(fake):
    """audience_type disambiguates which id namespace audience_id is in — must reach
    the wire, both on the criterion and in the draft preview."""
    captured = {}
    orig = fake.AddCampaignCriterions

    def spy(CampaignCriterions, CriterionType):
        captured["payload"] = CampaignCriterions
        return orig(CampaignCriterions, CriterionType)

    fake.AddCampaignCriterions = spy
    d = audiences.add_audience_targeting(524066223, 501, audience_type="CustomerList")
    assert d["preview"]["audience_type"] == "CustomerList"
    rails.apply_draft(d["draft_id"])
    cc = captured["payload"].CampaignCriterion[0]
    assert cc.Criterion.AudienceType == "CustomerList"


# --- url_contains resolution (Task A2) --------------------------------------------

def test_url_contains_param_used(fake):
    """Explicit url_contains wins and lands in the preview + on the wire, with no
    advertiser_domain configured at all."""
    d = audiences.create_custom_audience("Site Visitors 30d", "all visitors",
                                         url_contains="mysite.example")
    assert d["preview"]["url_contains"] == "mysite.example"
    rails.apply_draft(d["draft_id"])


def test_url_contains_falls_back_to_advertiser_domain(fake, monkeypatch):
    monkeypatch.setattr(settings, "advertiser_domain", lambda: "fallback.example")
    d = audiences.create_custom_audience("Site Visitors 30d", "all visitors")
    assert d["preview"]["url_contains"] == "fallback.example"


def test_url_contains_neither_set_raises_naming_both(fake):
    # conftest's autouse clean_advertiser_settings fixture leaves advertiser_domain unset
    with pytest.raises(ValueError, match="url_contains") as exc_info:
        audiences.create_custom_audience("Site Visitors 30d", "all visitors")
    assert "advertiser_domain" in str(exc_info.value)


def test_url_contains_whitespace_param_rejected_not_fallback(fake, monkeypatch):
    """A whitespace-only url_contains param is invalid input, not 'omitted' — it must
    NOT silently fall back to advertiser_domain."""
    monkeypatch.setattr(settings, "advertiser_domain", lambda: "fallback.example")
    with pytest.raises(ValueError, match="url_contains"):
        audiences.create_custom_audience("Site Visitors 30d", "all visitors", url_contains="   ")


def test_url_contains_whitespace_setting_treated_as_unset(monkeypatch):
    monkeypatch.setattr(settings, "advertiser_domain", lambda: "   ")
    with pytest.raises(ValueError, match="advertiser_domain"):
        audiences._resolve_url_contains(None)
