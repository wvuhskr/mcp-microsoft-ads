"""Negative-keyword writes: add/remove at campaign, ad_group, or shared_list scope.
Blocklist NOT applied here — negating a settings-blocked term is desired, only
*advertising* it is blocked (see keywords_write.draft_keywords)."""
from .. import client, rails
from ..app import mcp

_ENTITY = {"campaign": "Campaign", "ad_group": "AdGroup"}
_SCOPES = set(_ENTITY) | {"shared_list"}


def _existing_texts(svc, scope: str, entity_id: int, campaign_id: int | None = None) -> set[tuple[str, str]]:
    """check-existing-lists-first rule. Mirrors negatives_read.py's live-verified shapes:
    GetListItemsBySharedList unwraps to `.SharedListItem` directly; GetNegativeKeywordsByEntityIds
    stays wrapped in EntityNegativeKeywords/NegativeKeywords.

    Live+WSDL verified: GetNegativeKeywordsByEntityIds hard-faults "Invalid client data" for
    EntityType=AdGroup unless ParentEntityId=<campaign_id> is passed. Campaign scope works
    without it — only ad_group gets the extra kwarg."""
    if scope == "shared_list":
        sl = svc.factory.create("NegativeKeywordList")
        sl.Id = entity_id
        items = svc.GetListItemsBySharedList(SharedList=sl)
        kws = client.as_list(getattr(items, "SharedListItem", None))
        return {(k.Text.lower(), k.MatchType) for k in kws}
    ids = svc.factory.create("ns3:ArrayOflong")
    ids.long = [entity_id]
    kwargs = {"EntityIds": ids, "EntityType": _ENTITY[scope]}
    if scope == "ad_group":
        kwargs["ParentEntityId"] = campaign_id
    r = svc.GetNegativeKeywordsByEntityIds(**kwargs)
    out = set()
    for enk in client.as_list(getattr(getattr(r, "EntityNegativeKeywords", None), "EntityNegativeKeyword", None)):
        for k in client.as_list(getattr(getattr(enk, "NegativeKeywords", None), "NegativeKeyword", None)):
            out.add((k.Text.lower(), k.MatchType))
    return out


def _negative_keyword(svc, text: str | None = None, match_type: str | None = None, kw_id: int | None = None):
    nk = client.blank(svc, "NegativeKeyword")
    nk.Type = "NegativeKeyword"  # discriminator (client.blank live lesson)
    if text is not None:
        nk.Text = text
        nk.MatchType = match_type
    if kw_id is not None:
        nk.Id = kw_id
    return nk


@mcp.tool()
def add_negative_keywords(scope: str, entity_id: int, keywords: list[dict],
                          campaign_id: int | None = None) -> dict:
    """Draft negatives. scope: campaign | ad_group | shared_list. keywords:
    [{text, match_type: Phrase|Exact}]. Checks existing negatives first and skips
    duplicates (check-existing-lists-first rule). Blocklist NOT applied — negating
    a settings-blocked term is desired. scope='ad_group' REQUIRES campaign_id (the
    parent campaign) — MS's read API hard-faults for AdGroup scope without it.

    Live-verified 2026-07-28 at ad_group scope, including the duplicate-check read;
    campaign scope not separately smoked."""
    if scope not in _SCOPES:
        raise ValueError(f"scope must be one of {sorted(_SCOPES)}")
    if scope == "ad_group" and campaign_id is None:
        raise ValueError("campaign_id required for scope='ad_group' (MS API needs ParentEntityId)")
    svc = client.svc("CampaignManagementService")
    existing = _existing_texts(svc, scope, entity_id, campaign_id)

    adding = [k for k in keywords if (k["text"].lower(), k["match_type"]) not in existing]
    skipped = [k for k in keywords if (k["text"].lower(), k["match_type"]) in existing]
    if not adding:
        raise ValueError(f"all {len(keywords)} negatives already present on {scope} {entity_id}")

    def apply():
        negs = [_negative_keyword(svc, text=k["text"], match_type=k["match_type"]) for k in adding]

        if scope == "shared_list":
            arr = svc.factory.create("ArrayOfSharedListItem")  # base-type array name (WSDL-checked)
            arr.SharedListItem = negs
            sl = svc.factory.create("NegativeKeywordList")
            sl.Id = entity_id
            resp = svc.AddListItemsToSharedList(ListItems=arr, SharedList=sl)
            ids = [int(x) for x in client.as_list(getattr(getattr(resp, "ListItemIds", None), "long", None))]
            return {"added_ids": ids, "partial_errors": client.partial_errors(resp)}

        neg_arr = svc.factory.create("ArrayOfNegativeKeyword")
        neg_arr.NegativeKeyword = negs
        enk = svc.factory.create("EntityNegativeKeyword")
        enk.EntityId = entity_id
        enk.EntityType = _ENTITY[scope]
        enk.NegativeKeywords = neg_arr
        enk_arr = svc.factory.create("ArrayOfEntityNegativeKeyword")
        enk_arr.EntityNegativeKeyword = [enk]
        resp = svc.AddNegativeKeywordsToEntities(EntityNegativeKeywords=enk_arr)
        return {"added": len(adding), "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("add_negative_keywords",
                              {"scope": scope, "entity_id": entity_id, "adding": adding,
                               "skipped_existing": skipped}, apply)


@mcp.tool()
def remove_negative_keywords(scope: str, entity_id: int, keyword_ids: list[int],
                             campaign_id: int | None = None) -> dict:
    """Draft negative removal by id. scope: campaign | ad_group | shared_list.
    keyword_ids come from get_negative_keywords. scope='ad_group' REQUIRES
    campaign_id for consistency with add_negative_keywords — validation only,
    DeleteNegativeKeywordsFromEntities has no parent field per WSDL.

    Live-verified 2026-07-28 at ad_group scope; campaign scope not separately
    smoked."""
    if scope not in _SCOPES:
        raise ValueError(f"scope must be one of {sorted(_SCOPES)}")
    if scope == "ad_group" and campaign_id is None:
        raise ValueError("campaign_id required for scope='ad_group' (MS API needs ParentEntityId)")
    svc = client.svc("CampaignManagementService")

    def apply():
        if scope == "shared_list":
            ids = svc.factory.create("ns3:ArrayOflong")
            ids.long = keyword_ids
            sl = svc.factory.create("NegativeKeywordList")
            sl.Id = entity_id
            resp = svc.DeleteListItemsFromSharedList(ListItemIds=ids, SharedList=sl)
        else:
            negs = [_negative_keyword(svc, kw_id=kid) for kid in keyword_ids]
            neg_arr = svc.factory.create("ArrayOfNegativeKeyword")
            neg_arr.NegativeKeyword = negs
            enk = svc.factory.create("EntityNegativeKeyword")
            enk.EntityId = entity_id
            enk.EntityType = _ENTITY[scope]
            enk.NegativeKeywords = neg_arr
            enk_arr = svc.factory.create("ArrayOfEntityNegativeKeyword")
            enk_arr.EntityNegativeKeyword = [enk]
            resp = svc.DeleteNegativeKeywordsFromEntities(EntityNegativeKeywords=enk_arr)
        return {"removed": len(keyword_ids), "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("remove_negative_keywords",
                              {"scope": scope, "entity_id": entity_id, "keyword_ids": keyword_ids}, apply)
