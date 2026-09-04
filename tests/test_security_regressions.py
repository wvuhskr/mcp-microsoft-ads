"""Offline security reproductions. All secrets and account responses are synthetic."""
import base64
import io
import os
import traceback
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml
from PIL import Image

from mcp_microsoft_ads import auth, client, rails
from mcp_microsoft_ads.tools import adgroups_write, media, status


def png_bytes(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def service(monkeypatch):
    calls = []
    svc = NS(factory=NS(create=lambda t: NS()),
             AddMedia=lambda **kw: calls.append(kw) or NS(long=[1]))
    monkeypatch.setattr(client, "svc", lambda name: svc)
    return svc, calls


@pytest.mark.parametrize("payload", [b"refresh_token: SYNTHETIC_SECRET\n", b"\x89PNG\r\n\x1a\nfake"])
def test_upload_rejects_non_images_even_with_png_name(tmp_path, service, payload):
    p = tmp_path / "image.png"
    p.write_bytes(payload)
    with pytest.raises(ValueError, match="PNG|JPEG|image"):
        media.upload_image_asset(str(p), "Image1x1")
    assert service[1] == []


def test_upload_refuses_changed_file_after_preview(tmp_path, service):
    p = tmp_path / "image.png"
    p.write_bytes(png_bytes())
    d = media.upload_image_asset(str(p), "Image1x1")
    p.write_bytes(png_bytes("blue"))
    with pytest.raises((ValueError, rails.RailViolation), match="changed"):
        rails.apply_draft(d["draft_id"])
    assert service[1] == []


def test_valid_image_upload_sends_previewed_bytes(tmp_path, service):
    p = tmp_path / "image.png"
    content = png_bytes()
    p.write_bytes(content)
    d = media.upload_image_asset(str(p), "Image1x1")
    rails.apply_draft(d["draft_id"])
    assert base64.b64decode(service[1][0]["Media"].Media[0].Data) == content


@pytest.mark.parametrize("loader", [auth.load_creds, auth.load_static_creds])
def test_credential_parse_errors_never_print_secret_lines(tmp_path, loader):
    p = tmp_path / "credentials.yaml"
    p.write_text('refresh_token: [SYNTHETIC_SECRET\n')
    with pytest.raises(auth.CredsError) as caught:
        loader(str(p))
    # Default traceback rendering also must suppress PyYAML's original source excerpt.
    rendered = ''.join(traceback.format_exception(caught.value))
    assert "SYNTHETIC_SECRET" not in rendered


def test_initial_token_parse_error_does_not_print_secret_lines(tmp_path):
    p = tmp_path / "credentials.yaml"
    p.write_text('client_secret: [SYNTHETIC_SECRET\n')
    with pytest.raises(auth.CredsError) as caught:
        auth.write_initial_refresh_token("NEW", str(p))
    assert "SYNTHETIC_SECRET" not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("bootstrap", [False, True])
def test_backup_does_not_follow_existing_link(tmp_path, bootstrap):
    p = tmp_path / "credentials.yaml"
    p.write_text('client_id: example\n' if bootstrap else 'refresh_token: OLD\n')
    victim = tmp_path / "unrelated.txt"
    victim.write_text("keep this file")
    Path(str(p) + ".bak").symlink_to(victim)
    if bootstrap:
        auth.write_initial_refresh_token("NEW", str(p))
    else:
        auth.persist_rotated_token("OLD", "NEW", str(p))
    assert victim.read_text() == "keep this file"
    assert not Path(str(p) + ".bak").is_symlink()
    assert os.stat(str(p) + ".bak").st_mode & 0o777 == 0o600


def test_token_rotation_changes_only_token_field(tmp_path):
    p = tmp_path / "credentials.yaml"
    p.write_text('refresh_token: OLD\nclient_secret: prefix-OLD-suffix\n')
    auth.persist_rotated_token("OLD", "NEW", str(p))
    assert yaml.safe_load(p.read_text()) == {
        "refresh_token": "NEW", "client_secret": "prefix-OLD-suffix"}


def test_token_rotation_refuses_stale_token_found_in_other_field(tmp_path):
    p = tmp_path / "credentials.yaml"
    content = 'refresh_token: CURRENT\nclient_secret: OLD\n'
    p.write_text(content)
    with pytest.raises(auth.CredsError, match="refusing"):
        auth.persist_rotated_token("OLD", "NEW", str(p))
    assert p.read_text() == content


@pytest.mark.parametrize("bad", ["Deleted", "Bogus", ""])
def test_adgroup_update_cannot_smuggle_deletion_through_status(monkeypatch, bad):
    reads = []
    monkeypatch.setattr(adgroups_write, "_fetch_ad_group", lambda *a: reads.append(a) or NS(
        Name="Example", Status="Active", BiddingScheme=NS(Type="ManualCpc")))
    with pytest.raises(ValueError, match="Active|Paused"):
        adgroups_write.update_ad_group(1, 2, status=bad)
    assert reads == []


@pytest.mark.parametrize("entity", ["keyword", "ad"])
def test_status_single_entity_response_does_not_hide_success(monkeypatch, entity):
    calls = []
    row = NS(Id=9, Status="Paused")
    svc = NS(factory=NS(create=lambda t: NS()),
             UpdateKeywords=lambda **kw: calls.append(kw) or NS(),
             UpdateAds=lambda **kw: calls.append(kw) or NS(),
             GetKeywordsByAdGroupId=lambda **kw: NS(Keyword=row),
             GetAdsByAdGroupId=lambda **kw: NS(Ad=row))
    monkeypatch.setattr(client, "svc", lambda name: svc)
    d = status.pause_entity(entity, 9, 2)
    out = rails.apply_draft(d["draft_id"])
    assert len(calls) == 1
    assert out["result"]["verify"]["verified"] is True


@pytest.mark.parametrize("partial", [False, True])
def test_mcp_dispatch_preserves_safety_messages(partial):
    import asyncio

    import mcp_microsoft_ads.server  # noqa: F401
    from mcp_microsoft_ads.app import mcp

    if partial:
        def apply():
            raise rails.PartialWriteError("second step failed", {"campaign_ids": [888]})
        d = rails.create_draft("create_pmax_campaign", {"name": "Example"}, apply)
        draft_id = d["draft_id"]
        expected = "888"
    else:
        draft_id = "no-such-draft"
        expected = "unknown or already-applied"
    with pytest.raises(Exception) as caught:
        asyncio.run(mcp.call_tool("confirm_and_apply", {"draft_id": draft_id}))
    assert expected in str(caught.value)
    if partial:
        assert "reconcile" in str(caught.value)


def test_upload_disabled_does_not_inspect_local_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "false")
    with pytest.raises(rails.RailViolation, match="MS_ADS_ENABLE_WRITES"):
        media.upload_image_asset(str(tmp_path / "missing.png"), "Image1x1")


