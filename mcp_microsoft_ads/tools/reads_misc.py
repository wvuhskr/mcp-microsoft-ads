from .. import client
from ..app import mcp
from ..util import suds_to_dict
from .entities import ALL_AD_TYPES

_EXT_TYPES = ("SitelinkAdExtension CalloutAdExtension StructuredSnippetAdExtension "
              "CallAdExtension ImageAdExtension")
# live-probed: this account's customer does not have the In-Store Transaction pilot enabled;
# including it faults the WHOLE call (InStoreTransactionPilotNotEnabledForCustomer), not just a
# partial error. AppDownload omitted too — untested live, same pilot-gating risk.
_GOAL_TYPES = "Url Duration PagesViewedPerVisit Event AppInstall OfflineConversion"


def _extensions_by_ids(svc, id_list: list[int]) -> list:
    """GetAdExtensionsByIds caps at 100 ids/call — batch in chunks of 100, concatenate."""
    out = []
    for i in range(0, len(id_list), 100):
        arr = svc.factory.create("ns3:ArrayOflong")
        arr.long = id_list[i:i + 100]
        ext_resp = svc.GetAdExtensionsByIds(AccountId=client.account_id(), AdExtensionIds=arr,
                                            AdExtensionType=_EXT_TYPES)
        out.extend(client.as_list(getattr(getattr(ext_resp, "AdExtensions", None), "AdExtension", None)))
    return out


@mcp.tool()
def list_extensions(campaign_id: int | None = None) -> dict:
    """Ad extensions. campaign_id=None: full account library (sitelinks, callouts, structured
    snippets...). campaign_id set: only extensions associated with that campaign."""
    svc = client.svc("CampaignManagementService")

    if campaign_id is not None:
        ids = svc.factory.create("ns3:ArrayOflong")
        ids.long = [campaign_id]
        assoc_resp = svc.GetAdExtensionsAssociations(AccountId=client.account_id(),
            AdExtensionType=_EXT_TYPES, AssociationType="Campaign", EntityIds=ids)
        # Live-probed: the response field AdExtensionAssociationCollection is itself an
        # ArrayOfAdExtensionAssociationCollection wrapper whose own list field is (confusingly)
        # ALSO named AdExtensionAssociationCollection — double nesting, confirmed live, matches
        # the brief's original fake. Each entry then has .AdExtensionAssociations.AdExtensionAssociation.
        outer = getattr(assoc_resp, "AdExtensionAssociationCollection", None)
        collections = client.as_list(getattr(outer, "AdExtensionAssociationCollection", None))
        exts = []
        for coll in collections:
            assocs = client.as_list(getattr(getattr(coll, "AdExtensionAssociations", None),
                                            "AdExtensionAssociation", None))
            exts.extend(a.AdExtension for a in assocs if getattr(a, "AdExtension", None) is not None)
        return {"extensions": [suds_to_dict(e) for e in exts]}

    ids_resp = svc.GetAdExtensionIdsByAccountId(AccountId=client.account_id(),
                                                AssociationType=None, AdExtensionType=_EXT_TYPES)
    # live-probed: response is the ArrayOflong itself (r.long), no AdExtensionIds wrapper level
    # — same unwrapping suds does for GetSharedEntitiesByAccountId in negatives_read.py.
    id_list = [int(x) for x in client.as_list(getattr(ids_resp, "long", None))]
    if not id_list:
        return {"extensions": []}
    return {"extensions": [suds_to_dict(e) for e in _extensions_by_ids(svc, id_list)]}


@mcp.tool()
def get_conversion_actions() -> dict:
    """Conversion goals (nil ids returns all goals of the given types)."""
    svc = client.svc("CampaignManagementService")
    r = svc.GetConversionGoalsByIds(ConversionGoalIds=None, ConversionGoalTypes=_GOAL_TYPES)
    goals = client.as_list(getattr(getattr(r, "ConversionGoals", None), "ConversionGoal", None))
    return {"goals": [suds_to_dict(g) for g in goals if g is not None]}


@mcp.tool()
def get_policy_issues(ad_group_id: int) -> dict:
    """Disapproved ads + keywords in an ad group (editorial review failures)."""
    svc = client.svc("CampaignManagementService")
    # live-probed: AdTypes is declared optional (minOccurs=0) in the WSDL but the live API
    # faults without it (CampaignServiceAdTypeInvalid: "AdTypes are required").
    ad_types = svc.factory.create("ArrayOfAdType")
    ad_types.AdType = ALL_AD_TYPES
    ads = svc.GetAdsByEditorialStatus(AdGroupId=ad_group_id, EditorialStatus="Disapproved",
                                      AdTypes=ad_types)
    kws = svc.GetKeywordsByEditorialStatus(AdGroupId=ad_group_id, EditorialStatus="Disapproved")
    # GetAdsByEditorialStatusResponse/GetKeywordsByEditorialStatusResponse each declare
    # exactly one child (Ads/Keywords) so suds strips that wrapper level (one-child-response
    # rule, see client.partial_errors docstring) — ads/kws ARE the ArrayOfAd/ArrayOfKeyword
    # themselves, not wrapped in a named Ads/Keywords field.
    disapproved_ads = client.as_list(getattr(ads, "Ad", None))
    disapproved_keywords = client.as_list(getattr(kws, "Keyword", None))
    return {"disapproved_ads": [suds_to_dict(a) for a in disapproved_ads],
            "disapproved_keywords": [suds_to_dict(k) for k in disapproved_keywords]}
