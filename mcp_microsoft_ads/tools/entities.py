from .. import client
from ..app import mcp
from ..util import suds_to_dict

ALL_CAMPAIGN_TYPES = "Search Shopping DynamicSearchAds Audience PerformanceMax"
ALL_AD_TYPES = ["ResponsiveSearch", "ExpandedText", "DynamicSearch", "ResponsiveAd", "Text", "Product"]

_NEEDS_PARENT = {"ad_groups", "keywords", "ads", "asset_groups", "campaign_criterions"}


def _fetch(entity: str, parent_id, ids):
    svc = client.svc("CampaignManagementService")
    if entity == "campaigns":
        r = svc.GetCampaignsByAccountId(AccountId=client.account_id(), CampaignType=ALL_CAMPAIGN_TYPES)
        return client.as_list(getattr(r, "Campaign", None))
    if entity == "ad_groups":
        r = svc.GetAdGroupsByCampaignId(CampaignId=parent_id)
        return client.as_list(getattr(r, "AdGroup", None))
    if entity == "keywords":
        r = svc.GetKeywordsByAdGroupId(AdGroupId=parent_id)
        return client.as_list(getattr(r, "Keyword", None))
    if entity == "ads":
        types = svc.factory.create("ArrayOfAdType")
        types.AdType = ALL_AD_TYPES
        r = svc.GetAdsByAdGroupId(AdGroupId=parent_id, AdTypes=types)
        return client.as_list(getattr(r, "Ad", None))
    if entity == "asset_groups":
        r = svc.GetAssetGroupsByCampaignId(CampaignId=parent_id)
        return client.as_list(getattr(getattr(r, "AssetGroups", None), "AssetGroup", None))
    if entity == "campaign_criterions":
        crit_ids = None
        if ids:
            crit_ids = svc.factory.create("ns3:ArrayOflong")
            crit_ids.long = ids
        r = svc.GetCampaignCriterionsByIds(CampaignId=parent_id, CampaignCriterionIds=crit_ids,
                                           CriterionType="DayTime Location LocationIntent Radius")
        return client.as_list(getattr(getattr(r, "CampaignCriterions", None), "CampaignCriterion", None))
    raise ValueError(f"unknown entity '{entity}'")


@mcp.tool()
def get_entities(entity: str, parent_id: int | None = None, ids: list[int] | None = None) -> dict:
    """Fetch entity state. entity: campaigns | ad_groups | keywords | ads | asset_groups |
    campaign_criterions. parent_id required for all but campaigns. campaign_criterions returns
    DayTime, Location, LocationIntent, Radius criterion types (Audience not readable via this API).
    NOTE: campaign BiddingScheme reads as None on MS — effective strategy lives on ad_groups
    (BiddingScheme.InheritedBidStrategyType)."""
    if entity in _NEEDS_PARENT and parent_id is None:
        raise ValueError(f"parent_id required for entity '{entity}'")
    rows = _fetch(entity, parent_id, ids)
    if ids and entity != "campaign_criterions":
        rows = [r for r in rows if getattr(r, "Id", None) in set(ids)]
    return {"entity": entity, "count": len(rows), "entities": [suds_to_dict(r) for r in rows]}
