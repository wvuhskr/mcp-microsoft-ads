from .. import client
from ..app import mcp
from ..util import suds_to_dict


@mcp.tool()
def get_negative_keywords(campaign_id: int | None = None) -> dict:
    """Campaign-level negatives + ALL shared negative keyword lists (with items and
    campaign associations). Always call before recommending/adding negatives."""
    svc = client.svc("CampaignManagementService")
    camp_negs = {}
    if campaign_id is not None:
        ids = svc.factory.create("ns3:ArrayOflong")
        ids.long = [campaign_id]
        r = svc.GetNegativeKeywordsByEntityIds(EntityIds=ids, EntityType="Campaign")
        for enk in client.as_list(getattr(getattr(r, "EntityNegativeKeywords", None), "EntityNegativeKeyword", None)):
            kws = client.as_list(getattr(getattr(enk, "NegativeKeywords", None), "NegativeKeyword", None))
            camp_negs[str(enk.EntityId)] = [suds_to_dict(k) for k in kws]

    lists_resp = svc.GetSharedEntitiesByAccountId(SharedEntityType="NegativeKeywordList")
    # live-probed: response is ArrayOfSharedEntity itself, no SharedEntities wrapper
    shared = client.as_list(getattr(lists_resp, "SharedEntity", None))
    out_lists = []
    for sl in shared:
        sl_ref = svc.factory.create("NegativeKeywordList")
        sl_ref.Id = sl.Id
        items = svc.GetListItemsBySharedList(SharedList=sl_ref)
        # live-probed: response is ArrayOfSharedListItem itself, no ListItems wrapper
        kws = client.as_list(getattr(items, "SharedListItem", None))
        ids = svc.factory.create("ns3:ArrayOflong")
        ids.long = [sl.Id]
        # live-probed: SharedEntityType is a required param, missing it faults the call
        assoc = svc.GetSharedEntityAssociationsBySharedEntityIds(
            EntityType="Campaign", SharedEntityIds=ids, SharedEntityType="NegativeKeywordList")
        assoc_rows = client.as_list(getattr(getattr(assoc, "Associations", None), "SharedEntityAssociation", None))
        out_lists.append({"id": sl.Id, "name": sl.Name,
                          "associated_campaign_ids": [a.EntityId for a in assoc_rows],
                          "keywords": [suds_to_dict(k) for k in kws]})
    return {"campaign_negatives": camp_negs, "shared_lists": out_lists}
