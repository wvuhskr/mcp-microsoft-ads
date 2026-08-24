"""Ad extension writes: sitelinks, callouts, structured snippets, removal.
Add-then-associate pattern: AddAdExtensions (library) -> SetAdExtensionsAssociations
(campaign) inside one apply_fn; abort association if the library add failed."""
from .. import client, rails
from ..app import mcp


def _add_and_associate(tool: str, preview: dict, build_extensions):
    """Shared apply pipeline: AddAdExtensions -> SetAdExtensionsAssociations (campaign)."""
    campaign_id = preview["campaign_id"]

    def apply():
        svc = client.svc("CampaignManagementService")
        arr = svc.factory.create("ArrayOfAdExtension")  # business type, resolves bare
        arr.AdExtension = build_extensions(svc)
        resp = svc.AddAdExtensions(AccountId=client.account_id(), AdExtensions=arr)
        errs = client.partial_errors(resp)
        idents = client.as_list(getattr(getattr(resp, "AdExtensionIdentities", None),
                                        "AdExtensionIdentity", None))
        ext_ids = [int(i.Id) for i in idents if getattr(i, "Id", None)]
        if errs or not ext_ids:
            return {"extension_ids": ext_ids, "partial_errors": errs,
                    "aborted": "library add failed; no association attempted"}

        # Widened try (reviewer finding 2): payload BUILDING (the assocs loop/array
        # creation), not just the SOAP call, must be inside the guard -- a raise while
        # building the associations after the library add already landed is the exact
        # failure class this closes. Loop var renamed ext_id (reviewer finding 4 --
        # was `e`, shadowing the `except Exception as e` below).
        try:
            assocs = []
            for ext_id in ext_ids:
                # AdExtensionIdToEntityIdAssociation has no Type discriminator (checked WSDL).
                a = client.blank(svc, "AdExtensionIdToEntityIdAssociation")
                a.AdExtensionId = ext_id
                a.EntityId = campaign_id
                assocs.append(a)
            assoc_arr = svc.factory.create("ArrayOfAdExtensionIdToEntityIdAssociation")
            assoc_arr.AdExtensionIdToEntityIdAssociation = assocs
            resp2 = svc.SetAdExtensionsAssociations(
                AccountId=client.account_id(),
                AdExtensionIdToEntityIdAssociations=assoc_arr,
                AssociationType="Campaign")
        except Exception as e:
            # Step 1 (AddAdExtensions) landed -- carry the library ids so a mid-apply
            # exception here doesn't erase them (plan item 11).
            raise rails.PartialWriteError(
                "association failed after extension library add landed",
                partial={"extension_ids": ext_ids, "associated": False}) from e
        return {"extension_ids": ext_ids, "associated_campaign_id": campaign_id,
                "partial_errors": errs + client.partial_errors(resp2)}

    return rails.create_draft(tool, preview, apply)


@mcp.tool()
def draft_sitelinks(campaign_id: int, sitelinks: list[dict]) -> dict:
    """Draft sitelinks on a campaign. sitelinks: [{text, url, description1?, description2?}].
    Copy claims must be site-verified (the advertiser's website).

    Live-verified 2026-07-30.

    On a mid-apply failure (association raises after the library add landed) the
    error carries every landed ID (extension_ids) — reconcile account state before
    retrying (a retry re-runs ALL steps, including re-adding the extensions)."""
    rails.check_content([s["text"] for s in sitelinks] +
                        [s.get("description1") for s in sitelinks] +
                        [s.get("description2") for s in sitelinks])

    def build(svc):
        out = []
        for s in sitelinks:
            e = client.blank(svc, "SitelinkAdExtension")
            e.Type = "SitelinkAdExtension"  # discriminator (AdExtension.Type, inherited)
            e.DisplayText = s["text"]
            # ArrayOfstring lives in the Serialization/Arrays namespace, same as
            # ArrayOflong which needs the ns3: prefix live (Task 15-18) — live-proven
            # 2026-07-30 when this draft_sitelinks apply ran green (see docstring above).
            urls = svc.factory.create("ns3:ArrayOfstring")
            urls.string = [s["url"]]
            e.FinalUrls = urls
            e.Description1 = s.get("description1")
            e.Description2 = s.get("description2")
            out.append(e)
        return out

    return _add_and_associate("draft_sitelinks",
                              {"campaign_id": campaign_id, "sitelinks": sitelinks}, build)


