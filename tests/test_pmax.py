from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails, settings
from mcp_microsoft_ads.tools import pmax


class FakeSvc:
    def __init__(self, media_meta=None):
        # blank() no-ops on plain NS (no __keylist__); every field we touch is set
        # explicitly by the tool code, matching the audiences.py/schedule.py precedent.
        self.factory = NS(create=lambda t: NS())
        self.asset_groups = []
        # media id -> MediaType, as GetMediaMetaDataByIds would answer. Default covers
        # image_media_ids=[901] used by ARGS below (Image191x100 -> Landscape).
        self.media_meta = media_meta if media_meta is not None else {901: "Image191x100"}

    def AddCampaigns(self, AccountId, Campaigns):
        # real factory container: attribute access (Campaigns.Campaign), not dict-style —
        # binding deviation 1 (blank-built payloads in real factory Array containers).
        self.campaign = Campaigns.Campaign[0]
        return NS(CampaignIds=NS(long=[888]), PartialErrors=None)

    def AddAssetGroups(self, CampaignId, AssetGroups):
        self.asset_groups.append((CampaignId, AssetGroups.AssetGroup[0]))
        return NS(AssetGroupIds=NS(long=[71]), PartialErrors=None)

    def AddBidStrategies(self, BidStrategies):
        self.bid_strategy = BidStrategies.BidStrategy[0]
        return NS(BidStrategyIds=NS(long=[31]), PartialErrors=None)

    def GetMediaMetaDataByIds(self, MediaIds, ReturnAdditionalFields):
        # Real KEPT-wrapper shape (GetMediaMetaDataByIdsResponse declares TWO children,
        # so unlike GetMediaMetaDataByAccountId the suds ONE-CHILD RULE does NOT strip
        # it — see this file's header note: fakes must encode the SUDS shape, not the
        # WSDL shape). Rows are positionally aligned with the requested ids; an id
        # absent from media_meta yields a NIL row plus a PartialErrors entry at that
        # index (code 4000, CampaignServiceMediaIdInvalid) — matches the controller's
        # live probe 2026-07-31. Collapses a single-row result to a bare object/None
        # (not a 1-list), same suds single-item-array behavior client.as_list
        # normalizes everywhere else.
        self.requested_media_ids = list(MediaIds.long)
        rows, errors = [], []
        for i, mid in enumerate(self.requested_media_ids):
            if mid in self.media_meta:
                rows.append(NS(Id=mid, MediaType=self.media_meta[mid]))
            else:
                rows.append(None)
                errors.append(NS(Index=i, ErrorCode="CampaignServiceMediaIdInvalid", Code=4000,
                                 Message="An image or icon with the specified ID was not found "
                                         "in the account's media library."))
        return NS(MediaMetaData=NS(MediaMetaData=rows[0] if len(rows) == 1 else rows),
                 PartialErrors=NS(BatchError=errors[0] if len(errors) == 1 else errors) if errors else None)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


# ponytail: brief's verbatim ARGS used target_cpa=120, which exceeds the real
# MS_ADS_MAX_CPC=50 cap (see test_adgroups_write.py precedent for the same fix) —
# using 45 (under cap) to keep the suite internally consistent with test_rails.py.
ARGS = dict(name="Test PMax", daily_budget=50, target_cpa=45,
            final_url="https://example.com", headlines=["AC Repair", "Fast Service", "Local Pros"],
            long_headlines=["Example Region AC Repair Experts"],
            descriptions=["Call Example today.", "Serving Example County."],
            business_name="Example Home Services", image_media_ids=[901])


