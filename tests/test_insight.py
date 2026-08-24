"""Real WSDL/live shapes differ from the plan's fake (see insight.py module
docstring): GetKeywordIdeas/GetKeywordTrafficEstimates/GetRecommendations responses
unwrap to their single child directly (`.KeywordIdea`, `.CampaignEstimate`,
`.Recommendation`), not the wrapped `.KeywordIdeas.KeywordIdea` etc. the plan assumed.
ApplyRecommendations/DismissRecommendations take `Entities` (ArrayOfApply/
DismissRecommendationEntity), not `RecommendationsInfo`. Fixture updated to match —
Apply/Dismiss shapes are unconfirmed live (mutating calls are off-limits) so the fake
covers both the empty-error and populated-error cases."""
from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails, settings
from mcp_microsoft_ads.tools import insight


class FakeSvc:
    def __init__(self):
        self.applied = []
        self.dismissed = []
        self.factory = NS(create=lambda t: NS())

    def GetKeywordIdeas(self, ExpandIdeas, IdeaAttributes, SearchParameters):
        return NS(KeywordIdea=[NS(Keyword="ac repair springfield", MonthlySearchCounts=NS(long=[300]))])

    def GetKeywordTrafficEstimates(self, CampaignEstimators):
        return NS(CampaignEstimate=[NS(CampaignId=None, AdGroupEstimates=NS(AdGroupEstimate=[
            NS(AdGroupId=None, KeywordEstimates=NS(KeywordEstimate=[
                NS(Keyword=NS(Text="ac repair springfield", MatchType="Exact"),
                   Maximum=NS(Clicks=12.0, Impressions=400.0), Minimum=NS(Clicks=2.0, Impressions=50.0))]))]))])

    def GetRecommendations(self, CampaignId=None, AdGroupId=None, RecommendationType=None):
        return NS(Recommendation=[NS(RecommendationId="R1", RecommendationType="ResponsiveSearchAdRecommendation")])

    def ApplyRecommendations(self, Entities):
        self.applied.append(Entities)
        return NS(BatchError=None)

    def DismissRecommendations(self, Entities):
        self.dismissed.append(Entities)
        return NS(BatchError=[NS(Index=0, ErrorCode="X", Code="1", Message="already dismissed")])


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


def test_discover(fake):
    out = insight.discover_keywords(seed_keywords=["ac repair"])
    assert out["ideas"][0]["Keyword"] == "ac repair springfield"


def test_discover_needs_input(fake):
    with pytest.raises(ValueError, match="seed_keywords or url"):
        insight.discover_keywords()


def test_discover_scoped_by_settings(fake, monkeypatch):
    """location_id/language/network must flow from settings.keyword_research() into
    the actual SOAP call, not from hardcoded module constants."""
    monkeypatch.setattr(settings, "keyword_research", lambda: {
        "location_id": 999, "language": "French", "network": "OwnedAndOperatedOnly"})
    captured = {}
    orig = fake.GetKeywordIdeas

    def spy(ExpandIdeas, IdeaAttributes, SearchParameters):
        captured["params"] = SearchParameters
        return orig(ExpandIdeas, IdeaAttributes, SearchParameters)

    fake.GetKeywordIdeas = spy
    out = insight.discover_keywords(seed_keywords=["ac repair"])
    # order per discover_keywords body: query param, then language, location, network
    params = captured["params"].SearchParameter
    lang_param, loc_param, net_param = params[1], params[2], params[3]
    assert lang_param.Languages["LanguageCriterion"][0].Language == "French"
    assert loc_param.Locations["LocationCriterion"][0].LocationId == 999
    assert net_param.Network.Network == "OwnedAndOperatedOnly"
    assert "999" in out["provenance"]["scope"]
    assert "French" in out["provenance"]["scope"]


def test_forecasts(fake):
    out = insight.get_keyword_forecasts(["ac repair springfield"])
    est = out["estimates"][0]["AdGroupEstimates"]["AdGroupEstimate"][0]["KeywordEstimates"]["KeywordEstimate"][0]
    assert est["Keyword"]["Text"] == "ac repair springfield"
    assert out["provenance"]["max_cpc"] == rails.max_cpc()


def test_forecasts_scoped_by_settings(fake, monkeypatch):
    monkeypatch.setattr(settings, "keyword_research", lambda: {
        "location_id": 555, "language": "German", "network": "OwnedAndOperatedOnly"})
    captured = {}
    orig = fake.GetKeywordTrafficEstimates

    def spy(CampaignEstimators):
        captured["campaign_estimators"] = CampaignEstimators
        return orig(CampaignEstimators)

    fake.GetKeywordTrafficEstimates = spy
    insight.get_keyword_forecasts(["ac repair springfield"])
    crit = captured["campaign_estimators"].CampaignEstimator[0].Criteria.Criterion
    assert crit[0].LocationId == 555
    assert crit[1].Language == "German"
    assert crit[2].Network == "OwnedAndOperatedOnly"


def test_list_recommendations(fake):
    out = insight.list_recommendations()
    assert out["recommendations"][0]["RecommendationId"] == "R1"


def test_apply_recommendation_is_a_draft(fake, monkeypatch):
    # Task C3: apply_recommendation's own default-off flag, in addition to the global
    # MS_ADS_ENABLE_WRITES the conftest fixture already sets -- the gate itself is
    # exercised in test_rails.py, this test is about the draft/apply happy path.
    monkeypatch.setenv("MS_ADS_ALLOW_APPLY_RECOMMENDATION", "true")
    d = insight.apply_recommendation("R1")
    assert d["dry_run"] is True
    # Task C: rails budget/bid caps can't bound an MS-decided change — preview must warn.
    assert "warning" in d["preview"]
    assert "microsoft" in d["preview"]["warning"].lower()
    result = rails.apply_draft(d["draft_id"])
    assert len(fake.applied) == 1
    entity = fake.applied[0].ApplyRecommendationEntity[0]
    assert entity.RecommendationId == "R1"
    assert result["result"]["partial_errors"] == []


def test_dismiss_recommendation_surfaces_partial_errors(fake):
    d = insight.dismiss_recommendation("R1")
    result = rails.apply_draft(d["draft_id"])
    assert len(fake.dismissed) == 1
    # repo-standard lowercase {index, code, number, message} contract (client.partial_errors),
    # same schema on both the wrapped and unwrapped response-shape branches.
    err = result["result"]["partial_errors"][0]
    assert err == {"index": 0, "code": "X", "number": "1", "message": "already dismissed"}
