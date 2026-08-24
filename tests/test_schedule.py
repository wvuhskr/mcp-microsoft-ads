from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails
from mcp_microsoft_ads.tools import schedule

EXISTING = [NS(Id=301, Criterion=NS(Type="DayTime", Day="Monday", FromHour=0, ToHour=24)),
            NS(Id=302, Criterion=NS(Type="DayTime", Day="Tuesday", FromHour=0, ToHour=24))]


class FakeSvc:
    def __init__(self):
        self.deleted, self.added = [], []
        self.delete_criterion_types, self.get_criterion_types = [], []
        self.factory = NS(create=lambda t: NS(long=None, Type=None, Day=None, FromHour=None,
                                              ToHour=None, FromMinute=None, ToMinute=None,
                                              CampaignId=None, Criterion=None,
                                              CampaignCriterion=None))

    def GetCampaignCriterionsByIds(self, CampaignId, CampaignCriterionIds, CriterionType):
        self.get_criterion_types.append(CriterionType)
        rows = EXISTING if not self.deleted else []
        return NS(CampaignCriterions=NS(CampaignCriterion=rows))

    def DeleteCampaignCriterions(self, CampaignCriterionIds, CampaignId, CriterionType):
        self.deleted.append(CampaignCriterionIds)
        self.delete_criterion_types.append(CriterionType)
        return NS(IsMigrated=False, PartialErrors=None)

    def AddCampaignCriterions(self, CampaignCriterions, CriterionType):
        self.added.append(CampaignCriterions)
        return NS(CampaignCriterionIds=NS(long=[401, 402]), NestedPartialErrors=None, PartialErrors=None)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


WEEK = [{"day": d, "from_hour": 5, "to_hour": 22}
        for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]]


def test_preview_shows_replacement(fake):
    d = schedule.set_campaign_schedule(524066223, WEEK)
    assert d["preview"]["replacing_existing_rows"] == 2
    assert len(d["preview"]["new_rows"]) == 7


def test_apply_deletes_then_adds(fake):
    d = schedule.set_campaign_schedule(524066223, WEEK)
    out = rails.apply_draft(d["draft_id"])
    assert len(fake.deleted) == 1 and len(fake.added) == 1
    assert out["result"]["added_criterion_ids"] == [401, 402]
    added_wrappers = fake.added[0].CampaignCriterion
    assert len(added_wrappers) == 7
    assert all(cc.Type == "BiddableCampaignCriterion" for cc in added_wrappers)


def test_delete_uses_targets_not_daytime_criterion_type(fake):
    """Live fault (Task 25f): DeleteCampaignCriterions hard-faults CampaignCriterionTypeInvalid
    (4503) when passed the GET's specific type "DayTime" — Add/DeleteCampaignCriterions take
    the GROUP type "Targets" instead (matches AddCampaignCriterions on the add side and
    geo_write.remove_geo_target, both live-proven). EXISTING has 2 rows so
    _existing_daytime_ids is non-empty and the delete branch actually executes — this must
    not regress to a fixture with zero existing rows, which would skip the delete entirely
    and let this assertion pass for the wrong reason."""
    d = schedule.set_campaign_schedule(524066223, WEEK)
    rails.apply_draft(d["draft_id"])
    assert fake.deleted, "delete branch did not run — fixture must have existing rows"
    assert fake.delete_criterion_types == ["Targets"]
    # GET must stay on the SPECIFIC type; a future edit collapsing both to the same value
    # would pass the assertion above for the wrong reason.
    assert fake.get_criterion_types and all(t == "DayTime" for t in fake.get_criterion_types)


def test_partial_week_rejected(fake):
    with pytest.raises(ValueError, match="full week"):
        schedule.set_campaign_schedule(524066223, WEEK[:5])


def test_bad_hours_rejected(fake):
    bad = [dict(w) for w in WEEK]
    bad[0]["to_hour"] = 25
    with pytest.raises(ValueError, match="hour"):
        schedule.set_campaign_schedule(524066223, bad)


