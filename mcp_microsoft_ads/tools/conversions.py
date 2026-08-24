"""Conversion goals. Payloads are client.blank()-built factory objects; ConversionGoal
is polymorphic (concrete subtype per goal_type, WSDL-verified) so the factory type
name and the Type discriminator both vary with goal_type."""
from .. import client, rails
from ..app import mcp

# goal_type (ConversionGoalType enum value) -> concrete factory type name.
# InStoreTransaction faults on this account (pilot not enabled); AppDownload untested,
# same pilot risk — neither is offered. PagesViewedPerVisit/AppInstall out of scope (plan).
_GOAL_CLASS = {"Event": "EventGoal", "Url": "UrlGoal", "Duration": "DurationGoal"}

# ConversionGoal.GoalCategory (WSDL ConversionGoalCategory enum, complete). WSDL marks it
# optional/nillable but AddConversionGoals rejects a goal without it live ("InvalidGoalCategory",
# 3347 — Task 25 smoke). "None" is a literal enum member (not Python None) and is how all 16
# existing goals on this account read back (GoalCategory unset, confirmed by type inspection).
_GOAL_CATEGORIES = {"Unknown", "None", "Purchase", "AddToCart", "BeginCheckout", "Subscribe",
                    "SubmitLeadForm", "BookAppointment", "Signup", "RequestQuote",
                    "GetDirections", "OutboundClick", "Contact", "PageView", "Download", "Other"}


