"""Performance Max campaign + portfolio bid strategy writes. All payloads are
client.blank()-built factory objects in real factory Array containers (binding
deviation 1) — bare SimpleNamespace/dict payloads fail suds enum serialization live.

WSDL probe findings (Session 7, campaignmanagement_service.xml):
- Campaign, ArrayOfCampaign, AssetGroup, ArrayOfAssetGroup, BidStrategy,
  ArrayOfBidStrategy, *BiddingScheme, TextAsset, ImageAsset, AssetLink,
  ArrayOfAssetLink all factory.create() BARE (no ns3: prefix) — live-probed.
- ArrayOfstring/ArrayOflong need the ns3: prefix (same as elsewhere in this repo).
- BiddingScheme.Type is xs:string (not a restricted enum) — but the live-verified
  value shape (Task 15/23 precedent) is the short form ("MaxConversions"), not the
  class name ("MaxConversionsBiddingScheme"); followed here by analogy for
  MaxClicks/TargetImpressionShare too.
- AssetGroup has NO Type field at all (WSDL-confirmed) — no discriminator to set.
- Asset (base of TextAsset/ImageAsset) has Id/Name/Type; ImageAsset adds
  CropHeight/CropWidth/CropX/CropY/CroppingType/SubType/TargetHeight/TargetWidth.
  There is no separate "MediaId" field — Id (inherited from Asset) is the only
  id-shaped field, so image_media_ids (from upload_image_asset/AddMedia) are set
  there. Whether Asset.Id and Media.Id share an id space is NOT verifiable from
  the WSDL alone (Media and Asset are distinct complexTypes) — flagged as an
  assumption for Session 8's live smoke test, not proven here.
- AddCampaigns/AddAssetGroups/AddBidStrategies responses carry only flat
  PartialErrors (no NestedPartialErrors in this schema) and their *Ids fields are
  ArrayOfNullableOflong rather than ArrayOflong — client.as_list handles both the
  same way (attribute `.long`, single-item-or-list).
"""
from .. import client, rails, settings
from ..app import mcp

STRATEGY_TYPES = {"MaxConversions", "MaxClicks", "TargetImpressionShare"}


def _string_array(svc, strings):
    arr = svc.factory.create("ns3:ArrayOfstring")
    arr.string = strings
    return arr


def _text_asset_links(svc, texts):
    links = []
    for t in texts:
        asset = client.blank(svc, "TextAsset")
        asset.Type = "TextAsset"  # inner discriminator (Asset.Type, inherited)
        asset.Text = t
        link = client.blank(svc, "AssetLink")
        link.Asset = asset
        links.append(link)
    arr = svc.factory.create("ArrayOfAssetLink")
    arr.AssetLink = links
    return arr


# asset.SubType must track the media item's own MediaType (live-probed against two real
# asset groups) — only these two relationships are evidenced.
# GenericImage/Image4x1 have no evidenced SubType; an id absent from the library has none
# either — both cases raise below rather than guess (guessing landscape is the defect this
# replaced).
_SUBTYPE_BY_MEDIA_TYPE = {"Image1x1": "SquareImageMedia", "Image191x100": "LandscapeImageMedia"}


