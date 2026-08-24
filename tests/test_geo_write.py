from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails
from mcp_microsoft_ads.tools import geo_write


class FakeSvc:
    def __init__(self):
        self.added, self.deleted = [], []
        self.factory = NS(create=lambda t: NS(long=None, Type=None, LocationId=None,
                                              CampaignId=None, Criterion=None,
                                              CampaignCriterion=None))

    def AddCampaignCriterions(self, CampaignCriterions, CriterionType):
        self.added.append((CampaignCriterions, CriterionType))
        return NS(CampaignCriterionIds=NS(long=[601]), PartialErrors=None, NestedPartialErrors=None)

    def DeleteCampaignCriterions(self, CampaignCriterionIds, CampaignId, CriterionType):
        self.deleted.append((CampaignCriterionIds, CampaignId, CriterionType))
        return NS(PartialErrors=None, IsMigrated=False)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


def test_exclude_drafts_and_applies(fake):
    d = geo_write.exclude_geo_target(524066223, 81237)
    assert d["preview"]["location_id"] == 81237
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["criterion_ids"] == [601]
    payload, crit_type = fake.added[0]
    assert crit_type == "Targets"
    cc = payload.CampaignCriterion[0]
    assert cc.Type == "NegativeCampaignCriterion"
    assert cc.CampaignId == 524066223
    assert cc.Criterion.Type == "LocationCriterion"
    assert cc.Criterion.LocationId == 81237


def test_exclude_bare_id_not_truncated(fake, monkeypatch):
    """exclude_geo_target always adds exactly one criterion — suds unmarshals a
    single-item `long` array as a bare int, not a list (client.as_list); must
    still surface as [601], not be dropped or mishandled."""
    class SingleFakeSvc(FakeSvc):
        def AddCampaignCriterions(self, CampaignCriterions, CriterionType):
            self.added.append((CampaignCriterions, CriterionType))
            return NS(CampaignCriterionIds=NS(long=601), PartialErrors=None,  # bare int, not [601]
                      NestedPartialErrors=None)

    svc = SingleFakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    d = geo_write.exclude_geo_target(524066223, 81237)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["criterion_ids"] == [601]


def test_remove_geo(fake):
    d = geo_write.remove_geo_target(524066223, 601)
    rails.apply_draft(d["draft_id"])
    assert len(fake.deleted) == 1
    ids, campaign_id, crit_type = fake.deleted[0]
    assert ids.long == [601]
    assert campaign_id == 524066223
    assert crit_type == "Targets"


def test_remove_geo_surfaces_partial_errors(monkeypatch, tmp_path):
    """DeleteCampaignCriterionsResponse's only declared child is PartialErrors, so suds
    strips the wrapper (resp IS the ArrayOfBatchError) — must still surface, not silently
    swallow, a rejected delete."""
    class RejectedFakeSvc(FakeSvc):
        def DeleteCampaignCriterions(self, CampaignCriterionIds, CampaignId, CriterionType):
            self.deleted.append((CampaignCriterionIds, CampaignId, CriterionType))
            return NS(BatchError=[NS(Index=0, ErrorCode="CriterionNotFound", Code=4004, Message="gone")])

    svc = RejectedFakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = geo_write.remove_geo_target(524066223, 601)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["partial_errors"] == [
        {"index": 0, "code": "CriterionNotFound", "number": 4004, "message": "gone"}
    ]