@mcp.tool()
def create_callouts(campaign_id: int, texts: list[str]) -> dict:
    """Draft callout extensions (max 25 chars each).

    Live-verified 2026-07-30.

    On a mid-apply failure (association raises after the library add landed) the
    error carries every landed ID (extension_ids) — reconcile account state before
    retrying (a retry re-runs ALL steps, including re-adding the extensions)."""
    rails.check_content(texts)
    for t in texts:
        if len(t) > 25:
            raise ValueError(f"callout '{t}' exceeds 25 chars")

    def build(svc):
        out = []
        for t in texts:
            e = client.blank(svc, "CalloutAdExtension")
            e.Type = "CalloutAdExtension"
            e.Text = t
            out.append(e)
        return out

    return _add_and_associate("create_callouts", {"campaign_id": campaign_id, "texts": texts}, build)


@mcp.tool()
def create_structured_snippets(campaign_id: int, header: str, values: list[str]) -> dict:
    """Draft a structured snippet (header must be an MS-supported header, e.g. 'Services';
    3+ values required).

    Live-verified 2026-07-30.

    On a mid-apply failure (association raises after the library add landed) the
    error carries every landed ID (extension_ids) — reconcile account state before
    retrying (a retry re-runs ALL steps, including re-adding the extension)."""
    rails.check_content(values)
    if len(values) < 3:
        raise ValueError("structured snippets need >= 3 values")

    def build(svc):
        e = client.blank(svc, "StructuredSnippetAdExtension")
        e.Type = "StructuredSnippetAdExtension"
        e.Header = header
        vals = svc.factory.create("ns3:ArrayOfstring")  # see draft_sitelinks note
        vals.string = values
        e.Values = vals
        return [e]

    return _add_and_associate("create_structured_snippets",
                              {"campaign_id": campaign_id, "header": header, "values": values}, build)


@mcp.tool()
def remove_extension(extension_id: int, campaign_id: int) -> dict:
    """Draft removal: disassociate from campaign, then delete from library.

    Live-verified 2026-07-30.

    If the disassociation itself (step 1) comes back with a batch error, nothing
    landed — the apply aborts and the delete is never attempted (aborted=... in the
    result), nothing to reconcile.

    On a mid-apply failure (delete raises after disassociation landed) the error
    carries what landed (disassociated=True, deleted=False) — reconcile account state
    before retrying (a retry re-runs ALL steps, including re-disassociating)."""
    def apply():
        svc = client.svc("CampaignManagementService")
        assoc = client.blank(svc, "AdExtensionIdToEntityIdAssociation")
        assoc.AdExtensionId = extension_id
        assoc.EntityId = campaign_id
        assoc_arr = svc.factory.create("ArrayOfAdExtensionIdToEntityIdAssociation")
        assoc_arr.AdExtensionIdToEntityIdAssociation = [assoc]
        r1 = svc.DeleteAdExtensionsAssociations(
            AccountId=client.account_id(),
            AdExtensionIdToEntityIdAssociations=assoc_arr,
            AssociationType="Campaign")
        errs1 = client.partial_errors(r1)
        if errs1:
            # Step 1 (disassociation) itself came back with a batch PartialError --
            # nothing landed, so abort raw before ever attempting the delete (mirrors
            # _add_and_associate's "library add failed" abort and schedule.py's
            # delete-batch abort: a step-1 failure aborts, it does not raise
            # PartialWriteError, since there is nothing to reconcile).
            return {"partial_errors": errs1,
                    "aborted": "disassociation failed; no deletion attempted",
                    "extension_id": extension_id, "campaign_id": campaign_id}

        ids = svc.factory.create("ns3:ArrayOflong")  # live-verified prefix (Task 15-18)
        ids.long = [extension_id]
        try:
            r2 = svc.DeleteAdExtensions(AccountId=client.account_id(), AdExtensionIds=ids)
        except Exception as e:
            # Step 1 (DeleteAdExtensionsAssociations) landed -- disassociation happened
            # even though deletion did not (plan item 11 remove_extension nuance).
            raise rails.PartialWriteError(
                "extension delete failed after disassociation landed",
                partial={"disassociated": True, "deleted": False,
                         "extension_id": extension_id, "campaign_id": campaign_id}) from e
        return {"partial_errors": errs1 + client.partial_errors(r2)}

    return rails.create_draft("remove_extension",
                              {"extension_id": extension_id, "campaign_id": campaign_id}, apply)
