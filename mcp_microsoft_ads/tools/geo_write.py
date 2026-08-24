"""Geo-target writes: exclude a location (NegativeCampaignCriterion wrapping
LocationCriterion) and remove any Location criterion by id. Mirrors schedule.py's
Add/DeleteCampaignCriterions pattern; CriterionType="Targets" on both calls (WSDL's
CampaignCriterionType enum also lists a standalone "Location" flag, but per the
binding deviation for this task — and matching schedule.py's own Add-side usage —
"Targets" is what Add/DeleteCampaignCriterions expects for location criterions)."""
from .. import client, rails
from ..app import mcp


@mcp.tool()
def exclude_geo_target(campaign_id: int, location_id: int) -> dict:
    """Draft a location EXCLUSION on a campaign. location_id from search_geo_targets.

    Live-verified 2026-07-30."""
    svc = client.svc("CampaignManagementService")

    def apply():
        crit = client.blank(svc, "LocationCriterion")
        crit.Type = "LocationCriterion"  # discriminator (client.blank live lesson)
        crit.LocationId = location_id
        cc = client.blank(svc, "NegativeCampaignCriterion")
        cc.Type = "NegativeCampaignCriterion"  # wrapper also needs it re-set (Task 16 lesson)
        cc.CampaignId = campaign_id
        cc.Criterion = crit
        arr = svc.factory.create("ArrayOfCampaignCriterion")
        arr.CampaignCriterion = [cc]
        resp = svc.AddCampaignCriterions(CampaignCriterions=arr, CriterionType="Targets")
        ids = client.long_ids(getattr(resp, "CampaignCriterionIds", None))
        return {"criterion_ids": ids, "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("exclude_geo_target",
                              {"campaign_id": campaign_id, "location_id": location_id,
                               "action": "EXCLUDE location"}, apply)


@mcp.tool()
def remove_geo_target(campaign_id: int, criterion_id: int) -> dict:
    """Draft removal of a campaign criterion by id, via DeleteCampaignCriterions with
    CriterionType="Targets" — the Targets group covers Location, DayTime, Radius, and
    other campaign-criterion types, so this deletes whatever criterion id is passed,
    not Location only. ids from get_entities('campaign_criterions', parent_id=campaign_id).

    Live-verified 2026-07-30."""
    svc = client.svc("CampaignManagementService")

    def apply():
        ids = svc.factory.create("ns3:ArrayOflong")  # live-verified: bare/ns4 raise TypeNotFound
        ids.long = [criterion_id]
        resp = svc.DeleteCampaignCriterions(CampaignCriterionIds=ids, CampaignId=campaign_id,
                                            CriterionType="Targets")
        return {"deleted_criterion_id": criterion_id, "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("remove_geo_target",
                              {"campaign_id": campaign_id, "criterion_id": criterion_id}, apply)
