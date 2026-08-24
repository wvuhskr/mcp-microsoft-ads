"""AdInsight tools: keyword discovery/forecasts + account recommendations.

Live-probed (Task 22, AdInsightService — a new service, prefix/shapes unconfirmed
by prior sessions):
- Factory namespace prefix for Arrays types is "ns1:" (same slot as ReportingService,
  not CampaignManagementService's "ns3:") — only needed for ArrayOflong/ArrayOfstring;
  business types (QuerySearchParameter, LanguageCriterion, CampaignEstimator, ...)
  resolve bare via factory.create(). Not actually needed below (Queries/Languages
  assign fine as plain dicts on factory-built parents — see below) but recorded since
  the brief calls out prefix-probing as required.
- SearchParameter/Criterion are abstract bases with NO discriminator field at all
  (WSDL: `<xs:complexType name="SearchParameter"><xs:sequence/></xs:complexType>`) —
  unlike criterions/ads in other services, there is no `.Type` to set. suds carries
  the concrete type from factory.create() itself.
- GetKeywordIdeas HARD-REQUIRES LocationSearchParameter + LanguageSearchParameter +
  NetworkSearchParameter (each non-empty) even though the plan/WSDL mark them
  optional — live fault otherwise: "RequiredSearchParameterMissing". Values come from
  the advertiser settings' keyword_research block (location_id/language/network),
  not caller-supplied params.
- GetKeywordTrafficEstimates similarly hard-requires Criteria (Location + Language +
  Network criteria) on CampaignEstimator, plus MaxCpc on both the AdGroupEstimator
  and each KeywordEstimator, none of which the plan's fake exercised.
- Response unwrap: every AdInsight response element here has exactly one child field,
  and suds unwraps it — confirmed live for GetKeywordIdeas (`r.KeywordIdea` directly,
  not `r.KeywordIdeas.KeywordIdea`) and GetKeywordTrafficEstimates (`r.CampaignEstimate`
  directly). GetRecommendations/ApplyRecommendations/DismissRecommendations follow the
  same single-field-response shape so are coded the same way; Apply/Dismiss can't be
  live-verified (mutating), so their error extraction defensively handles both the
  unwrapped and (WSDL-literal) wrapped shape.
- GetRecommendations' RecommendationType filter is effectively required live (null
  faults "RecommendationType is null") but every RecommendationType enum value tried
  faulted 607 "recommendation type is unspported" on this account — looks like a
  feature-gating issue, not something fixable in code. list_recommendations() takes it
  as an optional pass-through param rather than the brief's zero-arg signature.
"""
from .. import client, rails, settings
from ..app import mcp
from ..util import suds_to_dict


def _location_criterion(svc):
    c = svc.factory.create("LocationCriterion")
    c.LocationId = settings.keyword_research()["location_id"]
    return c


def _language_criterion(svc):
    c = svc.factory.create("LanguageCriterion")
    c.Language = settings.keyword_research()["language"]
    return c


def _network_criterion(svc):
    c = svc.factory.create("NetworkCriterion")
    c.Network = settings.keyword_research()["network"]
    return c


@mcp.tool()
def discover_keywords(seed_keywords: list[str] | None = None, url: str | None = None) -> dict:
    """Keyword ideas from seed keywords and/or a landing-page URL (Ad Insight
    GetKeywordIdeas). Scoped to the location/language/network configured via advertiser
    settings' keyword_research block (defaults: US/English/Owned&Operated+Syndicated)."""
    if not seed_keywords and not url:
        raise ValueError("pass seed_keywords or url")
    svc = client.svc("AdInsightService")
    params = []
    if seed_keywords:
        qp = svc.factory.create("QuerySearchParameter")
        qp.Queries = {"string": seed_keywords}
        params.append(qp)
    if url:
        up = svc.factory.create("UrlSearchParameter")
        up.Url = url
        params.append(up)

    lang = svc.factory.create("LanguageSearchParameter")
    lang.Languages = {"LanguageCriterion": [_language_criterion(svc)]}
    params.append(lang)

    loc = svc.factory.create("LocationSearchParameter")
    loc.Locations = {"LocationCriterion": [_location_criterion(svc)]}
    params.append(loc)

    net = svc.factory.create("NetworkSearchParameter")
    net.Network = _network_criterion(svc)
    params.append(net)

    params_arr = svc.factory.create("ArrayOfSearchParameter")
    params_arr.SearchParameter = params

    attrs_arr = svc.factory.create("ArrayOfKeywordIdeaAttribute")
    attrs_arr.KeywordIdeaAttribute = ["Keyword", "MonthlySearchCounts", "SuggestedBid", "Competition"]

    r = svc.GetKeywordIdeas(ExpandIdeas=True, IdeaAttributes=attrs_arr, SearchParameters=params_arr)
    ideas = client.as_list(getattr(r, "KeywordIdea", None))
    kr = settings.keyword_research()
    return {"ideas": [suds_to_dict(i) for i in ideas],
            "provenance": {"source": "MS Ads AdInsight GetKeywordIdeas",
                            "scope": f"location_id={kr['location_id']}, {kr['language']}, {kr['network']}"}}