@mcp.tool()
def create_conversion_action(name: str, goal_type: str = "Event", value: float | None = None,
                             url_expression: str | None = None,
                             action_expression: str | None = None,
                             minimum_duration_seconds: int | None = None,
                             goal_category: str = "Other") -> dict:
    """Draft a conversion goal. goal_type: Event | Url | Duration (only supported values
    on the development account). Each goal_type REQUIRES its matching match-criteria arg — without
    one the goal creates "successfully" but can never record a conversion:
    - Url: url_expression (matched with UrlOperator="Contains")
    - Event: action_expression (matched with ActionOperator="Contains"; category/label
      expressions are out of scope here)
    - Duration: minimum_duration_seconds
    goal_category (ConversionGoalCategory enum) is REQUIRED live even though the WSDL marks
    it optional — omitting it faults "InvalidGoalCategory" (3347). Defaults to "Other", the
    only value live-verified valid across all three goal types (Task 25e probe); PageView
    also works on Url/Event but faults InvalidCategoryForGoalType (3349) on Duration. A
    category already claimed by an ACTIVE MS auto-created goal on the same UET tag faults
    SameCategoryAndTagNotAllowedForAutoGoalAndManualGoal (5667) — observed on Url goals only;
    the live acceptance matrix never exercised Event or Duration for this collision. The fault
    is keyed on category+tag rather than goal type, so it plausibly extends to Event/Duration
    too, but that's inference, not a tested fact — account state decides, not goal type.
    Lead-gen-meaningful values: SubmitLeadForm, BookAppointment, RequestQuote, Contact.
    NOTE: OfflineConversion goals are deliberately not created by this tool — create them
    in the MS Ads UI instead — because accounts that feed offline conversions from an
    external upload pipeline risk double-counting.
    ONE-WAY DOOR (Task 25f): there is no DeleteConversionGoals operation on this API
    (verified against the WSDL — every other entity family has a Delete*, this one
    doesn't) and UpdateConversionGoals(Status="Deleted") silently no-ops (partial_errors=[],
    goal reads back Active still) — so a created goal cannot be deleted. The only real
    levers are Status="Paused" and ExcludeFromBidding=True (both verified by read-back);
    full removal requires the MS Ads UI.

    Live-verified 2026-07-30."""
    if goal_type == "OfflineConversion":
        raise ValueError("OfflineConversion goals are deliberately not created by this tool — "
                         "create them in the MS Ads UI instead (double-count risk for accounts "
                         "feeding offline conversions from an external upload pipeline)")
    if goal_type not in _GOAL_CLASS:
        raise ValueError(f"unsupported goal_type '{goal_type}' — supported: {sorted(_GOAL_CLASS)} "
                         "(InStoreTransaction faulted on the development account — pilot not "
                         "enabled; AppDownload untested/same risk)")
    if goal_type == "Url" and not url_expression:
        raise ValueError("url_expression is required for goal_type='Url' — a goal with no match "
                         "criteria can never record a conversion")
    if goal_type == "Event" and not action_expression:
        raise ValueError("action_expression is required for goal_type='Event' — a goal with no match "
                         "criteria can never record a conversion")
    if goal_type == "Duration" and minimum_duration_seconds is None:
        raise ValueError("minimum_duration_seconds is required for goal_type='Duration' — a goal with "
                         "no match criteria can never record a conversion")
    if goal_category not in _GOAL_CATEGORIES:
        raise ValueError(f"goal_category must be one of {sorted(_GOAL_CATEGORIES)}, got '{goal_category}'")

    svc = client.svc("CampaignManagementService")
    tags = client.as_list(getattr(getattr(svc.GetUetTagsByIds(TagIds=None), "UetTags", None), "UetTag", None))
    if not tags:
        raise ValueError("no UET tag on the account — create one in MS Ads UI first")
    tag_id = tags[0].Id

    def apply():
        goal = client.blank(svc, _GOAL_CLASS[goal_type])
        goal.Type = goal_type
        goal.Name = name
        goal.TagId = tag_id
        goal.ConversionWindowInMinutes = 43200  # 30 days
        goal.CountType = "All"
        goal.Scope = "Account"
        goal.GoalCategory = goal_category
        if value is not None:
            rev = client.blank(svc, "ConversionGoalRevenue")
            rev.Type = "FixedValue"
            rev.Value = value
            goal.Revenue = rev
        if goal_type == "Url":
            goal.UrlExpression = url_expression
            goal.UrlOperator = "Contains"
        elif goal_type == "Event":
            goal.ActionExpression = action_expression
            goal.ActionOperator = "Contains"
        elif goal_type == "Duration":
            goal.MinimumDurationInSeconds = minimum_duration_seconds

        arr = svc.factory.create("ArrayOfConversionGoal")
        arr.ConversionGoal = [goal]
        resp = svc.AddConversionGoals(ConversionGoals=arr)
        ids = client.long_ids(getattr(resp, "ConversionGoalIds", None))
        return {"goal_ids": ids, "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("create_conversion_action",
                              {"name": name, "goal_type": goal_type, "value": value, "uet_tag_id": tag_id,
                               "url_expression": url_expression, "action_expression": action_expression,
                               "minimum_duration_seconds": minimum_duration_seconds,
                               "goal_category": goal_category}, apply)


@mcp.tool()
def set_conversion_action_primary_status(campaign_id: int, goal_id: int, primary: bool) -> dict:
    """Draft a campaign-goal association. MS models 'primary for this campaign' as a
    campaign conversion goal association (associate = campaign optimizes to it;
    disassociate = falls back to account-level goals).

    Live-verified 2026-07-30, both directions."""
    svc = client.svc("CampaignManagementService")
    action = "associate (make campaign-primary)" if primary else "disassociate"

    def apply():
        ccg = client.blank(svc, "CampaignConversionGoal")
        ccg.CampaignId = campaign_id
        ccg.GoalId = goal_id
        arr = svc.factory.create("ArrayOfCampaignConversionGoal")
        arr.CampaignConversionGoal = [ccg]
        # WSDL note: both Add/DeleteCampaignConversionGoalsRequest name this param
        # "CampaignConversionGoal" (singular) despite it carrying the array — verified
        # offline against campaignmanagement_service.xml.
        if primary:
            resp = svc.AddCampaignConversionGoals(CampaignConversionGoal=arr)
        else:
            resp = svc.DeleteCampaignConversionGoals(CampaignConversionGoal=arr)
        return {"partial_errors": client.partial_errors(resp)}

    return rails.create_draft("set_conversion_action_primary_status",
                              {"campaign_id": campaign_id, "goal_id": goal_id, "action": action}, apply)
