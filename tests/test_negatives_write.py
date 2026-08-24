from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails
from mcp_microsoft_ads.tools import negatives_write


class FakeSvc:
    def __init__(self):
        self.added = []
        self.removed = []
        self.shared_added = []
        self.shared_removed = []
        self.get_negs_calls = []
        self.factory = NS(create=lambda t: NS(long=None, Text=None, MatchType=None, Type=None,
                                              EntityId=None, EntityType=None, NegativeKeywords=None,
                                              Id=None))

    def GetNegativeKeywordsByEntityIds(self, EntityIds, EntityType, ParentEntityId=None):
        self.get_negs_calls.append({"EntityType": EntityType, "ParentEntityId": ParentEntityId})
        if EntityType == "AdGroup":
            return NS(EntityNegativeKeywords=NS(EntityNegativeKeyword=[NS(
                EntityId=77, EntityType="AdGroup",
                NegativeKeywords=NS(NegativeKeyword=[NS(Id=2, Text="diy", MatchType="Phrase")]))]))
        return NS(EntityNegativeKeywords=NS(EntityNegativeKeyword=[NS(
            EntityId=5, EntityType="Campaign",
            NegativeKeywords=NS(NegativeKeyword=[NS(Id=1, Text="free estimate", MatchType="Phrase")]))]))

    def AddNegativeKeywordsToEntities(self, EntityNegativeKeywords):
        self.added.append(EntityNegativeKeywords)
        return NS(NestedPartialErrors=None, PartialErrors=None)

    def DeleteNegativeKeywordsFromEntities(self, EntityNegativeKeywords):
        self.removed.append(EntityNegativeKeywords)
        return NS(NestedPartialErrors=None)

    def GetListItemsBySharedList(self, SharedList):
        return NS(SharedListItem=[NS(Text="diy", MatchType="Phrase")])

    def AddListItemsToSharedList(self, ListItems, SharedList):
        self.shared_added.append(ListItems)
        return NS(ListItemIds=NS(long=[901]), PartialErrors=None)

    def DeleteListItemsFromSharedList(self, ListItemIds, SharedList):
        self.shared_removed.append(ListItemIds)
        return NS(PartialErrors=None)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


def test_duplicate_skipped_and_new_added(fake):
    d = negatives_write.add_negative_keywords("campaign", 5, [
        {"text": "free estimate", "match_type": "Phrase"},   # dupe
        {"text": "diy", "match_type": "Phrase"}])
    assert d["preview"]["skipped_existing"] == [{"text": "free estimate", "match_type": "Phrase"}]
    assert d["preview"]["adding"] == [{"text": "diy", "match_type": "Phrase"}]
    rails.apply_draft(d["draft_id"])
    assert len(fake.added) == 1


def test_all_duplicates_rejected(fake):
    with pytest.raises(ValueError, match="already present"):
        negatives_write.add_negative_keywords("campaign", 5, [{"text": "free estimate", "match_type": "Phrase"}])


def test_negatives_allow_blocked_terms(fake):
    d = negatives_write.add_negative_keywords("campaign", 5, [{"text": "sewage backup", "match_type": "Phrase"}])
    assert d["preview"]["adding"][0]["text"] == "sewage backup"  # negating sewage is desired


def test_remove_negative_keywords(fake):
    d = negatives_write.remove_negative_keywords("campaign", 5, [1])
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["removed"] == 1
    assert len(fake.removed) == 1


def test_shared_list_scope_add_and_remove(fake):
    d = negatives_write.add_negative_keywords("shared_list", 900, [
        {"text": "diy", "match_type": "Phrase"},   # dupe per GetListItemsBySharedList
        {"text": "cheap", "match_type": "Phrase"}])
    assert d["preview"]["skipped_existing"] == [{"text": "diy", "match_type": "Phrase"}]
    assert d["preview"]["adding"] == [{"text": "cheap", "match_type": "Phrase"}]
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["added_ids"] == [901]

    d2 = negatives_write.remove_negative_keywords("shared_list", 900, [1, 2])
    out2 = rails.apply_draft(d2["draft_id"])
    assert out2["result"]["removed"] == 2
    assert len(fake.shared_removed) == 1


def test_invalid_scope_rejected(fake):
    with pytest.raises(ValueError, match="scope"):
        negatives_write.add_negative_keywords("bogus", 5, [{"text": "diy", "match_type": "Phrase"}])


def test_ad_group_scope_requires_campaign_id(fake):
    with pytest.raises(ValueError, match="campaign_id"):
        negatives_write.add_negative_keywords("ad_group", 77, [{"text": "diy", "match_type": "Phrase"}])
    with pytest.raises(ValueError, match="campaign_id"):
        negatives_write.remove_negative_keywords("ad_group", 77, [2])


def test_ad_group_scope_passes_parent_entity_id(fake):
    # live+WSDL verified: GetNegativeKeywordsByEntityIds hard-faults for AdGroup scope
    # without ParentEntityId=<campaign_id>
    d = negatives_write.add_negative_keywords("ad_group", 77, [
        {"text": "diy", "match_type": "Phrase"},   # dupe
        {"text": "cheap", "match_type": "Phrase"}], campaign_id=999)
    assert fake.get_negs_calls[-1] == {"EntityType": "AdGroup", "ParentEntityId": 999}
    assert d["preview"]["skipped_existing"] == [{"text": "diy", "match_type": "Phrase"}]
    assert d["preview"]["adding"] == [{"text": "cheap", "match_type": "Phrase"}]


def test_ad_group_scope_remove_with_campaign_id(fake):
    # remove is validation-only for campaign_id (no read, no parent field on the delete payload)
    d = negatives_write.remove_negative_keywords("ad_group", 77, [2], campaign_id=999)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["removed"] == 1


def test_bare_single_existing_negative_still_deduped(monkeypatch, tmp_path):
    """A campaign with ONE existing negative collapses to bare objects (client.as_list) —
    check-existing-lists-first must still see it, not re-add a duplicate."""
    svc = FakeSvc()
    svc.GetNegativeKeywordsByEntityIds = lambda EntityIds, EntityType, ParentEntityId=None: NS(
        EntityNegativeKeywords=NS(EntityNegativeKeyword=NS(
            EntityId=5, EntityType="Campaign",
            NegativeKeywords=NS(NegativeKeyword=NS(Id=1, Text="free estimate", MatchType="Phrase")))))
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    with pytest.raises(ValueError, match="already present"):
        negatives_write.add_negative_keywords("campaign", 5,
                                              [{"text": "free estimate", "match_type": "Phrase"}])
