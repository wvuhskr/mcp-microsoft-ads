from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails
from mcp_microsoft_ads.tools import conversions


class FakeSvc:
    def __init__(self):
        self.factory = NS(create=lambda t: NS())  # blank() no-ops on NS (no __keylist__)
        self.uet_calls = 0

    def GetUetTagsByIds(self, TagIds):
        self.uet_calls += 1
        return NS(UetTags=NS(UetTag=[NS(Id=333)]))

    def AddConversionGoals(self, ConversionGoals):
        return NS(ConversionGoalIds=NS(long=[42]), PartialErrors=None)

    # NOTE: WSDL names this param "CampaignConversionGoal" (singular) even though it
    # carries an ArrayOfCampaignConversionGoal — verified offline against
    # campaignmanagement_service.xml; the plan brief's fixture had it plural.
    def AddCampaignConversionGoals(self, CampaignConversionGoal):
        return NS(PartialErrors=None)

    def DeleteCampaignConversionGoals(self, CampaignConversionGoal):
        return NS(PartialErrors=None)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


def test_create_goal(fake):
    d = conversions.create_conversion_action("Web Lead", goal_type="Event", action_expression="submit")
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["goal_ids"] == [42]


def test_create_goal_payload_shape_url(fake):
    """goal_type is polymorphic: factory must build the concrete subtype
    (EventGoal/UrlGoal/DurationGoal), Type re-set after blank() to the enum string."""
    captured = {}
    orig = fake.AddConversionGoals

    def spy(ConversionGoals):
        captured["ConversionGoals"] = ConversionGoals
        return orig(ConversionGoals)

    fake.AddConversionGoals = spy
    d = conversions.create_conversion_action("Web Lead", goal_type="Url", value=99.5,
                                             url_expression="/thank-you")
    rails.apply_draft(d["draft_id"])
    goal = captured["ConversionGoals"].ConversionGoal[0]
    assert goal.Type == "Url"
    assert goal.Name == "Web Lead"
    assert goal.TagId == 333
    assert goal.Revenue.Type == "FixedValue"
    assert goal.Revenue.Value == 99.5
    assert goal.UrlExpression == "/thank-you"
    assert goal.UrlOperator == "Contains"


def test_create_goal_payload_shape_event(fake):
    captured = {}
    orig = fake.AddConversionGoals

    def spy(ConversionGoals):
        captured["ConversionGoals"] = ConversionGoals
        return orig(ConversionGoals)

    fake.AddConversionGoals = spy
    d = conversions.create_conversion_action("Form Submit", goal_type="Event", action_expression="lead_submit")
    rails.apply_draft(d["draft_id"])
    goal = captured["ConversionGoals"].ConversionGoal[0]
    assert goal.Type == "Event"
    assert goal.ActionExpression == "lead_submit"
    assert goal.ActionOperator == "Contains"
    assert goal.GoalCategory == "Other"  # Task 25e default (live-corrected — "None" is rejected, 3349)


def test_create_goal_default_goal_category_is_other(fake):
    """Task 25e: live probe showed goal_category="None" is rejected
    (InvalidCategoryForGoalType, 3349) — "Other" is the only value verified valid across
    all three goal types, so it is now the default (was "None")."""
    captured = {}
    orig = fake.AddConversionGoals

    def spy(ConversionGoals):
        captured["ConversionGoals"] = ConversionGoals
        return orig(ConversionGoals)

    fake.AddConversionGoals = spy
    d = conversions.create_conversion_action("Web Lead", goal_type="Event", action_expression="submit")
    assert d["preview"]["goal_category"] == "Other"
    rails.apply_draft(d["draft_id"])
    goal = captured["ConversionGoals"].ConversionGoal[0]
    assert goal.GoalCategory == "Other"


def test_create_goal_payload_shape_duration(fake):
    captured = {}
    orig = fake.AddConversionGoals

    def spy(ConversionGoals):
        captured["ConversionGoals"] = ConversionGoals
        return orig(ConversionGoals)

    fake.AddConversionGoals = spy
    d = conversions.create_conversion_action("Engaged Visit", goal_type="Duration",
                                             minimum_duration_seconds=60)
    rails.apply_draft(d["draft_id"])
    goal = captured["ConversionGoals"].ConversionGoal[0]
    assert goal.Type == "Duration"
    assert goal.MinimumDurationInSeconds == 60


def test_create_goal_custom_goal_category(fake):
    """Defect 3: AddConversionGoals requires GoalCategory live (WSDL marks it
    optional) — a passed goal_category must reach both the draft preview and the
    payload sent to the service."""
    captured = {}
    orig = fake.AddConversionGoals

    def spy(ConversionGoals):
        captured["ConversionGoals"] = ConversionGoals
        return orig(ConversionGoals)

    fake.AddConversionGoals = spy
    d = conversions.create_conversion_action("Booked Job", goal_type="Event", action_expression="book",
                                             goal_category="BookAppointment")
    assert d["preview"]["goal_category"] == "BookAppointment"
    rails.apply_draft(d["draft_id"])
    goal = captured["ConversionGoals"].ConversionGoal[0]
    assert goal.GoalCategory == "BookAppointment"


def test_create_goal_rejects_bad_goal_category_before_service_call(fake):
    with pytest.raises(ValueError, match="goal_category"):
        conversions.create_conversion_action("x", goal_type="Event", action_expression="submit",
                                             goal_category="Bogus")
    assert fake.uet_calls == 0


def test_create_goal_rejects_offline(fake):
    with pytest.raises(ValueError, match="offline"):
        conversions.create_conversion_action("Booked Job", goal_type="OfflineConversion")


def test_create_goal_rejects_unsupported_type(fake):
    with pytest.raises(ValueError, match="unsupported"):
        conversions.create_conversion_action("x", goal_type="InStoreTransaction")


def test_create_goal_requires_url_expression(fake):
    with pytest.raises(ValueError, match="url_expression"):
        conversions.create_conversion_action("x", goal_type="Url")


def test_create_goal_requires_action_expression(fake):
    with pytest.raises(ValueError, match="action_expression"):
        conversions.create_conversion_action("x", goal_type="Event")


def test_create_goal_requires_minimum_duration(fake):
    with pytest.raises(ValueError, match="minimum_duration_seconds"):
        conversions.create_conversion_action("x", goal_type="Duration")


def test_create_goal_no_uet_tag_errors(monkeypatch, tmp_path):
    class NoTagSvc(FakeSvc):
        def GetUetTagsByIds(self, TagIds):
            return NS(UetTags=None)

    svc = NoTagSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    with pytest.raises(ValueError, match="no UET tag"):
        conversions.create_conversion_action("x", action_expression="submit")


def test_primary_status_association(fake):
    d = conversions.set_conversion_action_primary_status(524066223, 42, primary=True)
    assert d["preview"]["action"] == "associate (make campaign-primary)"
    rails.apply_draft(d["draft_id"])


def test_primary_status_disassociation_calls_delete(fake):
    captured = {}
    orig = fake.DeleteCampaignConversionGoals

    def spy(CampaignConversionGoal):
        captured["arg"] = CampaignConversionGoal
        return orig(CampaignConversionGoal)

    fake.DeleteCampaignConversionGoals = spy
    d = conversions.set_conversion_action_primary_status(524066223, 42, primary=False)
    assert d["preview"]["action"] == "disassociate"
    rails.apply_draft(d["draft_id"])
    ccg = captured["arg"].CampaignConversionGoal[0]
    assert ccg.CampaignId == 524066223
    assert ccg.GoalId == 42
