from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails, settings
from mcp_microsoft_ads.tools import extensions_write


class FakeSvc:
    def __init__(self):
        self.assoc = []
        self.deleted_assoc = []
        self.deleted_ext = []
        self.call_order = []  # track order of DeleteAdExtensionsAssociations vs DeleteAdExtensions
        # generic blank stand-in: client.blank() no-ops (no __keylist__) and every
        # attribute gets set explicitly afterward by extensions_write, so no fields
        # need pre-populating here (mirrors tests/test_ads_write.py's FakeSvc).
        self.factory = NS(create=lambda t: NS())

    def AddAdExtensions(self, AccountId, AdExtensions):
        self.added = AdExtensions
        return NS(AdExtensionIdentities=NS(AdExtensionIdentity=[NS(Id=71)]),
                  NestedPartialErrors=None, PartialErrors=None)

    def SetAdExtensionsAssociations(self, AccountId, AdExtensionIdToEntityIdAssociations, AssociationType):
        self.assoc.append(AdExtensionIdToEntityIdAssociations)
        return NS(PartialErrors=None)

    def DeleteAdExtensionsAssociations(self, AccountId, AdExtensionIdToEntityIdAssociations, AssociationType):
        self.deleted_assoc.append(AdExtensionIdToEntityIdAssociations)
        self.call_order.append("disassoc")
        # Clean by default (mirrors AddAdExtensions/SetAdExtensionsAssociations above) --
        # a disassoc-side error is a step-1 abort case and gets its own dedicated test,
        # since step 1 failing now must prevent DeleteAdExtensions from ever firing.
        return NS(PartialErrors=None)

    def DeleteAdExtensions(self, AccountId, AdExtensionIds):
        self.deleted_ext.append(AdExtensionIds)
        self.call_order.append("delete")
        pe = NS(BatchError=[NS(Code=200, Index=0, Message="delete error", ErrorCode="DeleteError")])
        return NS(PartialErrors=pe)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


def test_sitelinks_add_then_associate(fake):
    d = extensions_write.draft_sitelinks(
        524066223, [{"text": "Coupons", "url": "https://example.com/coupons"}])
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["extension_ids"] == [71]
    assert out["result"]["associated_campaign_id"] == 524066223
    assert out["result"]["partial_errors"] == []
    assert len(fake.assoc) == 1
    a = fake.assoc[0].AdExtensionIdToEntityIdAssociation[0]
    assert a.AdExtensionId == 71
    assert a.EntityId == 524066223
    ext = fake.added.AdExtension[0]
    assert ext.Type == "SitelinkAdExtension"
    assert ext.DisplayText == "Coupons"
    assert ext.FinalUrls.string == ["https://example.com/coupons"]


def test_blocklist_on_sitelink_text(fake, monkeypatch):
    monkeypatch.setattr(settings, "blocked_terms", lambda: ("sewage", "backup"))
    with pytest.raises(rails.RailViolation):
        extensions_write.draft_sitelinks(1, [{"text": "Sewage Backup", "url": "https://x.com"}])


def test_callouts(fake):
    d = extensions_write.create_callouts(524066223, ["Family Owned", "24/7 Answering"])
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["extension_ids"] == [71]
    exts = fake.added.AdExtension
    assert [e.Text for e in exts] == ["Family Owned", "24/7 Answering"]
    assert all(e.Type == "CalloutAdExtension" for e in exts)


def test_callout_too_long_rejected(fake):
    with pytest.raises(ValueError, match="25 chars"):
        extensions_write.create_callouts(1, ["x" * 26])


def test_structured_snippets(fake):
    d = extensions_write.create_structured_snippets(
        524066223, "Services", ["AC Repair", "Furnace Repair", "Duct Cleaning"])
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["extension_ids"] == [71]
    ext = fake.added.AdExtension[0]
    assert ext.Type == "StructuredSnippetAdExtension"
    assert ext.Header == "Services"
    assert ext.Values.string == ["AC Repair", "Furnace Repair", "Duct Cleaning"]


def test_structured_snippets_needs_3_values(fake):
    with pytest.raises(ValueError, match=">= 3"):
        extensions_write.create_structured_snippets(1, "Services", ["A", "B"])


def test_remove_extension(fake):
    d = extensions_write.remove_extension(71, 524066223)
    out = rails.apply_draft(d["draft_id"])
    errors = out["result"]["partial_errors"]
    # Disassoc is clean by default (see FakeSvc) -- only the delete sweep's error
    # surfaces here, since a disassoc error is now a step-1 abort (own test below).
    assert len(errors) == 1
    assert errors[0]["message"] == "delete error"
    assert errors[0]["code"] == "DeleteError"
    # Verify call order: disassoc must come before delete.
    assert fake.call_order == ["disassoc", "delete"]
    a = fake.deleted_assoc[0].AdExtensionIdToEntityIdAssociation[0]
    assert a.AdExtensionId == 71
    assert a.EntityId == 524066223
    assert fake.deleted_ext[0].long == [71]