def test_upload_rejects_oversized_file_before_reading(tmp_path, service):
    p = tmp_path / "large.png"
    with p.open("wb") as f:
        f.truncate(5 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="limit"):
        media.upload_image_asset(str(p), "Image1x1")
    assert service[1] == []


def test_image_bytes_at_apply_are_same_as_checked_bytes(tmp_path, service, monkeypatch):
    p = tmp_path / "image.png"
    original = png_bytes()
    p.write_bytes(original)
    d = media.upload_image_asset(str(p), "Image1x1")
    real_svc = client.svc
    def swap_after_check(name):
        p.write_bytes(b"SYNTHETIC_SECRET")
        return real_svc(name)
    monkeypatch.setattr(client, "svc", swap_after_check)
    rails.apply_draft(d["draft_id"])
    assert base64.b64decode(service[1][0]["Media"].Media[0].Data) == original


def test_token_rotation_preserves_yaml_alias_target(tmp_path):
    p = tmp_path / "credentials.yaml"
    p.write_text('client_secret: &shared OLD\nrefresh_token: *shared\n')
    auth.persist_rotated_token("OLD", "NEW", str(p))
    assert yaml.safe_load(p.read_text()) == {"client_secret": "OLD", "refresh_token": "NEW"}


def test_token_rotation_preserves_comments_and_quotes_special_token(tmp_path):
    p = tmp_path / "credentials.yaml"
    p.write_text('# keep comment\nrefresh_token: OLD # token comment\nclient_id: example\n')
    new = 'NEW: value # quoted'
    auth.persist_rotated_token("OLD", new, str(p))
    assert yaml.safe_load(p.read_text())["refresh_token"] == new
    assert '# keep comment' in p.read_text() and '# token comment' in p.read_text()


def test_atomic_backup_is_private_before_replacement(tmp_path, monkeypatch):
    p = tmp_path / "credentials.yaml"
    p.write_text('refresh_token: OLD\n')
    p.chmod(0o644)
    observed = []
    real_replace = auth.os.replace
    def inspect(src, dst):
        observed.append((dst, os.stat(src).st_mode & 0o777))
        return real_replace(src, dst)
    monkeypatch.setattr(auth.os, "replace", inspect)
    auth.persist_rotated_token("OLD", "NEW", str(p))
    assert observed == [(str(p) + '.bak', 0o600), (str(p), 0o600)]


def test_failed_draft_audit_does_not_leave_applicable_draft(monkeypatch):
    before = set(rails._DRAFTS)
    def fail_audit(*args, **kwargs):
        raise OSError("synthetic disk full")
    monkeypatch.setattr(rails.audit, "log_event", fail_audit)
    with pytest.raises(OSError):
        rails.create_draft("example", {"name": "Example"}, lambda: {})
    assert set(rails._DRAFTS) == before


@pytest.mark.parametrize("content", [
    'refresh_token: |\n  OLD\nclient_id: example\n',
    'refresh_token: &shared OLD\nclient_secret: *shared\n',
])
def test_rotation_handles_block_scalars_and_shared_anchors(tmp_path, content):
    p = tmp_path / "credentials.yaml"
    p.write_text(content)
    expected = yaml.safe_load(content)
    old = expected['refresh_token']
    expected['refresh_token'] = 'NEW'
    auth.persist_rotated_token(old, 'NEW', str(p))
    assert yaml.safe_load(p.read_text()) == expected


@pytest.mark.parametrize("content", ['[]', 'false', 'plain-text'])
def test_invalid_credential_document_refused_without_overwriting(tmp_path, content):
    p = tmp_path / 'credentials.yaml'
    p.write_text(content)
    with pytest.raises(auth.CredsError, match='mapping'):
        auth.write_initial_refresh_token('NEW', str(p))
    assert p.read_text() == content


def test_failed_atomic_backup_preserves_credentials_and_cleans_temp(tmp_path, monkeypatch):
    p = tmp_path / 'credentials.yaml'
    original = 'refresh_token: OLD\n'
    p.write_text(original)
    def fail_replace(*args):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(auth.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        auth.persist_rotated_token('OLD', 'NEW', str(p))
    assert p.read_text() == original
    assert list(tmp_path.iterdir()) == [p]
