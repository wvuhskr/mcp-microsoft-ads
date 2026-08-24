from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails, settings
from mcp_microsoft_ads.tools import keywords_write


class FakeSvc:
    def __init__(self):
        self.added = []
        self.factory = NS(create=lambda t: NS(long=None, Amount=None))

    def AddKeywords(self, AdGroupId, Keywords):
        self.added.append(Keywords)
        return NS(KeywordIds=NS(long=[71, 72]), PartialErrors=None)

    def GetKeywordsByAdGroupId(self, AdGroupId):
        return NS(Keyword=[NS(Id=71, Text="ac repair", Status="Paused", Bid=NS(Amount=20)),
                           NS(Id=72, Text="hvac tune up", Status="Paused")])

    def GetAdGroupsByIds(self, CampaignId, AdGroupIds):
        # live-verified WRAPPED shape (unlike GetAdGroupsByCampaignId, which is unwrapped) —
        # default ad group carries a recognized non-Smart strategy so pre-existing
        # update_keyword_bid tests keep proceeding to apply.
        return NS(AdGroups=NS(AdGroup=[NS(Id=111, BiddingScheme=NS(
            Type="ManualCpc", InheritedBidStrategyType=None))]))

    def UpdateKeywords(self, AdGroupId, Keywords):
        return NS(PartialErrors=None)

    def DeleteKeywords(self, AdGroupId, KeywordIds):
        return NS(PartialErrors=None)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


def test_keywords_created_paused(fake):
    d = keywords_write.draft_keywords(111, [{"text": "ac repair", "match_type": "Phrase", "bid": 15}])
    assert d["preview"]["keywords"][0]["Status"] == "Paused"
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["keyword_ids"] == [71, 72]
    # factory-built ArrayOfKeyword (deviation 2), not a bare dict — attribute access, not subscript
    assert fake.added[0].Keyword[0].Status == "Paused"


def test_blocklist_enforced(fake, monkeypatch):
    # blocked_terms is settings-driven now (empty by default in tests — see conftest)
    monkeypatch.setattr(settings, "blocked_terms", lambda: ("sewage", "backup"))
    with pytest.raises(rails.RailViolation, match="sewage"):
        keywords_write.draft_keywords(111, [{"text": "sewage cleanup", "match_type": "Exact", "bid": 5}])


def test_bid_cap(fake):
    with pytest.raises(rails.RailViolation):
        keywords_write.draft_keywords(111, [{"text": "ac", "match_type": "Exact", "bid": 51}])


def test_update_keyword_bid_cap(fake):
    with pytest.raises(rails.RailViolation):
        keywords_write.update_keyword_bid(111, 71, bid=99)


def test_update_keyword_bid_apply(fake):
    d = keywords_write.update_keyword_bid(111, 71, bid=20)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["partial_errors"] == []
    assert out["result"]["verify"]["verified"] is True


def test_update_keyword_bid_allowed_recognized_non_smart_strategy(fake):
    """Brief item 3: a recognized NON-Smart strategy (default fake ad group is ManualCpc)
    must still proceed — the pre-reject must not over-block."""
    d = keywords_write.update_keyword_bid(111, 71, bid=20)
    assert d["preview"]["effective_strategy"] == "ManualCpc"
    rails.apply_draft(d["draft_id"])  # does not raise


def test_update_keyword_bid_blocked_smart_bidding(fake, monkeypatch):
    """Owner decision (Task 25e): UpdateKeywords silently ignores Bid changes when the ad
    group's effective strategy is Smart Bidding (live-verified: empty PartialErrors, bid
    unchanged 15s later) — must raise RailViolation at draft time, before any write."""
    class SmartBiddingSvc(FakeSvc):
        def GetAdGroupsByIds(self, CampaignId, AdGroupIds):
            return NS(AdGroups=NS(AdGroup=[NS(Id=111, BiddingScheme=NS(
                Type="InheritFromParent", InheritedBidStrategyType="MaxConversions"))]))

        def UpdateKeywords(self, AdGroupId, Keywords):
            raise AssertionError("must not reach UpdateKeywords when strategy is Smart Bidding")

    svc = SmartBiddingSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    with pytest.raises(rails.RailViolation, match="Smart Bidding"):
        keywords_write.update_keyword_bid(111, 71, bid=20)


