from types import SimpleNamespace as NS

from mcp_microsoft_ads import client
from mcp_microsoft_ads.tools import negatives_read


def fake_svc(name):
    return NS(
        GetNegativeKeywordsByEntityIds=lambda EntityIds, EntityType, ParentEntityId=None: NS(
            EntityNegativeKeywords=NS(EntityNegativeKeyword=[NS(
                EntityId=524066222, EntityType="Campaign",
                NegativeKeywords=NS(NegativeKeyword=[NS(Id=1, Text="sewage", MatchType="Phrase")]))])),
        # live-probed: GetSharedEntitiesByAccountId and GetListItemsBySharedList return their
        # ArrayOf* payload directly (no SharedEntities/ListItems wrapper level)
        GetSharedEntitiesByAccountId=lambda SharedEntityType: NS(
            SharedEntity=[NS(Id=900, Name="Master Negatives", Type="NegativeKeywordList")]),
        GetListItemsBySharedList=lambda SharedList: NS(
            SharedListItem=[NS(Id=2, Text="free", MatchType="Exact", Type="NegativeKeyword")]),
        GetSharedEntityAssociationsBySharedEntityIds=lambda EntityType, SharedEntityIds, SharedEntityType: NS(
            Associations=NS(SharedEntityAssociation=[NS(EntityId=524066222, SharedEntityId=900)])),
        factory=NS(create=lambda t: NS(long=None, Id=None, Type=None)),
    )

def test_reads_both_scopes(monkeypatch):
    monkeypatch.setattr(client, "svc", fake_svc)
    out = negatives_read.get_negative_keywords(campaign_id=524066222)
    assert out["campaign_negatives"]["524066222"][0]["Text"] == "sewage"
    sl = out["shared_lists"][0]
    assert sl["name"] == "Master Negatives"
    assert sl["keywords"][0]["Text"] == "free"
    assert 524066222 in sl["associated_campaign_ids"]


def test_bare_single_item_shapes(monkeypatch):
    """suds collapses every single-item array to a bare object (client.as_list). An account
    with one shared list holding one keyword on one campaign hits all five collapse points."""
    monkeypatch.setattr(client, "svc", lambda name: NS(
        GetNegativeKeywordsByEntityIds=lambda EntityIds, EntityType, ParentEntityId=None: NS(
            EntityNegativeKeywords=NS(EntityNegativeKeyword=NS(
                EntityId=524066222, EntityType="Campaign",
                NegativeKeywords=NS(NegativeKeyword=NS(Id=1, Text="sewage", MatchType="Phrase"))))),
        GetSharedEntitiesByAccountId=lambda SharedEntityType: NS(
            SharedEntity=NS(Id=900, Name="Master Negatives", Type="NegativeKeywordList")),
        GetListItemsBySharedList=lambda SharedList: NS(
            SharedListItem=NS(Id=2, Text="free", MatchType="Exact", Type="NegativeKeyword")),
        GetSharedEntityAssociationsBySharedEntityIds=lambda EntityType, SharedEntityIds, SharedEntityType: NS(
            Associations=NS(SharedEntityAssociation=NS(EntityId=524066222, SharedEntityId=900))),
        factory=NS(create=lambda t: NS(long=None, Id=None, Type=None)),
    ))
    out = negatives_read.get_negative_keywords(campaign_id=524066222)
    assert out["campaign_negatives"]["524066222"][0]["Text"] == "sewage"
    sl = out["shared_lists"][0]
    assert sl["keywords"][0]["Text"] == "free"
    assert sl["associated_campaign_ids"] == [524066222]