def _image_asset_links(svc, media_ids):
    """GetMediaMetaDataByIds: exact-id lookup. Replaces the old account-wide
    GetMediaMetaDataByAccountId scan outright — by-ids needs no paging and doesn't care
    how large the account's media library grows, closing the previously-queued "add a
    paging loop" item without building it (owner-approved 2026-07-31).

    Shape probe-verified live 2026-07-31 by the controller (read-only, against the real
    account):
    - GetMediaMetaDataByIdsResponse declares TWO children (MediaMetaData, PartialErrors),
      so per the suds ONE-CHILD RULE (client.partial_errors docstring) the wrapper is
      KEPT: rows live at resp.MediaMetaData.MediaMetaData, routed through client.as_list.
      (Contrast the old by-account-id response: ONE child, so it came back stripped —
      resp WAS the ArrayOfMediaMetaData and rows sat at resp.MediaMetaData directly. The
      nesting genuinely flips between the two operations; the one-child rule predicts
      both.)
    - Rows come back positionally aligned with the requested ids; an id absent from the
      library yields a NIL (None) row at its index rather than being omitted — probed
      with [real, bogus, real] -> [<row>, None, <row>]. A bad id does not poison the
      good ones in the same batch.
    - MediaEnabledEntities has no equivalent on this call; it simply goes away.
    """
    id_arr = svc.factory.create("ns3:ArrayOflong")  # ns3: prefix required, live-proven (see module docstring)
    id_arr.long = media_ids
    resp = svc.GetMediaMetaDataByIds(MediaIds=id_arr, ReturnAdditionalFields=None)
    # MediaMetaData is nillable/minOccurs=0 at the response level (WSDL) — a batch where
    # every requested id is invalid can nil the whole element, not just individual rows.
    # Double-getattr (audiences.py precedent) turns that into the clean ValueError below
    # instead of a raw AttributeError.
    rows = client.as_list(getattr(getattr(resp, "MediaMetaData", None), "MediaMetaData", None))
    errs_by_index = {e["index"]: e["message"] for e in client.partial_errors(resp)}

    links = []
    for i, mid in enumerate(media_ids):
        row = rows[i] if i < len(rows) else None
        if row is None:
            suffix = f": {errs_by_index[i]}" if i in errs_by_index else ""
            raise ValueError(f"media id {mid} not found in the account media library "
                             f"(GetMediaMetaDataByIds){suffix}")
        sub_type = _SUBTYPE_BY_MEDIA_TYPE.get(row.MediaType)
        if sub_type is None:
            raise ValueError(f"media id {mid} has MediaType '{row.MediaType}', which has no evidenced "
                             f"SubType mapping (evidenced: {_SUBTYPE_BY_MEDIA_TYPE}) — refusing to guess")
        asset = client.blank(svc, "ImageAsset")
        asset.Type = "ImageAsset"
        asset.Id = mid
        asset.SubType = sub_type
        link = client.blank(svc, "AssetLink")
        link.Asset = asset
        links.append(link)
    arr = svc.factory.create("ArrayOfAssetLink")
    arr.AssetLink = links
    return arr


@mcp.tool()
def create_pmax_campaign(name: str, daily_budget: float, final_url: str,
                         headlines: list[str], long_headlines: list[str], descriptions: list[str],
                         business_name: str, image_media_ids: list[int],
                         target_cpa: float | None = None,
                         time_zone: str = "EasternTimeUSCanada") -> dict:
    """Draft a Performance Max campaign + first asset group — created PAUSED. time_zone
    defaults to EasternTimeUSCanada (US-centric default) — override for other markets.
    target_cpa is required by default (require_pmax_target_cpa setting, default true) —
    never create PMax without a target; removing one later explodes impressions (hard
    rule observed on the owner's Google Ads account). Set require_pmax_target_cpa
    false in advertiser settings to allow a no-target MaxConversions campaign instead.
    Images are REQUIRED (upload via upload_image_asset first). Each image_media_ids
    entry must already be in the account media library with MediaType Image1x1 or
    Image191x100; GenericImage, Image4x1, unknown types, and absent ids raise. Aborts
    the asset-group add if the campaign add fails. Returns a draft; apply with
    confirm_and_apply.

    Live-verified 2026-07-30 — with an explicit target_cpa: campaign + asset group
    created and read back with both images carrying the correct per-image SubType;
    ImageAsset.Id = media id write-proven.
    NOT live-verified — the require_pmax_target_cpa=false / no-target_cpa path: only
    exercised against fakes, never run against the live API through this tool.
    NOT live-verified with non-default time_zone values.

    On a mid-apply failure (asset group add raises after the campaign landed) the
    error carries every landed ID (campaign_ids) — reconcile account state before
    retrying (a retry re-runs ALL steps, including re-creating the campaign)."""
    if not image_media_ids:
        raise ValueError("image_media_ids required — PMax needs images; use upload_image_asset first")
    if target_cpa is None and settings.require_pmax_target_cpa():
        raise ValueError(
            "target_cpa required — never launch PMax without a target (require_pmax_target_cpa "
            "setting is true; set it to false in advertiser settings to allow a no-target campaign)")
    rails.check_budget(daily_budget)
    if target_cpa is not None:
        rails.check_bid(target_cpa)
    rails.check_content([name, business_name] + headlines + long_headlines + descriptions)

    def apply():
        svc = client.svc("CampaignManagementService")
        ag_images = _image_asset_links(svc, image_media_ids)  # validate before any write
        scheme = client.blank(svc, "MaxConversionsBiddingScheme")
        scheme.Type = "MaxConversions"
        if target_cpa is not None:
            scheme.TargetCpa = target_cpa

        camp = client.blank(svc, "Campaign")
        camp.Name = name
        camp.CampaignType = "PerformanceMax"
        camp.Status = "Paused"
        camp.DailyBudget = daily_budget
        camp.BudgetType = "DailyBudgetStandard"
        camp.TimeZone = time_zone
        camp.BiddingScheme = scheme
        camp.Languages = _string_array(svc, ["All"])

        camp_arr = svc.factory.create("ArrayOfCampaign")
        camp_arr.Campaign = [camp]
        resp = svc.AddCampaigns(AccountId=client.account_id(), Campaigns=camp_arr)
        errs = client.partial_errors(resp)
        camp_ids = client.long_ids(getattr(resp, "CampaignIds", None))
        if errs or not camp_ids:
            return {"campaign_ids": camp_ids, "partial_errors": errs,
                    "aborted": "campaign add failed; asset group not attempted"}

        # Widened try (reviewer finding 2): payload BUILDING (blank()/array creation),
        # not just the SOAP call, must be inside the guard -- a raise while building the
        # asset group after the campaign already landed is the exact failure class this
        # closes, not just a raise from the call itself.
        try:
            ag = client.blank(svc, "AssetGroup")  # no Type field — WSDL-confirmed, no discriminator
            ag.Name = f"{name} - Asset Group 1"
            ag.Status = "Paused"
            ag.FinalUrls = _string_array(svc, [final_url])
            ag.BusinessName = business_name
            ag.Headlines = _text_asset_links(svc, headlines)
            ag.LongHeadlines = _text_asset_links(svc, long_headlines)
            ag.Descriptions = _text_asset_links(svc, descriptions)
            ag.Images = ag_images

            ag_arr = svc.factory.create("ArrayOfAssetGroup")
            ag_arr.AssetGroup = [ag]
            resp2 = svc.AddAssetGroups(CampaignId=camp_ids[0], AssetGroups=ag_arr)
        except Exception as e:
            # Step 1 (AddCampaigns) landed -- carry the campaign so a mid-apply
            # exception here doesn't erase it (plan item 11).
            raise rails.PartialWriteError(
                "asset group add failed after campaign add landed",
                partial={"campaign_ids": camp_ids, "asset_group_ids": []}) from e

        try:
            ag_ids = client.long_ids(getattr(resp2, "AssetGroupIds", None))
        except Exception as e:
            # Reviewer finding 3: the SOAP call above already succeeded -- the asset
            # group rows exist server-side even though id-parsing then failed. Reporting
            # asset_group_ids=[] here would read as "nothing landed" and could prompt a
            # dangerous blind re-add; asset_group_call_landed=True says otherwise.
            raise rails.PartialWriteError(
                "asset group add landed but response id parsing failed",
                partial={"campaign_ids": camp_ids, "asset_group_ids": [],
                         "asset_group_call_landed": True}) from e
        return {"campaign_ids": camp_ids, "asset_group_ids": ag_ids,
                "partial_errors": errs + client.partial_errors(resp2)}

    return rails.create_draft("create_pmax_campaign",
                              {"Name": name, "Status": "Paused", "DailyBudget": daily_budget,
                               "TargetCpa": target_cpa, "TimeZone": time_zone,
                               "images": len(image_media_ids)}, apply)