def test_update_keyword_bid_blocked_unknown_strategy(fake, monkeypatch):
    """Fail closed (Task 25e, consistent with rails.check_no_pct_adjustment): if the ad
    group's effective strategy can't be determined, block rather than assume it's safe."""
    class NoStrategySvc(FakeSvc):
        def GetAdGroupsByIds(self, CampaignId, AdGroupIds):
            return NS(AdGroups=NS(AdGroup=[NS(Id=111, BiddingScheme=None)]))

        def UpdateKeywords(self, AdGroupId, Keywords):
            raise AssertionError("must not reach UpdateKeywords when strategy is unknown")

    svc = NoStrategySvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    with pytest.raises(rails.RailViolation, match="could not be determined"):
        keywords_write.update_keyword_bid(111, 71, bid=20)


def test_update_keyword_bid_uses_client_blank_for_bid(fake, monkeypatch):
    """keywords_write.py:58 built the nested Bid via a raw svc.factory.create("Bid") — same
    defect class as the live SOAP fault client.blank exists to avoid (see client.blank
    docstring). Spy on client.blank (delegating to the real implementation, same style as
    tests/test_campaigns_write.py) rather than inferring it from shape alone — the fake's
    factory.create is type-name-agnostic and blank() no-ops on a plain NS, so shape can't
    tell blank-built from hand-built."""
    blanked = []
    real_blank = client.blank

    def spy(svc_obj, type_name):
        blanked.append(type_name)
        return real_blank(svc_obj, type_name)

    monkeypatch.setattr(client, "blank", spy)
    d = keywords_write.update_keyword_bid(111, 71, bid=20)
    rails.apply_draft(d["draft_id"])
    assert "Bid" in blanked


def test_draft_keywords_uses_client_blank_for_bid(fake, monkeypatch):
    """draft_keywords built the nested Bid via a raw svc.factory.create("Bid") — same
    defect class client.blank exists to avoid (see client.blank docstring), already fixed
    on the update_keyword_bid sibling (test_update_keyword_bid_uses_client_blank_for_bid).
    Spy on client.blank (delegating to the real implementation, same style as that test
    and tests/test_campaigns_write.py) rather than inferring it from shape alone — the
    fake's factory.create is type-name-agnostic and blank() no-ops on a plain NS, so shape
    can't tell blank-built from hand-built."""
    blanked = []
    real_blank = client.blank

    def spy(svc_obj, type_name):
        blanked.append(type_name)
        return real_blank(svc_obj, type_name)

    monkeypatch.setattr(client, "blank", spy)
    d = keywords_write.draft_keywords(111, [{"text": "ac repair", "match_type": "Phrase", "bid": 15}])
    rails.apply_draft(d["draft_id"])
    assert "Bid" in blanked


def test_single_keyword_bare_id_not_truncated(fake, monkeypatch):
    """suds unmarshals a single-item `long` array as a bare int, not a list
    (client.as_list) — a single-keyword add (the common case) must not silently
    drop or mis-handle the id."""
    class SingleFakeSvc(FakeSvc):
        def AddKeywords(self, AdGroupId, Keywords):
            self.added.append(Keywords)
            return NS(KeywordIds=NS(long=71), PartialErrors=None)  # bare int, not [71]

    svc = SingleFakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    d = keywords_write.draft_keywords(111, [{"text": "ac repair", "match_type": "Phrase"}])
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["keyword_ids"] == [71]


def test_remove_keywords(fake):
    d = keywords_write.remove_keywords(111, [71, 72])
    assert "warning" in d["preview"]
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["deleted"] == 2
    assert out["result"]["partial_errors"] == []


def test_bare_single_keyword_readback(monkeypatch, tmp_path):
    """An ad group with ONE keyword collapses to a bare object (client.as_list) — the
    post-update verify read-back must still find it instead of raising."""
    svc = FakeSvc()
    svc.GetKeywordsByAdGroupId = lambda AdGroupId: NS(
        Keyword=NS(Id=71, Text="ac repair", Status="Paused", Bid=NS(Amount=20)))
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    d = keywords_write.update_keyword_bid(111, 71, bid=20)
    assert rails.apply_draft(d["draft_id"])["result"]["verify"]["verified"] is True