def test_pmax_created_paused_with_tcpa(fake):
    d = pmax.create_pmax_campaign(**ARGS)
    assert d["preview"]["Status"] == "Paused"
    assert d["preview"]["TargetCpa"] == 45
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["campaign_ids"] == [888]
    assert out["result"]["asset_group_ids"] == [71]
    assert fake.campaign.Status == "Paused"
    assert fake.campaign.CampaignType == "PerformanceMax"
    assert fake.campaign.BiddingScheme.TargetCpa == 45
    assert fake.campaign.BiddingScheme.Type == "MaxConversions"
    ag = fake.asset_groups[0][1]
    assert fake.asset_groups[0][0] == 888
    assert ag.Status == "Paused"
    assert ag.BusinessName == "Example Home Services"
    assert ag.Images.AssetLink[0].Asset.Id == 901
    assert ag.Images.AssetLink[0].Asset.Type == "ImageAsset"
    assert ag.Images.AssetLink[0].Asset.SubType == "LandscapeImageMedia"
    assert ag.Headlines.AssetLink[0].Asset.Text == "AC Repair"
    assert fake.requested_media_ids == [901]  # GetMediaMetaDataByIds asked for exactly image_media_ids


def test_image_subtype_matches_media_type(monkeypatch, tmp_path):
    """Defect 2: SubType must track each media item's own MediaType (live-observed:
    Image1x1 -> SquareImageMedia, Image191x100 -> LandscapeImageMedia), not a
    hardcoded landscape default — today both come back landscape."""
    svc = FakeSvc(media_meta={901: "Image1x1", 902: "Image191x100"})
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = pmax.create_pmax_campaign(**{**ARGS, "image_media_ids": [901, 902]})
    rails.apply_draft(d["draft_id"])
    links = svc.asset_groups[0][1].Images.AssetLink
    assert links[0].Asset.Id == 901
    assert links[0].Asset.SubType == "SquareImageMedia"
    assert links[1].Asset.Id == 902
    assert links[1].Asset.SubType == "LandscapeImageMedia"


def test_unmapped_media_type_raises(monkeypatch, tmp_path):
    """GenericImage/Image4x1 have no evidenced SubType — must raise, not guess."""
    svc = FakeSvc(media_meta={901: "GenericImage"})
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = pmax.create_pmax_campaign(**ARGS)
    with pytest.raises(ValueError, match="no evidenced SubType mapping"):
        rails.apply_draft(d["draft_id"])


def test_media_id_absent_from_library_raises(monkeypatch, tmp_path):
    """A media id with no row at all in GetMediaMetaDataByIds must raise,
    not silently fall back to landscape."""
    svc = FakeSvc(media_meta={})
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = pmax.create_pmax_campaign(**ARGS)
    with pytest.raises(ValueError, match="not found in the account media library"):
        rails.apply_draft(d["draft_id"])


def test_media_id_invalid_in_mixed_batch_raises_named_id_and_ms_text(monkeypatch, tmp_path):
    """Finding 3: every absent-id test above uses media_meta={} with a single id, so
    FakeSvc's single-row collapse (len(rows) == 1) hands the code a bare None and the
    loop takes its length-fallback branch (i >= len(rows)) — never the NIL-row-AT-ITS-
    INDEX branch a real mixed batch produces. Use a populated 3-id batch (901, 903 valid;
    999 not) so rows is a genuine 3-list with a None at index 1, and assert the raise both
    names the bad id and carries MS's own PartialErrors text verbatim (not just this
    code's own generic wording) — the two probe facts the by-ids docstring rests on,
    previously untested against a populated row list."""
    svc = FakeSvc(media_meta={901: "Image1x1", 903: "Image191x100"})
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = pmax.create_pmax_campaign(**{**ARGS, "image_media_ids": [901, 999, 903]})
    with pytest.raises(ValueError, match="999") as exc_info:
        rails.apply_draft(d["draft_id"])
    assert "not found in the account's media library" in str(exc_info.value)
    assert not hasattr(svc, "campaign"), "AddCampaigns ran before image validation raised"