@mcp.tool()
def create_portfolio_bidding_strategy(name: str, strategy_type: str,
                                      target_cpa: float | None = None) -> dict:
    """Draft a portfolio bid strategy (account library, shared across campaigns).
    strategy_type: MaxConversions | MaxClicks | TargetImpressionShare. Returns a
    draft; apply with confirm_and_apply.

    Live-verified 2026-07-30."""
    if strategy_type not in STRATEGY_TYPES:
        raise ValueError(f"strategy_type must be one of {sorted(STRATEGY_TYPES)}, got '{strategy_type}'")
    if target_cpa is not None:
        if strategy_type != "MaxConversions":
            # WSDL-confirmed: MaxClicksBiddingScheme/TargetImpressionShareBiddingScheme have
            # no TargetCpa field — setting it would silently fail to serialize rather than error.
            raise ValueError(f"target_cpa only applies to strategy_type=MaxConversions, not {strategy_type}")
        rails.check_bid(target_cpa)

    def apply():
        svc = client.svc("CampaignManagementService")
        scheme = client.blank(svc, f"{strategy_type}BiddingScheme")
        scheme.Type = strategy_type
        if target_cpa is not None:
            scheme.TargetCpa = target_cpa

        bs = client.blank(svc, "BidStrategy")
        bs.Name = name
        bs.BiddingScheme = scheme

        arr = svc.factory.create("ArrayOfBidStrategy")
        arr.BidStrategy = [bs]
        resp = svc.AddBidStrategies(BidStrategies=arr)
        ids = client.long_ids(getattr(resp, "BidStrategyIds", None))
        return {"bid_strategy_ids": ids, "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("create_portfolio_bidding_strategy",
                              {"name": name, "strategy_type": strategy_type,
                               "target_cpa": target_cpa}, apply)