def test_associate_exception_raises_partial_write_error(fake, monkeypatch):
    """Plan item 11: library add (step 1) lands, then SetAdExtensionsAssociations
    (step 2) raises a real exception (not a PARTIAL-ERROR) -- the landed extension_ids
    must not vanish, so the tool must raise PartialWriteError carrying them."""
    def exploding_associate(AccountId, AdExtensionIdToEntityIdAssociations, AssociationType):
        raise RuntimeError("SOAP fault: connection reset")

    monkeypatch.setattr(fake, "SetAdExtensionsAssociations", exploding_associate)
    d = extensions_write.create_callouts(524066223, ["Family Owned"])
    with pytest.raises(rails.PartialWriteError) as exc_info:
        rails.apply_draft(d["draft_id"])
    assert exc_info.value.partial == {"extension_ids": [71], "associated": False}
    assert "71" in str(exc_info.value)
    assert "reconcile" in str(exc_info.value).lower()


def test_remove_extension_delete_exception_raises_partial_write_error(fake, monkeypatch):
    """Plan item 11 / remove_extension nuance: step 1 is DeleteAdExtensionsAssociations;
    a raise from the DeleteAdExtensions call (step 2) means disassociation landed but
    deletion did not -- must carry both facts in .partial."""
    def exploding_delete(AccountId, AdExtensionIds):
        raise RuntimeError("SOAP fault: timeout")

    monkeypatch.setattr(fake, "DeleteAdExtensions", exploding_delete)
    d = extensions_write.remove_extension(71, 524066223)
    with pytest.raises(rails.PartialWriteError) as exc_info:
        rails.apply_draft(d["draft_id"])
    assert exc_info.value.partial == {
        "disassociated": True, "deleted": False, "extension_id": 71, "campaign_id": 524066223}
    assert "reconcile" in str(exc_info.value).lower()


def test_remove_extension_disassociate_exception_raises_raw(fake, monkeypatch):
    """Brief minimum case (a): a HARD exception from step 1 itself
    (DeleteAdExtensionsAssociations raising, not a batch PartialError) means nothing
    landed -- must propagate RAW, not wrapped in PartialWriteError, and step 2
    (DeleteAdExtensions) must never be attempted."""
    def exploding_disassoc(AccountId, AdExtensionIdToEntityIdAssociations, AssociationType):
        fake.call_order.append("disassoc")
        raise RuntimeError("SOAP fault: connection reset")

    monkeypatch.setattr(fake, "DeleteAdExtensionsAssociations", exploding_disassoc)
    d = extensions_write.remove_extension(71, 524066223)
    with pytest.raises(RuntimeError, match="connection reset"):
        rails.apply_draft(d["draft_id"])
    assert fake.call_order == ["disassoc"]
    assert fake.deleted_ext == []


def test_remove_extension_aborts_when_disassociate_has_partial_errors(fake, monkeypatch):
    """remove_extension nuance of the step-1 abort convention (_add_and_associate's
    "library add failed" abort, schedule.py's "delete partially failed" abort): a
    disassociation (step 1) that comes back with a batch PartialError has NOT
    landed -- DeleteAdExtensions (step 2) must not even be attempted, and the
    result is a plain abort dict, not a PartialWriteError (nothing landed to
    reconcile)."""
    def failing_disassoc(AccountId, AdExtensionIdToEntityIdAssociations, AssociationType):
        fake.call_order.append("disassoc")
        pe = NS(BatchError=[NS(Code=100, Index=0, Message="disassoc error", ErrorCode="DisassocError")])
        return NS(PartialErrors=pe)

    monkeypatch.setattr(fake, "DeleteAdExtensionsAssociations", failing_disassoc)
    d = extensions_write.remove_extension(71, 524066223)
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["aborted"]
    assert fake.call_order == ["disassoc"]
    assert fake.deleted_ext == []
    errors = out["result"]["partial_errors"]
    assert len(errors) == 1
    assert errors[0]["message"] == "disassoc error"
    assert errors[0]["code"] == "DisassocError"


def test_associate_build_raise_after_library_add_carries_extension_ids(fake, monkeypatch):
    """Reviewer finding 2 (representative site): a raise while BUILDING the association
    payload (client.blank for AdExtensionIdToEntityIdAssociation, not the
    SetAdExtensionsAssociations call itself) after the library add already landed must
    still carry the landed extension_ids -- the widened try must cover build code, not
    just the SOAP call."""
    real_blank = client.blank

    def exploding_blank(svc, type_name):
        if type_name == "AdExtensionIdToEntityIdAssociation":
            raise RuntimeError("boom while building association payload")
        return real_blank(svc, type_name)

    monkeypatch.setattr(client, "blank", exploding_blank)
    d = extensions_write.create_callouts(524066223, ["Family Owned"])
    with pytest.raises(rails.PartialWriteError) as exc_info:
        rails.apply_draft(d["draft_id"])
    assert exc_info.value.partial == {"extension_ids": [71], "associated": False}
    assert "reconcile" in str(exc_info.value).lower()


def test_abort_association_when_add_fails(fake, monkeypatch):
    class FailSvc(FakeSvc):
        def AddAdExtensions(self, AccountId, AdExtensions):
            return NS(AdExtensionIdentities=None,
                      NestedPartialErrors=NS(BatchErrorCollection=[
                          NS(Code=1, Index=0, Message="bad", ErrorCode="X", BatchErrors=None)]),
                      PartialErrors=None)

    svc = FailSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    d = extensions_write.create_callouts(1, ["Family Owned"])
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["aborted"]
    assert svc.assoc == []
    assert out["result"]["partial_errors"]