def test_duplicate_day_8_rows_rejected(fake):
    """8 rows: full week plus a duplicate Monday — days_given as a set still equals
    DAYS, so the old set-comparison guard alone would miss this."""
    dup = WEEK + [dict(WEEK[0])]
    with pytest.raises(rails.RailViolation, match="7"):
        schedule.set_campaign_schedule(524066223, dup)


def test_duplicate_and_missing_day_rejected(fake):
    """7 rows total but Monday appears twice and Sunday is missing — count check alone
    would miss this, since len(week) == 7."""
    bad = [dict(w) for w in WEEK if w["day"] != "Sunday"] + [dict(WEEK[0])]
    with pytest.raises(rails.RailViolation, match="Monday|Sunday"):
        schedule.set_campaign_schedule(524066223, bad)


def test_apply_aborts_when_delete_rejected(monkeypatch, tmp_path):
    """DeleteCampaignCriterionsResponse's only declared child is PartialErrors, so suds
    strips the wrapper (resp IS the ArrayOfBatchError). This delete-abort branch has never
    fired in any prior test/live run — it goes live once client.partial_errors (C1) reads
    the stripped shape correctly. Add must never be called after a rejected delete."""
    class RejectedDeleteFakeSvc(FakeSvc):
        def DeleteCampaignCriterions(self, CampaignCriterionIds, CampaignId, CriterionType):
            self.deleted.append(CampaignCriterionIds)
            self.delete_criterion_types.append(CriterionType)
            return NS(BatchError=[NS(Index=0, ErrorCode="CriterionNotFound", Code=4004, Message="gone")])

        def GetCampaignCriterionsByIds(self, CampaignId, CampaignCriterionIds, CriterionType):
            self.get_criterion_types.append(CriterionType)
            # Live-verified 2955 behavior: a mixed batch's valid ids still delete — simulate
            # id 301 gone, 302 surviving, once a delete attempt has been made.
            rows = [EXISTING[1]] if self.deleted else EXISTING
            return NS(CampaignCriterions=NS(CampaignCriterion=rows))

    svc = RejectedDeleteFakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = schedule.set_campaign_schedule(524066223, WEEK)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["partial_errors"] == [
        {"index": 0, "code": "CriterionNotFound", "number": 4004, "message": "gone"}
    ]
    assert "partially" in out["result"]["aborted"]
    assert out["result"]["surviving_criterion_ids"] == [302]
    assert not svc.added, "AddCampaignCriterions must not run after a rejected delete"


def test_apply_surfaces_nested_partial_errors(monkeypatch, tmp_path):
    """AddCampaignCriterions reports per-item errors via NestedPartialErrors, not
    PartialErrors — must surface in the applied result, never a blanket success."""
    class NestedErrFakeSvc(FakeSvc):
        def AddCampaignCriterions(self, CampaignCriterions, CriterionType):
            self.added.append(CampaignCriterions)
            return NS(CampaignCriterionIds=NS(long=[401, 402]), PartialErrors=None,
                      NestedPartialErrors=NS(BatchErrorCollection=NS(
                          Index=2, ErrorCode="CriterionError", Code=4001, Message="bad interval")))

    svc = NestedErrFakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = schedule.set_campaign_schedule(524066223, WEEK)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["partial_errors"] == [
        {"index": 2, "code": "CriterionError", "number": 4001, "message": "bad interval"}
    ]


def test_add_exception_raises_partial_write_error_with_deleted_ids(monkeypatch, tmp_path):
    """Plan item 11: delete (step 1) lands, AddCampaignCriterions (step 2) raises a
    real exception -- deleted_criterion_ids must survive in .partial, added must be
    empty since nothing landed there yet."""
    class ExplodingAddSvc(FakeSvc):
        def AddCampaignCriterions(self, CampaignCriterions, CriterionType):
            raise RuntimeError("SOAP fault: network reset")

    svc = ExplodingAddSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = schedule.set_campaign_schedule(524066223, WEEK)
    with pytest.raises(rails.PartialWriteError) as exc_info:
        rails.apply_draft(d["draft_id"])
    assert exc_info.value.partial == {"deleted_criterion_ids": [301, 302], "added_criterion_ids": []}
    assert "reconcile" in str(exc_info.value).lower()


