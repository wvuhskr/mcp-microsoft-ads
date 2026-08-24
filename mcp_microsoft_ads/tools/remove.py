"""remove_entity: permanent delete of campaign | ad_group | keyword | ad.
Irreversible — pause_entity is the reversible lever. Only tool that needs
plain ArrayOflong id arrays; no business-object payload, so client.blank()
doesn't apply here."""
from .. import client, rails
from ..app import mcp

ALL_TYPES = "Search Shopping DynamicSearchAds Audience PerformanceMax"
_NEEDS_PARENT = {"ad_group": "CampaignId", "keyword": "AdGroupId", "ad": "AdGroupId"}
_DELETE = {
    "campaign": ("DeleteCampaigns", "CampaignIds"),
    "ad_group": ("DeleteAdGroups", "AdGroupIds"),
    "keyword": ("DeleteKeywords", "KeywordIds"),
    "ad": ("DeleteAds", "AdIds"),
}


def _ids_array(svc, entity_id: int):
    ids = svc.factory.create("ns3:ArrayOflong")  # live-verified: ns4 raises TypeNotFound
    ids.long = [entity_id]
    return ids


@mcp.tool()
def remove_entity(entity_type: str, entity_id: int, parent_id: int | None = None) -> dict:
    """Draft PERMANENT deletion of campaign | ad_group | keyword | ad. Irreversible —
    prefer pause_entity. ad_group needs parent_id=campaign id; keyword/ad need
    parent_id=ad group id.

    Live-verified: ad branch 2026-07-28, campaign/keyword branches 2026-07-30.
    NOT live-verified: ad_group branch (fixed by analogy only)."""
    if entity_type not in _DELETE:
        raise ValueError("entity_type must be campaign | ad_group | keyword | ad")
    if entity_type in _NEEDS_PARENT and parent_id is None:
        raise ValueError(f"parent_id required for {entity_type}")
    svc = client.svc("CampaignManagementService")

    name = str(entity_id)
    if entity_type == "campaign":
        resp = svc.GetCampaignsByIds(AccountId=client.account_id(), CampaignIds=_ids_array(svc, entity_id),
                                      CampaignType=ALL_TYPES)
        camp = client.as_list(getattr(getattr(resp, "Campaigns", None), "Campaign", None))[0]
        name = camp.Name

    def apply():
        method_name, ids_kwarg = _DELETE[entity_type]
        method = getattr(svc, method_name)
        kwargs = {ids_kwarg: _ids_array(svc, entity_id)}
        if entity_type == "campaign":
            kwargs["AccountId"] = client.account_id()
        else:
            kwargs[_NEEDS_PARENT[entity_type]] = parent_id
        resp = method(**kwargs)
        return {"deleted": f"{entity_type} {entity_id}", "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("remove_entity",
                              {"entity_type": entity_type, "entity_id": entity_id, "name": name,
                               "warning": "IRREVERSIBLE delete — pause_entity is the reversible lever"},
                              apply)
