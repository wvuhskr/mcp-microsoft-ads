"""Remarketing audiences + audience targeting. RemarketingList requires an account
UET tag (GetUetTagsByIds) — errors clearly if none exists. All write payloads are
client.blank()-built factory objects (bare SimpleNamespace fails suds enum
serialization live — Task 23 binding deviation)."""
from .. import client, rails, settings
from ..app import mcp


def _resolve_url_contains(url_contains: str | None) -> str:
    if url_contains is not None:
        stripped = url_contains.strip()
        if not stripped:
            raise ValueError(
                "url_contains is blank — pass a non-empty substring, or omit the param "
                "entirely to fall back to the advertiser_domain setting")
        return stripped
    domain = settings.advertiser_domain().strip()
    if not domain:
        raise ValueError(
            "no url_contains param and no advertiser_domain configured — pass url_contains "
            "or set advertiser_domain in the advertiser settings file")
    return domain


@mcp.tool()
def create_custom_audience(name: str, description: str, membership_duration_days: int = 30,
                          url_contains: str | None = None) -> dict:
    """Draft a remarketing list targeting site visitors (needs the account's UET tag;
    errors if none exists — create one in the MS Ads UI first). url_contains: substring
    the visited page URL must contain; defaults to the configured advertiser_domain
    setting when omitted — error naming both if neither is set. A whitespace-only
    url_contains is rejected outright (not treated as omitted).

    Live-verified 2026-07-30."""
    resolved_url = _resolve_url_contains(url_contains)
    svc = client.svc("CampaignManagementService")
    tags = client.as_list(getattr(getattr(svc.GetUetTagsByIds(TagIds=None), "UetTags", None), "UetTag", None))
    if not tags:
        raise ValueError("no UET tag on the account — create one in MS Ads UI first")
    tag_id = tags[0].Id

    def apply():
        item = client.blank(svc, "StringRuleItem")
        item.Type = "StringRuleItem"
        item.Operand = "Url"
        item.Operator = "Contains"
        item.Value = resolved_url
        item_arr = svc.factory.create("ArrayOfRuleItem")
        item_arr.RuleItem = [item]

        group = client.blank(svc, "RuleItemGroup")
        group.Items = item_arr
        group_arr = svc.factory.create("ArrayOfRuleItemGroup")
        group_arr.RuleItemGroup = [group]

        rule = client.blank(svc, "PageVisitorsRule")
        rule.Type = "PageVisitorsRule"
        rule.NormalForm = "Conjunctive"
        rule.RuleItemGroups = group_arr

        aud = client.blank(svc, "RemarketingList")
        aud.Type = "RemarketingList"
        aud.Name = name
        aud.Description = description
        aud.MembershipDuration = membership_duration_days
        aud.TagId = tag_id
        aud.Rule = rule

        arr = svc.factory.create("ArrayOfAudience")
        arr.Audience = [aud]
        resp = svc.AddAudiences(Audiences=arr)
        ids = client.long_ids(getattr(resp, "AudienceIds", None))
        return {"audience_ids": ids, "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("create_custom_audience",
                              {"name": name, "duration_days": membership_duration_days,
                               "uet_tag_id": tag_id, "url_contains": resolved_url}, apply)


@mcp.tool()
def add_audience_targeting(campaign_id: int, audience_id: int,
                           audience_type: str = "RemarketingList",
                           bid_adjustment_pct: float | None = None) -> dict:
    """Draft audience targeting on a campaign. audience_type disambiguates which id
    namespace audience_id is in — one of: RemarketingList, Custom, InMarket, Product,
    SimilarRemarketingList, CombinedList, CustomerList, ImpressionBasedRemarketingList,
    CustomSegment (WSDL AudienceType enum). bid_adjustment_pct is allowed ONLY when the
    campaign's effective bid strategy is in the MANUAL_BIDDING allowlist (rails.py);
    rejected for Smart Bidding, any other/unrecognized strategy, or when the strategy
    can't be determined at all (fail closed — standing no-%-adjustments-on-auto-bidding
    rule).

    Live-verified 2026-07-30 (the AddCampaignCriterions write path, CriterionType=
    "Audience"); the read-side fault seen earlier is GET-only and doesn't apply here."""
    svc = client.svc("CampaignManagementService")

    def _campaign_strategy():
        # live-verified unwrapped: .AdGroup direct, not .AdGroups.AdGroup (deviation 5)
        ags = client.as_list(getattr(svc.GetAdGroupsByCampaignId(CampaignId=campaign_id), "AdGroup", None))
        return client.effective_strategy(ags[0]) if ags else None

    def check_policy(strategy):
        rails.check_no_pct_adjustment(strategy, {"audience_bid_adjustment_pct": bid_adjustment_pct})

    strategy = _campaign_strategy()
    check_policy(strategy)

    def apply():
        crit = client.blank(svc, "AudienceCriterion")
        crit.Type = "AudienceCriterion"
        crit.AudienceId = audience_id
        crit.AudienceType = audience_type

        cc = client.blank(svc, "BiddableCampaignCriterion")
        cc.Type = "BiddableCampaignCriterion"
        cc.CampaignId = campaign_id
        cc.Criterion = crit
        if bid_adjustment_pct is not None:
            bid = client.blank(svc, "BidMultiplier")
            bid.Type = "BidMultiplier"
            bid.Multiplier = bid_adjustment_pct
            cc.CriterionBid = bid

        arr = svc.factory.create("ArrayOfCampaignCriterion")
        arr.CampaignCriterion = [cc]
        # CriterionType="Audience" is a valid CampaignCriterionType enum value (WSDL-confirmed).
        # GetCampaignCriterionsByIds (GET) hard-faults with it — that's GET-only, not a write
        # problem. This write path (AddCampaignCriterions) is what the plan specifies, and it
        # was live-verified clean 2026-07-30.
        resp = svc.AddCampaignCriterions(CampaignCriterions=arr, CriterionType="Audience")
        ids = client.long_ids(getattr(resp, "CampaignCriterionIds", None))
        return {"criterion_ids": ids, "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("add_audience_targeting",
                              {"campaign_id": campaign_id, "audience_id": audience_id,
                               "audience_type": audience_type,
                               "effective_strategy": strategy,
                               "bid_adjustment_pct": bid_adjustment_pct}, apply,
                              validate_fn=lambda: check_policy(_campaign_strategy()))
