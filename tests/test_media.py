import base64
from types import SimpleNamespace as NS

import pytest

from mcp_microsoft_ads import client, rails
from mcp_microsoft_ads.tools import media


class FakeSvc:
    def __init__(self):
        self.added = []
        self.factory = NS(create=lambda t: NS())

    def AddMedia(self, AccountId, Media):
        self.added.append(Media)
        # Stripped shape (suds ONE-CHILD RULE — AddMediaResponse declares only MediaIds,
        # so the wrapper never survives live; see media.py's _media_ids docstring).
        return NS(long=[9001], PartialErrors=None)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    svc = FakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    return svc


def test_upload_image_asset(fake, tmp_path):
    img_path = tmp_path / "logo.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepngbytes")
    d = media.upload_image_asset(str(img_path), media_type="Image191x100")
    assert d["preview"]["size_kb"] >= 0
    assert d["preview"]["media_type"] == "Image191x100"
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["media_ids"] == [9001]
    assert out["result"]["partial_errors"] == []
    m = fake.added[0].Media[0]
    assert m.Type == "Image"
    assert m.MediaType == "Image191x100"
    assert base64.b64decode(m.Data) == b"\x89PNG\r\n\x1a\nfakepngbytes"


def test_missing_file_raises(fake, tmp_path):
    with pytest.raises(ValueError, match="not found"):
        media.upload_image_asset(str(tmp_path / "nope.png"), media_type="Image1x1")


def test_bad_media_type_raises_before_service_call(fake, tmp_path):
    """Defect 1: MediaType is an aspect-ratio label, not the literal 'Image' — an
    unrecognized label must raise before AddMedia is ever called."""
    img_path = tmp_path / "logo.png"
    img_path.write_bytes(b"data")
    with pytest.raises(ValueError, match="media_type"):
        media.upload_image_asset(str(img_path), media_type="Bogus")
    assert fake.added == []


def test_upload_image_asset_unwrapped_response_shape(monkeypatch, tmp_path):
    """Defect 1 (live-verified, Task 25 smoke): AddMediaResponse unwraps directly to
    the ArrayOflong — resp.long carries the id(s) and MediaIds is ABSENT. Real probe
    (single image upload): a bare int, not a list — so this also covers the
    single-item collapse the default FakeSvc's list shape can't reach. Both must
    yield the id; returning [] would drop an image that HAS been uploaded."""
    class UnwrappedFakeSvc(FakeSvc):
        def AddMedia(self, AccountId, Media):
            self.added.append(Media)
            return NS(long=1326011251266108, PartialErrors=None)

    svc = UnwrappedFakeSvc()
    monkeypatch.setattr(client, "svc", lambda n: svc)
    monkeypatch.setattr("mcp_microsoft_ads.audit.AUDIT_PATH", str(tmp_path / "a.jsonl"))
    img_path = tmp_path / "logo.png"
    img_path.write_bytes(b"data")
    d = media.upload_image_asset(str(img_path), media_type="Image191x100")
    out = rails.apply_draft(d["draft_id"])
    assert out["result"]["media_ids"] == [1326011251266108]
