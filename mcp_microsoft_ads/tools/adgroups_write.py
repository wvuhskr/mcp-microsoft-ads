from .. import client, rails, verify
from ..app import mcp


def _fetch_ad_group(ad_group_id: int, campaign_id: int):
    svc = client.svc("CampaignManagementService")
    ids = svc.factory.create("ns3:ArrayOflong")  # live-verified: ns4 raises TypeNotFound (Session 2)
    ids.long = [ad_group_id]
    r = svc.GetAdGroupsByIds(CampaignId=campaign_id, AdGroupIds=ids)
    ags = client.as_list(getattr(getattr(r, "AdGroups", None), "AdGroup", None))
    if not ags:
        raise ValueError(f"ad group {ad_group_id} not found in campaign {campaign_id}")
    return ags[0]


@mcp.tool()
def update_ad_group(ad_group_id: int, campaign_id: int, status: str | None = None,
                    cpc_bid: float | None = None, target_cpa: float | None = None) -> dict:
    """Draft an ad-group update. target_cpa sets an explicit MaxConversions tCPA on the
    ad group (MS Search campaigns on the development account run MaxConversions with no
    target — this INTRODUCES one). cpc_bid is allowed ONLY when the ad group's effective
    bid strategy is in the MANUAL_BIDDING allowlist (rails.py) — same fail-closed guard
    as update_keyword_bid; MS silently ignores fixed bids under Smart Bidding. Returns
    draft; apply with confirm_and_apply (policy rails re-checked at apply).

    NOT live-verified through this tool itself. The underlying blank()-built UpdateAdGroups
    call IS live-proven (2026-07-28, Status flip on a z. ad group via pause/enable_entity);
    the cpc_bid / target_cpa branches have never run live."""
    if status is None and cpc_bid is None and target_cpa is None:
        raise ValueError("nothing to change")

    def check_policy(strategy):
        # target_cpa deliberately NOT strategy-gated: tCPA is an allowed lever ON
        # Smart Bidding (it's what the guard's own refusal message points people to)
        if cpc_bid is not None:
            rails.check_bid(cpc_bid)
            rails.check_manual_bid_allowed(strategy, "ad-group CpcBid change")
        if target_cpa is not None:
            rails.check_bid(target_cpa)

    current = _fetch_ad_group(ad_group_id, campaign_id)
    strategy = client.effective_strategy(current)
    check_policy(strategy)

    changes = {}
    if status is not None:
        changes["Status"] = {"before": current.Status, "after": status}
    if cpc_bid is not None:
        before = getattr(getattr(current, "CpcBid", None), "Amount", None)
        changes["CpcBid"] = {"before": before, "after": cpc_bid}
    if target_cpa is not None:
        changes["TargetCpa"] = {"before": getattr(getattr(current, "BiddingScheme", None), "TargetCpa", None),
                                "after": target_cpa}

    def apply():
        svc = client.svc("CampaignManagementService")
        upd = client.blank(svc, "AdGroup")  # live-verified: bare SimpleNamespace/dict fails (see client.blank)
        upd.Id = ad_group_id
        expect = {}
        if status is not None:
            upd.Status = status
            expect["Status"] = status
        if cpc_bid is not None:
            bid = client.blank(svc, "Bid")
            bid.Amount = cpc_bid
            upd.CpcBid = bid
            expect["CpcBid.Amount"] = float(cpc_bid)
        if target_cpa is not None:
            scheme = client.blank(svc, "MaxConversionsBiddingScheme")
            scheme.Type = "MaxConversions"
            scheme.TargetCpa = target_cpa
            upd.BiddingScheme = scheme
            expect["BiddingScheme.TargetCpa"] = float(target_cpa)
        arr = svc.factory.create("ArrayOfAdGroup")
        arr.AdGroup = [upd]
        resp = svc.UpdateAdGroups(CampaignId=campaign_id, AdGroups=arr)
        return {"partial_errors": client.partial_errors(resp),
                "verify": verify.verify_fields(lambda: _fetch_ad_group(ad_group_id, campaign_id), expect)}

    return rails.create_draft("update_ad_group",
                              {"ad_group": current.Name, "ad_group_id": ad_group_id,
                               "effective_strategy": strategy, "changes": changes}, apply,
                              validate_fn=lambda: check_policy(
                                  client.effective_strategy(_fetch_ad_group(ad_group_id, campaign_id))))
