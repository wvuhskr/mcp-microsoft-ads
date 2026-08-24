"""Ad-schedule REPLACE. Google's MCP shipped append-only and hit
AD_SCHEDULE_TIME_INTERVALS_OVERLAP — delete existing DayTime criterions first, always."""
from .. import client, rails, verify
from ..app import mcp

DAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


def _existing_daytime_ids(svc, campaign_id: int) -> list[int]:
    r = svc.GetCampaignCriterionsByIds(CampaignId=campaign_id, CampaignCriterionIds=None,
                                       CriterionType="DayTime")
    rows = client.as_list(getattr(getattr(r, "CampaignCriterions", None), "CampaignCriterion", None))
    return [c.Id for c in rows]


@mcp.tool()
def set_campaign_schedule(campaign_id: int, week: list[dict]) -> dict:
    """REPLACE a campaign's ad schedule. week = 7 dicts {day, from_hour, to_hour}
    (0-23 hour clock; to_hour is the hour service stops, e.g. to_hour=22 serves until
    22:00). MUST cover all 7 days — a campaign with any DayTime rows serves only inside
    them, so a partial week silently narrows delivery. Deletes existing DayTime
    criterions then adds the new set. Returns a draft; apply with confirm_and_apply.

    Live-verified 2026-07-30 including the REPLACE delete path (7 rows deleted/7 added,
    read-back verified).

    On a mid-apply failure (add or the verify readback raises after the existing
    schedule was deleted) the error carries every landed ID (deleted_criterion_ids,
    added_criterion_ids) — reconcile account state before retrying (a retry re-runs
    ALL steps, including re-deleting whatever the previous attempt left)."""
    day_list = [w.get("day") for w in week]
    days_given = set(day_list)
    dupes = sorted({d for d in day_list if day_list.count(d) > 1})
    if dupes:
        # Covers both an 8-row week (duplicate + full day set) and a 7-row week where a
        # duplicate crowds out another day — the pure "missing days, no duplicates" case
        # below keeps its existing ValueError so that message doesn't change.
        missing = sorted(DAYS - days_given)
        raise rails.RailViolation(
            f"week must have exactly 7 entries covering Monday..Sunday once each — "
            f"got {len(week)} entries, duplicated {dupes}, missing {missing}")
    if days_given != DAYS:
        raise rails.RailViolation(f"full week required — missing {sorted(DAYS - days_given)}")
    for w in week:
        if not (0 <= w["from_hour"] < w["to_hour"] <= 23):
            raise rails.RailViolation(f"hour range invalid in {w} (0 <= from_hour < to_hour <= 23)")

    svc = client.svc("CampaignManagementService")
    existing = _existing_daytime_ids(svc, campaign_id)
    new_rows = [{"day": w["day"], "from_hour": w["from_hour"], "to_hour": w["to_hour"]} for w in week]

    def apply():
        deleted = []
        if existing:
            ids = svc.factory.create("ns3:ArrayOflong")  # live-verified: bare/ns4 raise TypeNotFound (Task 15)
            ids.long = existing
            # CriterionType taxonomy (Task 25f, live A/B probe): GetCampaignCriterionsByIds
            # takes the SPECIFIC type ("DayTime", used in _existing_daytime_ids above), but
            # Add/DeleteCampaignCriterions take the GROUP type "Targets" instead — "DayTime"
            # here hard-faults CampaignCriterionTypeInvalid (4503).
            resp_d = svc.DeleteCampaignCriterions(CampaignCriterionIds=ids, CampaignId=campaign_id,
                                                  CriterionType="Targets")
            errs_d = client.partial_errors(resp_d)
            if errs_d:
                # Live-verified 2026-07-30 (post-C1 re-probe, throwaway campaign): MS does NOT
                # silently ignore unknown delete ids — each bad id returns partial error 2955
                # CampaignServiceInvalidCriterionId at its batch index, but valid ids in the
                # SAME batch still delete. So a rejected delete can leave the campaign
                # partially stripped, not untouched — re-read what's actually left and say so
                # instead of a bare "aborted" that implies nothing changed.
                surviving = _existing_daytime_ids(svc, campaign_id)
                return {
                    "partial_errors": errs_d,
                    "aborted": (
                        f"delete partially failed; {len(surviving)} existing DayTime row(s) "
                        "may have survived (or been left as the only coverage) — schedule is "
                        "not fully replaced, re-check and re-apply"
                    ),
                    "surviving_criterion_ids": surviving,
                }
            deleted = existing

        # step1_landed: a delete actually ran. When existing is empty, nothing was
        # deleted -- AddCampaignCriterions below IS step 1 in that case (reviewer
        # finding 1), and an exception from it must propagate RAW, not as a
        # PartialWriteError (nothing landed yet to reconcile).
        step1_landed = bool(deleted)
        partial = {"deleted_criterion_ids": deleted, "added_criterion_ids": []}

        # Widened try (reviewer finding 2): payload BUILDING (the crits loop/array
        # creation), not just the SOAP call, must be inside the guard -- a raise while
        # building the criterions after a real step-1 delete already landed is the
        # exact failure class this closes.
        try:
            crits = []
            for w in week:
                # live-verified pattern (Task 15): bare factory.create() defaults carry
                # junk enum values that fail server-side deserialization — blank() first.
                crit = client.blank(svc, "DayTimeCriterion")
                crit.Type = "DayTimeCriterion"
                crit.Day = w["day"]
                crit.FromHour = w["from_hour"]
                crit.ToHour = w["to_hour"]
                crit.FromMinute = "Zero"   # enum string, NOT int (opposite of Google's MCP)
                crit.ToMinute = "Zero"
                cc = client.blank(svc, "BiddableCampaignCriterion")
                cc.Type = "BiddableCampaignCriterion"
                cc.CampaignId = campaign_id
                cc.Criterion = crit
                crits.append(cc)
            arr = svc.factory.create("ArrayOfCampaignCriterion")
            arr.CampaignCriterion = crits
            resp = svc.AddCampaignCriterions(CampaignCriterions=arr, CriterionType="Targets")
        except Exception as e:
            if not step1_landed:
                raise  # AddCampaignCriterions was step 1 itself -- nothing landed
            # Step 1 (delete) landed -- carry the deleted ids so a mid-apply exception
            # on the add itself doesn't erase them (plan item 11). Nothing added yet.
            raise rails.PartialWriteError(
                "criterion add failed after existing schedule was deleted",
                partial=partial) from e

        try:
            ids_out = client.long_ids(getattr(resp, "CampaignCriterionIds", None))
            partial["added_criterion_ids"] = ids_out
        except Exception as e:
            # Reviewer finding 3: the SOAP call above already succeeded -- the
            # criterions exist server-side even though id-parsing then failed.
            # Reporting added_criterion_ids=[] here would read as "nothing added" and
            # could prompt a dangerous blind re-add; add_call_landed=True says
            # otherwise. This is a genuine landed-write regardless of step1_landed
            # (the add itself just succeeded), so always a PartialWriteError.
            partial["add_call_landed"] = True
            raise rails.PartialWriteError(
                "criterion add landed but response id parsing failed", partial=partial) from e

        try:
            post = verify.verify_fields(
                lambda: type("X", (), {"count": len(_existing_daytime_ids(svc, campaign_id))})(),
                {"count": 7})
        except Exception as e:
            # Delete (if any) AND add both landed -- carry the added ids too (plan item
            # 11: the verify readback is also "after step 1"; the nested try above is
            # what makes ids_out known here even though the add call itself didn't
            # raise). Always a PartialWriteError -- the add just landed regardless of
            # whether a delete preceded it.
            raise rails.PartialWriteError(
                "verify readback failed after add landed", partial=partial) from e
        return {"deleted_criterion_ids": deleted, "added_criterion_ids": ids_out,
                "partial_errors": client.partial_errors(resp), "verify": post}

    return rails.create_draft("set_campaign_schedule",
                              {"campaign_id": campaign_id, "replacing_existing_rows": len(existing),
                               "new_rows": new_rows}, apply)