def test_all_ids_invalid_batch_nils_whole_element(monkeypatch, tmp_path):
    """Re-review Minor 1: MediaMetaData is nillable at the RESPONSE level (WSDL) — an
    all-ids-invalid batch can nil the whole element, not just individual rows. Unprobed
    live; this is the one fake allowed to encode the WSDL possibility. Pre-guard code
    (bare resp.MediaMetaData.MediaMetaData deref) raises AttributeError here; the
    double-getattr guard must turn it into the clean not-found ValueError instead."""
    svc = FakeSvc(media_meta={})
    svc.GetMediaMetaDataByIds = lambda MediaIds, ReturnAdditionalFields: NS(
        MediaMetaData=None, PartialErrors=None)
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = pmax.create_pmax_campaign(**ARGS)
    with pytest.raises(ValueError, match="not found in the account media library"):
        rails.apply_draft(d["draft_id"])
    assert not hasattr(svc, "campaign"), "AddCampaigns ran before image validation raised"


def test_image_validation_precedes_campaign_add(monkeypatch, tmp_path):
    """Image validation must run before AddCampaigns fires — a bad media id must
    not leave a live campaign behind with no rollback. Absent-from-library id
    reuses test_media_id_absent_from_library_raises' fixture shape; the new
    assertion is that AddCampaigns never ran."""
    svc = FakeSvc(media_meta={})
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = pmax.create_pmax_campaign(**ARGS)
    with pytest.raises(ValueError, match="901"):
        rails.apply_draft(d["draft_id"])
    assert not hasattr(svc, "campaign"), "AddCampaigns ran before image validation raised"


def test_images_required(fake):
    with pytest.raises(ValueError, match="image_media_ids"):
        pmax.create_pmax_campaign(**{**ARGS, "image_media_ids": []})


def test_tcpa_required(fake):
    """Default require_pmax_target_cpa setting is true -> no target_cpa raises,
    naming the settings key."""
    with pytest.raises(ValueError, match="require_pmax_target_cpa"):
        pmax.create_pmax_campaign(**{**ARGS, "target_cpa": None})


def test_tcpa_not_required_when_setting_false(fake, monkeypatch):
    """require_pmax_target_cpa=false + no target_cpa builds a MaxConversions scheme
    WITHOUT setting TargetCpa at all (not TargetCpa=None — the field is absent)."""
    monkeypatch.setattr(settings, "require_pmax_target_cpa", lambda: False)
    args = {**ARGS, "target_cpa": None}
    d = pmax.create_pmax_campaign(**args)
    assert d["preview"]["TargetCpa"] is None
    rails.apply_draft(d["draft_id"])
    assert fake.campaign.BiddingScheme.Type == "MaxConversions"
    assert not hasattr(fake.campaign.BiddingScheme, "TargetCpa")


def test_tcpa_explicit_value_unchanged_when_setting_false(fake, monkeypatch):
    """An explicit target_cpa still works, and rails.check_bid still runs, regardless
    of the require_pmax_target_cpa setting."""
    monkeypatch.setattr(settings, "require_pmax_target_cpa", lambda: False)
    d = pmax.create_pmax_campaign(**ARGS)
    rails.apply_draft(d["draft_id"])
    assert fake.campaign.BiddingScheme.TargetCpa == 45


def test_pmax_time_zone_param(fake):
    d = pmax.create_pmax_campaign(**{**ARGS, "time_zone": "PacificTimeUSCanada"})
    assert d["preview"]["TimeZone"] == "PacificTimeUSCanada"
    rails.apply_draft(d["draft_id"])
    assert fake.campaign.TimeZone == "PacificTimeUSCanada"


def test_pmax_time_zone_defaults_eastern(fake):
    d = pmax.create_pmax_campaign(**ARGS)
    assert d["preview"]["TimeZone"] == "EasternTimeUSCanada"
    rails.apply_draft(d["draft_id"])
    assert fake.campaign.TimeZone == "EasternTimeUSCanada"


def test_aborts_asset_group_on_campaign_add_failure(monkeypatch, tmp_path):
    class FailSvc(FakeSvc):
        def AddCampaigns(self, AccountId, Campaigns):
            self.campaign = Campaigns.Campaign[0]
            return NS(CampaignIds=None,
                      PartialErrors=NS(BatchError=NS(Index=0, ErrorCode="CampaignError",
                                                     Code=1001, Message="bad campaign")))

    svc = FailSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = pmax.create_pmax_campaign(**ARGS)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["aborted"]
    assert out["result"]["campaign_ids"] == []
    assert svc.asset_groups == []


