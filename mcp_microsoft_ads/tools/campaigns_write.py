from .. import client, rails, verify
from ..app import mcp
from ..util import suds_to_dict

ALL_TYPES = "Search Shopping DynamicSearchAds Audience PerformanceMax"


def _fetch_campaign(campaign_id: int):
    svc = client.svc("CampaignManagementService")
    ids = svc.factory.create("ns3:ArrayOflong")  # live-verified: ns4 raises TypeNotFound (Session 2)
    ids.long = [campaign_id]
    r = svc.GetCampaignsByIds(AccountId=client.account_id(), CampaignIds=ids, CampaignType=ALL_TYPES)
    camps = client.as_list(getattr(getattr(r, "Campaigns", None), "Campaign", None))
    if not camps:
        raise ValueError(f"campaign {campaign_id} not found")
    return camps[0]


@mcp.tool()
def update_campaign(campaign_id: int, daily_budget: float | None = None,
                    status: str | None = None, target_cpa: float | None = None) -> dict:
    """Draft a campaign update (budget / status Active|Paused / tCPA). tCPA at campaign
    level is only operative on PMax — for Search use update_ad_group. Returns a draft;
    apply with confirm_and_apply.

    NOT live-verified through this tool itself. The underlying blank()-built
    UpdateCampaigns call IS live-probed (2026-07-30, DailyBudget-only change), via the
    same pattern live-proven through status.py's ad_group Status flip (2026-07-28), not
    through update_ad_group itself."""
    if daily_budget is None and status is None and target_cpa is None:
        raise ValueError("nothing to change — pass daily_budget, status, and/or target_cpa")
    if status is not None and status not in ("Active", "Paused"):
        raise ValueError("status must be Active or Paused")
    if daily_budget is not None:
        rails.check_budget(daily_budget)
    if target_cpa is not None:
        rails.check_bid(target_cpa)

    current = _fetch_campaign(campaign_id)
    changes = {}
    if daily_budget is not None:
        changes["DailyBudget"] = {"before": current.DailyBudget, "after": daily_budget}
    if status is not None:
        changes["Status"] = {"before": current.Status, "after": status}
    if target_cpa is not None:
        if current.CampaignType != "PerformanceMax":
            raise rails.RailViolation(
                f"campaign-level tCPA only operative on PMax; {current.Name} is {current.CampaignType} — "
                "set tCPA on its ad groups via update_ad_group")
        changes["TargetCpa"] = {"before": suds_to_dict(current.BiddingScheme), "after": target_cpa}

    def apply():
        svc = client.svc("CampaignManagementService")
        # live-verified (Task 15): a bare SimpleNamespace/dict payload fails server-side
        # deserialization on Update* calls (suds needs the real factory-typed object to encode
        # enum fields like Status correctly) — build via client.blank() + the real ArrayOfCampaign
        # container instead. See client.blank docstring.
        upd = client.blank(svc, "Campaign")
        upd.Id = campaign_id
        expect = {}
        if daily_budget is not None:
            upd.DailyBudget = daily_budget
            expect["DailyBudget"] = float(daily_budget)
        if status is not None:
            upd.Status = status
            expect["Status"] = status
        if target_cpa is not None:
            # nested scheme objects are client.blank()-built for the same reason the outer
            # Campaign is (raw factory defaults populate junk enum values that fail
            # server-side deserialization — see client.blank docstring); this branch itself
            # has not yet been live-exercised.
            scheme = client.blank(svc, "MaxConversionsBiddingScheme")
            scheme.Type = "MaxConversions"
            scheme.TargetCpa = target_cpa
            upd.BiddingScheme = scheme
            # campaign.BiddingScheme reads None — verify via Bulk/UI, flag as unverifiable here
        arr = svc.factory.create("ArrayOfCampaign")
        arr.Campaign = [upd]
        resp = svc.UpdateCampaigns(AccountId=client.account_id(), Campaigns=arr)
        errs = client.partial_errors(resp)
        result = {"partial_errors": errs}
        if expect:
            result["verify"] = verify.verify_fields(lambda: _fetch_campaign(campaign_id), expect)
        if target_cpa is not None:
            result["tcpa_note"] = ("applied; campaign BiddingScheme is not readable back via API — "
                                   "confirm in MS Ads UI or Bulk download")
        return result

    return rails.create_draft("update_campaign",
                              {"campaign": current.Name, "campaign_id": campaign_id, "changes": changes},
                              apply)


@mcp.tool()
def draft_campaign(name: str, campaign_type: str, daily_budget: float,
                   time_zone: str = "EasternTimeUSCanada", language: str = "English") -> dict:
    """Draft a new campaign — ALWAYS created Paused. campaign_type: Search | PerformanceMax.
    time_zone/language default to EasternTimeUSCanada/English (US-centric defaults) —
    override for other markets.

    Live-verified 2026-07-30 (AddCampaigns create path, proven twice).
    NOT live-verified with non-default language values."""
    rails.check_budget(daily_budget)
    rails.check_content([name])
    preview = {"Name": name, "CampaignType": campaign_type, "DailyBudget": daily_budget,
               "Status": "Paused", "TimeZone": time_zone, "Language": language,
               "BudgetType": "DailyBudgetStandard"}

    def apply():
        svc = client.svc("CampaignManagementService")
        # live fault (Task 25b): AddCampaigns hit the same suds deserialization fault as
        # Task 15's UpdateAdGroups — a bare SimpleNamespace/dict payload can't carry
        # enum-wrapped fields (Campaign.Status) over the wire. Build via client.blank() +
        # the real ArrayOfCampaign/ArrayOfstring containers, same idiom as update_campaign
        # above and pmax.py. See client.blank docstring.
        camp = client.blank(svc, "Campaign")
        camp.Name = name
        camp.CampaignType = campaign_type
        camp.DailyBudget = daily_budget
        camp.BudgetType = "DailyBudgetStandard"
        camp.Status = "Paused"
        camp.TimeZone = time_zone
        langs = svc.factory.create("ns3:ArrayOfstring")  # live-proven prefix (Session 6, AddAds/FinalUrls)
        langs.string = [language]
        camp.Languages = langs

        arr = svc.factory.create("ArrayOfCampaign")
        arr.Campaign = [camp]
        resp = svc.AddCampaigns(AccountId=client.account_id(), Campaigns=arr)
        ids = client.long_ids(getattr(resp, "CampaignIds", None))
        result = {"campaign_ids": ids, "partial_errors": client.partial_errors(resp)}
        if ids:
            expect = {"Name": name, "DailyBudget": float(daily_budget),
                      "CampaignType": campaign_type, "Status": "Paused"}
            result["verify"] = verify.verify_fields(lambda: _fetch_campaign(ids[0]), expect)
        return result

    return rails.create_draft("draft_campaign", preview, apply)
