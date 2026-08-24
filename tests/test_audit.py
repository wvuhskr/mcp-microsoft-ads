import json
import os
import stat
import subprocess
import sys

from mcp_microsoft_ads import audit


def test_log_event_appends_jsonl(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    audit.log_event("update_campaign", "draft", {"campaign_id": 1}, path=p)
    audit.log_event("update_campaign", "apply", {"campaign_id": 1}, path=p)
    lines = [json.loads(line) for line in open(p)]
    assert len(lines) == 2
    assert lines[0]["tool"] == "update_campaign"
    assert lines[0]["phase"] == "draft"
    assert lines[1]["phase"] == "apply"
    assert "ts" in lines[0]

def test_log_event_serializes_non_json(tmp_path):
    p = str(tmp_path / "audit.jsonl")
    audit.log_event("t", "apply", {"obj": object()}, path=p)  # must not raise
    assert json.loads(open(p).read())["obj"]

def test_audit_path_default_no_env(monkeypatch):
    # audit.AUDIT_PATH itself is sandboxed to a tmp path by conftest's autouse
    # _sandbox_audit_path fixture (so this suite never touches a real home dir) --
    # the contract under test is "falls back to module-level AUDIT_PATH", not the
    # literal ~/.mcp-microsoft-ads value, so compare against the (patched) constant.
    monkeypatch.delenv("MS_ADS_AUDIT_PATH", raising=False)
    assert audit.audit_path() == audit.AUDIT_PATH

def test_audit_default_path_literal():
    """The documented default -- used when MS_ADS_AUDIT_PATH is unset -- must stay
    ~/.mcp-microsoft-ads/audit.jsonl. This suite's autouse _sandbox_audit_path
    fixture monkeypatches audit.AUDIT_PATH for every in-process test, so the only
    honest way to see the real default is a fresh, unpatched import in a subprocess."""
    out = subprocess.run(
        [sys.executable, "-c", "from mcp_microsoft_ads import audit; print(audit.AUDIT_PATH)"],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == os.path.expanduser("~/.mcp-microsoft-ads/audit.jsonl")

def test_audit_path_env_wins(monkeypatch, tmp_path):
    p = tmp_path / "somewhere.jsonl"
    monkeypatch.setenv("MS_ADS_AUDIT_PATH", str(p))
    assert audit.audit_path() == str(p)

def test_env_var_selects_audit_path_for_log_event(monkeypatch, tmp_path):
    p = tmp_path / "sub" / "audit.jsonl"
    monkeypatch.setenv("MS_ADS_AUDIT_PATH", str(p))
    audit.log_event("t", "draft", {"x": 1})  # no explicit path -> env var
    assert p.exists()

def test_explicit_path_beats_env_for_log_event(monkeypatch, tmp_path):
    wrong = tmp_path / "wrong.jsonl"
    real = tmp_path / "real.jsonl"
    monkeypatch.setenv("MS_ADS_AUDIT_PATH", str(wrong))
    audit.log_event("t", "draft", {"x": 1}, path=str(real))
    assert real.exists()
    assert not wrong.exists()

def test_fresh_install_creates_dir_0700_and_file_0600(monkeypatch, tmp_path):
    home = tmp_path / "home"
    audit_file = home / ".mcp-microsoft-ads" / "audit.jsonl"
    monkeypatch.setenv("MS_ADS_AUDIT_PATH", str(audit_file))
    assert not audit_file.parent.exists()

    audit.log_event("update_campaign", "draft", {"campaign_id": 1})

    assert stat.S_IMODE(os.stat(audit_file.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(audit_file).st_mode) == 0o600
    entry = json.loads(open(audit_file).read().strip())
    assert entry["tool"] == "update_campaign"

def test_existing_audit_file_mode_untouched(tmp_path):
    """Creation mode only applies on create -- an existing file's mode must not
    be tightened (or loosened) by a later append."""
    p = tmp_path / "audit.jsonl"
    p.write_text("")
    os.chmod(p, 0o644)
    audit.log_event("t", "draft", {"x": 1}, path=str(p))
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o644