def test_verify_exception_raises_partial_write_error_with_added_ids(monkeypatch, tmp_path):
    """Plan item 11: delete + add both land, but the verify readback raises -- the
    added ids must still be visible in .partial (nested try around the add call)."""
    class ExplodingVerifySvc(FakeSvc):
        def GetCampaignCriterionsByIds(self, CampaignId, CampaignCriterionIds, CriterionType):
            self.get_criterion_types.append(CriterionType)
            if not self.added:
                rows = EXISTING if not self.deleted else []
                return NS(CampaignCriterions=NS(CampaignCriterion=rows))
            raise RuntimeError("SOAP fault: verify read timed out")

    svc = ExplodingVerifySvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = schedule.set_campaign_schedule(524066223, WEEK)
    with pytest.raises(rails.PartialWriteError) as exc_info:
        rails.apply_draft(d["draft_id"])
    assert exc_info.value.partial == {"deleted_criterion_ids": [301, 302], "added_criterion_ids": [401, 402]}
    assert "reconcile" in str(exc_info.value).lower()


def test_add_raises_with_no_existing_rows_propagates_raw(monkeypatch, tmp_path):
    """Reviewer finding 1: with no existing DayTime rows, the delete branch never runs
    (deleted stays []) -- AddCampaignCriterions IS step 1 in that case, so an exception
    from it must propagate RAW, not as a PartialWriteError (nothing landed yet)."""
    class NoExistingExplodingAddSvc(FakeSvc):
        def GetCampaignCriterionsByIds(self, CampaignId, CampaignCriterionIds, CriterionType):
            self.get_criterion_types.append(CriterionType)
            return NS(CampaignCriterions=NS(CampaignCriterion=[]))

        def AddCampaignCriterions(self, CampaignCriterions, CriterionType):
            raise RuntimeError("SOAP fault: network reset")

    svc = NoExistingExplodingAddSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = schedule.set_campaign_schedule(524066223, WEEK)
    with pytest.raises(RuntimeError, match="network reset"):
        rails.apply_draft(d["draft_id"])


def test_add_ids_parse_raises_marks_add_landed(monkeypatch, tmp_path):
    """Reviewer finding 3: AddCampaignCriterions succeeds server-side, but parsing its
    response ids (client.long_ids) then raises -- .partial must not report
    added_criterion_ids=[] as if nothing landed (that could prompt a dangerous blind
    re-add); it must mark add_call_landed=True instead."""
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    def exploding_long_ids(container):
        raise RuntimeError("bad ids shape")

    monkeypatch.setattr(client, "long_ids", exploding_long_ids)

    d = schedule.set_campaign_schedule(524066223, WEEK)
    with pytest.raises(rails.PartialWriteError) as exc_info:
        rails.apply_draft(d["draft_id"])
    assert exc_info.value.partial == {
        "deleted_criterion_ids": [301, 302], "added_criterion_ids": [], "add_call_landed": True}
    assert "reconcile" in str(exc_info.value).lower()


def test_bare_single_existing_row(monkeypatch, tmp_path):
    """One existing DayTime row collapses to a bare object (client.as_list) — the REPLACE
    delete step must still see it, or the add would overlap-fault against a live row."""
    svc = FakeSvc()
    svc.GetCampaignCriterionsByIds = lambda CampaignId, CampaignCriterionIds, CriterionType: NS(
        CampaignCriterions=NS(CampaignCriterion=NS(Id=301, Criterion=NS(Type="DayTime", Day="Monday"))))
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    d = schedule.set_campaign_schedule(524066223, WEEK)
    assert d["preview"]["replacing_existing_rows"] == 1
    rails.apply_draft(d["draft_id"])
    assert svc.deleted[0].long == [301]
