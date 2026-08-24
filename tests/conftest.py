# WSDL-shaped fakes are the root cause of every "unwrapped response" shape bug in this
# project (see client.partial_errors docstring). The rule: suds strips one wrapper
# level when a `*Response` element declares exactly ONE child (suds
# bindings/binding.py::get_reply returns the single child unmarshalled directly; with
# 2+ declared children the named wrapper is kept). When writing a fake for a response
# whose WSDL element has only one child, shape it STRIPPED — `NS(BatchError=[...])`,
# not `NS(PartialErrors=NS(BatchError=[...]))` — or the fake will pass while the real
# response would break the code under test.
import pytest

from mcp_microsoft_ads import audit, client, settings


@pytest.fixture(autouse=True)
def fixed_account(monkeypatch):
    monkeypatch.setattr(client, "account_id", lambda: 111222333, raising=False)
    monkeypatch.setattr(client, "customer_id", lambda: 444555666, raising=False)


@pytest.fixture(autouse=True)
def _no_ms_ads_path_env(monkeypatch):
    """A developer's real MS_ADS_CREDENTIALS_PATH / MS_ADS_AUDIT_PATH exports (the
    machine running the suite may have both, pointed at a real config directory)
    must not leak into the suite -- otherwise tests could silently read/write the
    real creds or audit log."""
    monkeypatch.delenv("MS_ADS_CREDENTIALS_PATH", raising=False)
    monkeypatch.delenv("MS_ADS_AUDIT_PATH", raising=False)


@pytest.fixture(autouse=True)
def _sandbox_audit_path(monkeypatch, tmp_path):
    """Belt-and-suspenders on top of the env delenv above: monkeypatch audit.AUDIT_PATH
    itself to a per-test tmp path, so no test can ever create a real
    ~/.mcp-microsoft-ads/audit.jsonl in a developer's home dir even if a test forgets to
    override the path. Tests that need a specific audit path still call
    monkeypatch.setattr(audit, "AUDIT_PATH", ...) themselves -- that runs in the test
    body, after this fixture's setup, so it simply overrides this default."""
    monkeypatch.setattr(audit, "AUDIT_PATH", str(tmp_path / "audit.jsonl"))


@pytest.fixture(autouse=True)
def _ms_ads_writes_enabled_by_default(monkeypatch):
    """MS_ADS_ENABLE_WRITES (Task C1) defaults writes OFF; the pre-existing suite
    exercises create_draft/apply_draft assuming writes work, so default it on here.
    Tests for the gate itself explicitly monkeypatch it off/invalid, overriding this."""
    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "true")


@pytest.fixture(autouse=True)
def clean_advertiser_settings(monkeypatch, tmp_path):
    """Point every test at a settings file that doesn't exist (-> library defaults),
    regardless of whatever real ~/.mcp-microsoft-ads/advertiser.yaml a developer's
    machine has. Without this, a populated personal advertiser.yaml would silently
    change test behavior (e.g. blocked_terms, advertiser_domain) machine to machine.
    Tests that need non-default settings set their own env var/path and call
    settings._reset() explicitly, which overrides this for their own duration."""
    monkeypatch.setenv(settings.ENV_VAR, str(tmp_path / "no-such-advertiser.yaml"))
    settings._reset()
    yield
    settings._reset()
