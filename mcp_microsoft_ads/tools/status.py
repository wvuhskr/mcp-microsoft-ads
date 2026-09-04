"""pause_entity / enable_entity — minimal status flips for campaign | ad_group | keyword | ad."""
from types import SimpleNamespace

from .. import client, rails, verify
from ..app import mcp
from .adgroups_write import _fetch_ad_group
from .campaigns_write import _fetch_campaign

_NEEDS_PARENT = {"keyword", "ad"}
_VALID = {"campaign", "ad_group", "keyword", "ad"}


def _flip(entity_type: str, entity_id: int, parent_id, new_status: str) -> dict:
    if entity_type not in _VALID:
        raise ValueError(f"entity_type must be one of {sorted(_VALID)}")
    if entity_type in _NEEDS_PARENT and parent_id is None:
        raise ValueError(f"parent_id (ad group id) required for entity_type '{entity_type}'")
    svc = client.svc("CampaignManagementService")

    # live-verified (Task 15): bare SimpleNamespace/dict Update payloads fail server-side
    # deserialization — build via client.blank() + the real ArrayOf* container. See client.blank.
    if entity_type == "campaign":
        cur = _fetch_campaign(entity_id)
        def do():
            upd = client.blank(svc, "Campaign")
            upd.Id = entity_id
            upd.Status = new_status
            arr = svc.factory.create("ArrayOfCampaign")
            arr.Campaign = [upd]
            return svc.UpdateCampaigns(AccountId=client.account_id(), Campaigns=arr)
        def readback():
            return _fetch_campaign(entity_id)
    elif entity_type == "ad_group":
        if parent_id is None:
            raise ValueError("parent_id (campaign id) required for ad_group status flip")
        cur = _fetch_ad_group(entity_id, parent_id)
        def do():
            upd = client.blank(svc, "AdGroup")
            upd.Id = entity_id
            upd.Status = new_status
            arr = svc.factory.create("ArrayOfAdGroup")
            arr.AdGroup = [upd]
            return svc.UpdateAdGroups(CampaignId=parent_id, AdGroups=arr)
        def readback():
            return _fetch_ad_group(entity_id, parent_id)
    elif entity_type == "keyword":
        cur = SimpleNamespace(Name=f"keyword {entity_id}", Status="(not fetched)")
        def do():
            upd = client.blank(svc, "Keyword")
            upd.Id = entity_id
            upd.Status = new_status
            arr = svc.factory.create("ArrayOfKeyword")
            arr.Keyword = [upd]
            return svc.UpdateKeywords(AdGroupId=parent_id, Keywords=arr)
        def readback():
            r = svc.GetKeywordsByAdGroupId(AdGroupId=parent_id)
            return next(k for k in client.as_list(getattr(r, "Keyword", None)) if k.Id == entity_id)
    else:  # ad — Ads support Active/Paused via UpdateAds Status
        cur = SimpleNamespace(Name=f"ad {entity_id}", Status="(not fetched)")
        def do():
            upd = client.blank(svc, "Ad")
            upd.Id = entity_id
            upd.Status = new_status
            arr = svc.factory.create("ArrayOfAd")
            arr.Ad = [upd]
            return svc.UpdateAds(AdGroupId=parent_id, Ads=arr)
        def readback():
            types = svc.factory.create("ArrayOfAdType")
            types.AdType = ["ResponsiveSearch", "ExpandedText", "DynamicSearch", "Text"]
            r = svc.GetAdsByAdGroupId(AdGroupId=parent_id, AdTypes=types)
            return next(a for a in client.as_list(getattr(r, "Ad", None)) if a.Id == entity_id)

    def apply():
        resp = do()
        return {"partial_errors": client.partial_errors(resp),
                "verify": verify.verify_fields(readback, {"Status": new_status})}

    name = getattr(cur, "Name", str(entity_id))
    before = getattr(cur, "Status", None)
    return rails.create_draft(f"{'pause' if new_status == 'Paused' else 'enable'}_entity",
                              {"entity_type": entity_type, "entity_id": entity_id, "name": name,
                               "changes": {"Status": {"before": before, "after": new_status}}}, apply)


@mcp.tool()
def pause_entity(entity_type: str, entity_id: int, parent_id: int | None = None) -> dict:
    """Draft a pause. entity_type: campaign | ad_group | keyword | ad. keyword/ad need
    parent_id = ad group id; ad_group needs parent_id = campaign id.

    Live-verified 2026-07-28: ad_group branch (Paused→Active→Paused on a z. ad group,
    read-back verified). NOT live-verified: campaign/ad/keyword branches (fixed by
    analogy only)."""
    return _flip(entity_type, entity_id, parent_id, "Paused")


@mcp.tool()
def enable_entity(entity_type: str, entity_id: int, parent_id: int | None = None) -> dict:
    """Draft an enable (Status=Active). Same shape as pause_entity.

    Live-verified 2026-07-28: ad_group branch (same probe as pause_entity).
    NOT live-verified: campaign/ad/keyword branches (fixed by analogy only)."""
    return _flip(entity_type, entity_id, parent_id, "Active")