@mcp.tool()
def get_keyword_forecasts(keywords: list[str], match_type: str = "Exact",
                          max_cpc: float | None = None) -> dict:
    """Traffic estimates (clicks/impressions/CPC/cost) for candidate keywords at a
    given max CPC (defaults to the account's MS_ADS_MAX_CPC cap). Scoped to the
    location/language/network configured via advertiser settings (see discover_keywords)."""
    svc = client.svc("AdInsightService")
    bid = max_cpc if max_cpc is not None else rails.max_cpc()

    kw_ests = []
    for text in keywords:
        kw = svc.factory.create("Keyword")
        kw.Text = text
        kw.MatchType = match_type
        kw_est = svc.factory.create("KeywordEstimator")
        kw_est.Keyword = kw
        kw_est.MaxCpc = bid
        kw_ests.append(kw_est)
    kw_est_arr = svc.factory.create("ArrayOfKeywordEstimator")
    kw_est_arr.KeywordEstimator = kw_ests

    ag_est = svc.factory.create("AdGroupEstimator")
    ag_est.KeywordEstimators = kw_est_arr
    ag_est.MaxCpc = bid
    ag_est_arr = svc.factory.create("ArrayOfAdGroupEstimator")
    ag_est_arr.AdGroupEstimator = [ag_est]

    crit_arr = svc.factory.create("ArrayOfCriterion")
    crit_arr.Criterion = [_location_criterion(svc), _language_criterion(svc), _network_criterion(svc)]

    camp_est = svc.factory.create("CampaignEstimator")
    camp_est.AdGroupEstimators = ag_est_arr
    camp_est.Criteria = crit_arr
    camp_est.DailyBudget = 100.0  # high enough that budget, not bid, is never the limiting factor here
    camp_est_arr = svc.factory.create("ArrayOfCampaignEstimator")
    camp_est_arr.CampaignEstimator = [camp_est]

    r = svc.GetKeywordTrafficEstimates(CampaignEstimators=camp_est_arr)
    estimates = client.as_list(getattr(r, "CampaignEstimate", None))
    return {"estimates": [suds_to_dict(e) for e in estimates],
            "provenance": {"source": "MS Ads AdInsight GetKeywordTrafficEstimates",
                            "max_cpc": bid}}


@mcp.tool()
def list_recommendations(recommendation_type: str | None = None, campaign_id: int | None = None,
                         ad_group_id: int | None = None) -> dict:
    """Account recommendations (MS Ad Insight GetRecommendations). recommendation_type
    is passed through as-is to MS — live probing found the API rejects a null type but
    every documented RecommendationType enum value also faulted (607, account/feature
    gating, not a code issue); pass whatever type MS has enabled for this account.

    NOT live-verified — 607-gated on the development account, as above."""
    svc = client.svc("AdInsightService")
    r = svc.GetRecommendations(CampaignId=campaign_id, AdGroupId=ad_group_id,
                               RecommendationType=recommendation_type)
    recs = getattr(r, "Recommendation", None)
    if recs is None:  # defensive: unconfirmed live (account has no enabled types) — fall back
        recs = getattr(getattr(r, "Recommendations", None), "Recommendation", None)  # to WSDL-literal wrapped shape
    return {"recommendations": [suds_to_dict(x) for x in client.as_list(recs)]}


def _rec_action(tool: str, recommendation_id: str, entity_type: str, op_name: str, warning: str = None) -> dict:
    svc = client.svc("AdInsightService")

    def apply():
        entity = client.blank(svc, entity_type)
        entity.RecommendationId = recommendation_id
        arr = svc.factory.create(f"ArrayOf{entity_type}")
        setattr(arr, entity_type, [entity])
        resp = getattr(svc, op_name)(Entities=arr)
        return {"recommendation_id": recommendation_id, "partial_errors": client.partial_errors(resp)}

    preview = {"recommendation_id": recommendation_id, "action": op_name}
    if warning:
        preview["warning"] = warning
    return rails.create_draft(tool, preview, apply)


@mcp.tool()
def apply_recommendation(recommendation_id: str) -> dict:
    """Draft applying an MS recommendation (mutates the account — rails apply).

    NOT live-verified — every RecommendationType faults 607 InvalidOpportunityType
    on the development account (account-level gating). Fakes only.

    Also gated by MS_ADS_ALLOW_APPLY_RECOMMENDATION (rails.py), in addition to the
    global MS_ADS_ENABLE_WRITES — its monetary effect can't be bounded by the budget/bid
    caps, so it needs its own opt-in on top of the general write gate."""
    return _rec_action(
        "apply_recommendation", recommendation_id, "ApplyRecommendationEntity", "ApplyRecommendations",
        warning="applies a Microsoft-decided change; rails budget/bid caps cannot bound what this applies")


@mcp.tool()
def dismiss_recommendation(recommendation_id: str) -> dict:
    """Draft dismissing an MS recommendation.

    NOT live-verified — same 607 gate as apply_recommendation. Fakes only."""
    return _rec_action("dismiss_recommendation", recommendation_id, "DismissRecommendationEntity", "DismissRecommendations")