def test_asset_group_add_exception_raises_partial_write_error(monkeypatch, tmp_path):
    """Plan item 11: campaign add (step 1) lands, then asset-group add (step 2) blows
    up with a real exception (not a PARTIAL-ERROR abort dict) -- the campaign_ids must
    not vanish, so the tool must raise PartialWriteError carrying them."""
    class ExplodingSvc(FakeSvc):
        def AddAssetGroups(self, CampaignId, AssetGroups):
            raise RuntimeError("SOAP fault: network reset")

    svc = ExplodingSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))

    d = pmax.create_pmax_campaign(**ARGS)
    with pytest.raises(rails.PartialWriteError) as exc_info:
        rails.apply_draft(d["draft_id"])
    assert exc_info.value.partial == {"campaign_ids": [888], "asset_group_ids": []}
    assert "888" in str(exc_info.value)
    assert "reconcile" in str(exc_info.value).lower()


def test_portfolio_strategy(fake):
    d = pmax.create_portfolio_bidding_strategy("Shared tCPA", "MaxConversions", target_cpa=40)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["bid_strategy_ids"] == [31]
    assert fake.bid_strategy.Name == "Shared tCPA"
    assert fake.bid_strategy.BiddingScheme.Type == "MaxConversions"
    assert fake.bid_strategy.BiddingScheme.TargetCpa == 40


def test_portfolio_strategy_bad_type_rejected(fake):
    with pytest.raises(ValueError, match="strategy_type"):
        pmax.create_portfolio_bidding_strategy("Bad", "NotARealType")


def test_portfolio_strategy_tcpa_rejected_on_non_maxconversions(fake):
    with pytest.raises(ValueError, match="target_cpa"):
        pmax.create_portfolio_bidding_strategy("Bad", "MaxClicks", target_cpa=100)


# Rail-wiring tests: prove rails.check_* is actually called from these tools, not just
# present in rails.py's own suite (test_rails.py). rails.RailViolation subclasses
# ValueError, so pytest.raises(ValueError) alone would also pass on the tools' own plain
# ValueError guards (image_media_ids/target_cpa/strategy_type) — assert RailViolation
# specifically. All rails fire at draft time, before create_draft/apply(), so no svc
# call should happen at all; asserting that is stronger than asserting the exception.

def test_pmax_budget_over_cap_blocked(fake):
    with pytest.raises(rails.RailViolation, match="exceeds cap"):
        pmax.create_pmax_campaign(**{**ARGS, "daily_budget": 1001})  # default cap 1000
    assert not hasattr(fake, "campaign")


def test_pmax_tcpa_over_cap_blocked(fake):
    with pytest.raises(rails.RailViolation, match="exceeds cap"):
        pmax.create_pmax_campaign(**{**ARGS, "target_cpa": 51})  # default cap 50
    assert not hasattr(fake, "campaign")


def test_pmax_blocked_term_in_copy_blocked(fake, monkeypatch):
    """descriptions is the LAST field in [name, business_name] + headlines +
    long_headlines + descriptions passed to check_content — proves the whole
    concatenation reaches it, not just the first entries. blocked_terms is
    settings-driven now (empty by default in tests — see conftest); set it explicitly
    to exercise the block."""
    monkeypatch.setattr(settings, "blocked_terms", lambda: ("sewage",))
    with pytest.raises(rails.RailViolation, match="sewage"):
        pmax.create_pmax_campaign(**{**ARGS, "descriptions": ["No sewage cleanup here"]})
    assert not hasattr(fake, "campaign")


def test_portfolio_strategy_tcpa_over_cap_blocked(fake):
    with pytest.raises(rails.RailViolation, match="exceeds cap"):
        pmax.create_portfolio_bidding_strategy("Shared tCPA", "MaxConversions", target_cpa=51)
    assert not hasattr(fake, "bid_strategy")
