"""Guided setup, CLI dispatch, the offline report catalog, and report provenance."""
import os
import stat
import sys

import pytest
import yaml

from mcp_microsoft_ads import reauth, server, setup_wizard
from mcp_microsoft_ads.tools import reporting

ANSWERS = {"Developer token": "DEVTOKEN", "Application (client) ID": "client-id-0000",
           "Client secret": "s3cret", "Customer ID": "1000000001", "Account ID": "1000000002"}


@pytest.fixture
def wizard(monkeypatch, tmp_path):
    path = tmp_path / "cfg" / "credentials.yaml"
    monkeypatch.setenv("MS_ADS_CREDENTIALS_PATH", str(path))
    for field in ("DEVELOPER_TOKEN", "CLIENT_ID", "CLIENT_SECRET", "CUSTOMER_ID", "ACCOUNT_ID", "TENANT"):
        monkeypatch.delenv(f"MS_ADS_{field}", raising=False)

    def answer(prompt):
        return next((v for k, v in ANSWERS.items() if prompt.startswith(k)), "")  # tenant -> ""
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(setup_wizard.getpass, "getpass", answer)
    monkeypatch.setattr(reauth, "main", lambda: 0)
    return path


def test_setup_writes_private_creds_file_and_prints_client_config(wizard, capsys):
    assert setup_wizard.main() == 0
    saved = yaml.safe_load(wizard.read_text())
    assert saved == {"developer_token": "DEVTOKEN", "client_id": "client-id-0000",
                     "client_secret": "s3cret", "customer_id": "1000000001",
                     "account_id": "1000000002", "tenant": "common"}
    assert stat.S_IMODE(os.stat(wizard).st_mode) == 0o600
    out = capsys.readouterr().out
    assert '"uvx"' in out and "s3cret" not in out and "DEVTOKEN" not in out


def test_setup_never_overwrites_an_existing_incomplete_file(wizard):
    wizard.parent.mkdir()
    wizard.write_text("developer_token: KEEPME\n")
    assert setup_wizard.main() == 2
    assert wizard.read_text() == "developer_token: KEEPME\n"


def test_setup_rejects_non_numeric_account_id(wizard, monkeypatch):
    monkeypatch.setitem(ANSWERS, "Account ID", "not-a-number")
    assert setup_wizard.main() == 2
    assert not wizard.exists()


def test_cli_dispatches_setup_and_reauth(monkeypatch):
    monkeypatch.setattr(setup_wizard, "main", lambda: 7)
    monkeypatch.setattr(sys, "argv", ["mcp-microsoft-ads", "setup"])
    with pytest.raises(SystemExit) as e:
        server.main()
    assert e.value.code == 7

    seen = {}
    monkeypatch.setattr(reauth, "main", lambda: seen.update(argv=list(sys.argv)) or 0)
    monkeypatch.setattr(sys, "argv", ["mcp-microsoft-ads", "reauth", "--url-file", "x.txt"])
    with pytest.raises(SystemExit) as e:
        server.main()
    assert e.value.code == 0 and seen["argv"] == ["mcp-microsoft-ads", "--url-file", "x.txt"]


def test_describe_report_lists_types_and_columns():
    types = reporting.describe_report()["report_types"]
    assert "CampaignPerformanceReportRequest" in types and "GoalsAndFunnelsReportRequest" in types
    assert "SubmitGenerateReportRequest" not in types
    cols = reporting.describe_report("CampaignPerformanceReportRequest")["columns"]
    # every column the run_report docstring recipes name must really exist
    for col in ("Goal", "GoalType", "Revenue", "ReturnOnAdSpend", "ImpressionSharePercent",
                "ImpressionLostToBudgetPercent", "ImpressionLostToRankAggPercent", "DeviceType"):
        assert col in cols
    with pytest.raises(ValueError, match="unknown report_type"):
        reporting.describe_report("NopeReportRequest")


def test_every_preset_column_is_valid_for_its_report_type():
    catalog = reporting.report_catalog()
    for rtype, cols in reporting.REPORT_PRESETS.values():
        assert not set(cols) - set(catalog[rtype]), rtype


def test_provenance_names_period_columns_and_conversion_definition(monkeypatch):
    monkeypatch.setattr(reporting, "run_report_request", lambda *a, **k: [])
    prov = reporting.get_campaign_performance(days=30)["provenance"]
    assert prov["period"] == "Last30Days" and prov["aggregation"] == "Daily"
    assert "Spend" in prov["columns"] and "attribution window" in prov["conversions_note"]
    prov = reporting.run_report("AccountPerformanceReportRequest", ["Spend"], aggregation="Summary",
                                start_date="2026-05-01", end_date="2026-05-31")["provenance"]
    assert "period" not in prov and prov["aggregation"] == "Summary"
