"""Keyword writes: draft (created Paused), bid updates, deletion. Blocklist enforced
in draft_keywords ONLY — negatives are exempt on purpose (see negatives_write)."""
from .. import client, rails, verify
from ..app import mcp


def _fetch_keyword(svc, ad_group_id: int, keyword_id: int):
    r = svc.GetKeywordsByAdGroupId(AdGroupId=ad_group_id)  # live-verified unwrapped: .Keyword direct (status.py)
    return next(k for k in client.as_list(getattr(r, "Keyword", None)) if k.Id == keyword_id)


def _ad_group_strategy(svc, ad_group_id: int) -> str | None:
    """Fetch a single ad group and read its effective bid strategy (client.effective_strategy).
    GetAdGroupsByIds returns the WRAPPED shape (resp.AdGroups.AdGroup) — unlike
    GetAdGroupsByCampaignId (unwrapped, used in audiences.py) — route the list access
    through client.as_list. CampaignId is optional on this WSDL operation (minOccurs=0);
    update_keyword_bid takes no campaign_id, so omit it."""
    ids = svc.factory.create("ns3:ArrayOflong")
    ids.long = [ad_group_id]
    r = svc.GetAdGroupsByIds(CampaignId=None, AdGroupIds=ids)
    ags = client.as_list(getattr(getattr(r, "AdGroups", None), "AdGroup", None))
    return client.effective_strategy(ags[0]) if ags else None


@mcp.tool()
def draft_keywords(ad_group_id: int, keywords: list[dict]) -> dict:
    """Draft new keywords under an ad group — created Paused. keywords:
    [{text, match_type: Broad|Phrase|Exact, bid: optional float}]. Blocklist
    (settings-configured blocked_terms) + bid cap (MS_ADS_MAX_CPC) enforced.

    Live-verified 2026-07-28 (one paused keyword added to a z. ad group)."""
    def check_policy():
        rails.check_content([k["text"] for k in keywords])
        for k in keywords:
            if k.get("bid") is not None:
                rails.check_bid(k["bid"])

    check_policy()
    preview_rows = [{"Text": k["text"], "MatchType": k["match_type"],
                     "Bid": k.get("bid"), "Status": "Paused"} for k in keywords]

    def apply():
        svc = client.svc("CampaignManagementService")
        kws = []
        for k in keywords:
            kw = client.blank(svc, "Keyword")
            kw.Text = k["text"]
            kw.MatchType = k["match_type"]
            kw.Status = "Paused"
            if k.get("bid") is not None:
                bid = client.blank(svc, "Bid")
                bid.Amount = k["bid"]
                kw.Bid = bid
            kws.append(kw)
        arr = svc.factory.create("ArrayOfKeyword")
        arr.Keyword = kws
        resp = svc.AddKeywords(AdGroupId=ad_group_id, Keywords=arr)
        ids = client.long_ids(getattr(resp, "KeywordIds", None))
        live = svc.GetKeywordsByAdGroupId(AdGroupId=ad_group_id)
        created = [k for k in client.as_list(getattr(live, "Keyword", None)) if k.Id in set(ids)]
        all_paused = bool(created) and all(k.Status == "Paused" for k in created)
        return {"keyword_ids": ids, "partial_errors": client.partial_errors(resp),
                "verify": {"verified": all_paused, "created_count": len(created)}}

    return rails.create_draft("draft_keywords",
                              {"ad_group_id": ad_group_id, "keywords": preview_rows}, apply,
                              validate_fn=check_policy)


@mcp.tool()
def update_keyword_bid(ad_group_id: int, keyword_id: int, bid: float) -> dict:
    """Draft a keyword CPC bid change (MS_ADS_MAX_CPC cap enforced). Allowed ONLY when the ad
    group's effective bid strategy is in the MANUAL_BIDDING allowlist (rails.py).
    Pre-rejected for Smart Bidding: UpdateKeywords silently ignores Bid there instead of
    erroring (live-verified — empty PartialErrors, bid unchanged on read-back), so this
    must be caught before the call, not after. Also rejected, fail closed, for any other
    unrecognized strategy or when the effective strategy can't be determined at all.

    NOT live-verified for a landed bid change — the manual-bidding write path has
    never run live (no manual-bidding ad group exists on the development account)."""
    svc = client.svc("CampaignManagementService")

    def check_policy(strategy):
        rails.check_bid(bid)
        rails.check_keyword_bid_allowed(strategy)

    strategy = _ad_group_strategy(svc, ad_group_id)
    check_policy(strategy)

    def apply():
        b = client.blank(svc, "Bid")
        b.Amount = bid
        kw = client.blank(svc, "Keyword")
        kw.Id = keyword_id
        kw.Bid = b
        arr = svc.factory.create("ArrayOfKeyword")
        arr.Keyword = [kw]
        resp = svc.UpdateKeywords(AdGroupId=ad_group_id, Keywords=arr)
        return {"partial_errors": client.partial_errors(resp),
                "verify": verify.verify_fields(lambda: _fetch_keyword(svc, ad_group_id, keyword_id),
                                               {"Bid.Amount": float(bid)})}

    return rails.create_draft("update_keyword_bid",
                              {"ad_group_id": ad_group_id, "keyword_id": keyword_id, "bid": bid,
                               "effective_strategy": strategy}, apply,
                              validate_fn=lambda: check_policy(_ad_group_strategy(svc, ad_group_id)))


@mcp.tool()
def remove_keywords(ad_group_id: int, keyword_ids: list[int]) -> dict:
    """Draft keyword deletion (irreversible — pause_entity is the reversible lever).

    Live-verified 2026-07-28."""
    def apply():
        svc = client.svc("CampaignManagementService")
        ids = svc.factory.create("ns3:ArrayOflong")
        ids.long = keyword_ids
        resp = svc.DeleteKeywords(AdGroupId=ad_group_id, KeywordIds=ids)
        return {"deleted": len(keyword_ids), "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("remove_keywords",
                              {"ad_group_id": ad_group_id, "keyword_ids": keyword_ids,
                               "warning": "DELETE is irreversible; pause_entity is the reversible lever"},
                              apply)
