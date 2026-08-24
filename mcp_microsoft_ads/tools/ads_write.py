"""RSA (responsive search ad) writes. MS RSAs carry no per-ad Status on create —
if isolation is needed, pause the AD GROUP first; the preview warns when it's Active."""
from .. import client, rails
from ..app import mcp


def _asset_link_array(svc, texts: list[str]):
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


@mcp.tool()
def draft_responsive_search_ad(ad_group_id: int, campaign_id: int, headlines: list[str],
                               descriptions: list[str], final_url: str,
                               path1: str | None = None, path2: str | None = None) -> dict:
    """Draft a Responsive Search Ad. 3-15 headlines, 2-4 descriptions. Ad copy claims
    MUST be verified against the advertiser's website before use — don't claim anything
    not actually present on the site (site-verified-claims policy). Blocklist
    (settings-configured blocked_terms) enforced on every headline/description/path.
    MS RSAs have no per-ad paused state on create — if isolation is needed, pause the
    parent ad group first; preview warns when it's Active (new ad serves once approved).

    Live-verified 2026-07-28 — ad created in a z. ad group, read back with
    Type="ResponsiveSearch"."""
    if not 3 <= len(headlines) <= 15:
        raise ValueError("3-15 headlines required")
    if not 2 <= len(descriptions) <= 4:
        raise ValueError("2-4 descriptions required")
    rails.check_content(headlines + descriptions + [path1, path2])

    svc = client.svc("CampaignManagementService")
    ids = svc.factory.create("ns3:ArrayOflong")  # live-verified prefix (Task 15/16/17/18)
    ids.long = [ad_group_id]
    r = svc.GetAdGroupsByIds(CampaignId=campaign_id, AdGroupIds=ids)
    ags = client.as_list(getattr(getattr(r, "AdGroups", None), "AdGroup", None))
    if not ags:
        raise ValueError(f"ad group {ad_group_id} not found in campaign {campaign_id}")
    ag = ags[0]
    warning = None if ag.Status == "Paused" else \
        f"parent ad group '{ag.Name}' is {ag.Status} — new RSA serves once approved"

    def apply():
        ad = client.blank(svc, "ResponsiveSearchAd")
        # live-verified 2026-07-28: Ad.Type is the AdType ENUM, not the concrete class
        # name — xsi:type (from the factory object) already carries the polymorphism.
        # RSA's enum value is "ResponsiveSearch" (see ALL_AD_TYPES in entities.py, read
        # side already used this). "ResponsiveSearchAd" faults: invalid enum value.
        ad.Type = "ResponsiveSearch"
        ad.Headlines = _asset_link_array(svc, headlines)
        ad.Descriptions = _asset_link_array(svc, descriptions)
        # ArrayOfstring lives in the same Serialization/Arrays namespace as ArrayOflong,
        # which needs the ns3: prefix live — live-proven 2026-07-28: this tool created a
        # real ad, read back with Type="ResponsiveSearch" (see docstring above).
        urls = svc.factory.create("ns3:ArrayOfstring")
        urls.string = [final_url]
        ad.FinalUrls = urls
        ad.Path1 = path1
        ad.Path2 = path2

        ads_arr = svc.factory.create("ArrayOfAd")
        ads_arr.Ad = [ad]
        resp = svc.AddAds(AdGroupId=ad_group_id, Ads=ads_arr)
        ad_ids = client.long_ids(getattr(resp, "AdIds", None))
        return {"ad_ids": ad_ids, "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("draft_responsive_search_ad",
                              {"ad_group_id": ad_group_id, "campaign_id": campaign_id,
                               "headlines": headlines, "descriptions": descriptions,
                               "final_url": final_url, "path1": path1, "path2": path2,
                               "warning": warning}, apply)
