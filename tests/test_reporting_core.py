import pytest

from mcp_microsoft_ads.reporting_core import (
    REPORT_PRESETS,
    parse_report_csv,
    run_report_request,
)

CSV = """CampaignName,CampaignId,Impressions,Clicks,Spend
HVAC - Search - MS,524066223,1000,50,321.50
Branded - Search - MS,524066227,400,90,88.00
"""


def test_parse_rows():
    rows = parse_report_csv(CSV)
    assert rows[0]["CampaignName"] == "HVAC - Search - MS"
    assert rows[0]["Spend"] == "321.50"
    assert len(rows) == 2


def test_parse_strips_quoted_footer():
    rows = parse_report_csv(CSV + '"©2026 Microsoft. All rights reserved."\n')
    assert len(rows) == 2  # footer row without full columns dropped


def test_presets_cover_five_tools():
    assert set(REPORT_PRESETS) == {"campaign", "ad", "keyword", "geo", "search_terms"}
    for name, (rtype, cols) in REPORT_PRESETS.items():
        assert rtype.endswith("PerformanceReportRequest") or rtype == "SearchQueryPerformanceReportRequest"
        assert "Impressions" in cols and "Clicks" in cols and "Spend" in cols


def test_run_report_request_rejects_unsupported_days():
    # must raise before touching client.svc/account_id/creds — no live auth call, no patching
    rtype, cols = REPORT_PRESETS["campaign"]
    with pytest.raises(ValueError, match="7, 14, 30"):
        run_report_request(rtype, cols, days=90)


def test_run_report_request_rejects_half_custom_range():
    # start/end must travel together — raise before any live auth call
    rtype, cols = REPORT_PRESETS["campaign"]
    with pytest.raises(ValueError, match="together"):
        run_report_request(rtype, cols, start_date="2026-05-01")
    with pytest.raises(ValueError, match="together"):
        run_report_request(rtype, cols, end_date="2026-08-14")


def test_custom_range_skips_days_validation(monkeypatch):
    # a custom range must not trip the days allowlist; stub client.svc so the
    # test proves the gate is bypassed without ever touching live auth
    from mcp_microsoft_ads import reporting_core

    class _Sentinel(Exception):
        pass

    monkeypatch.setattr(reporting_core.client, "svc",
                        lambda name: (_ for _ in ()).throw(_Sentinel()))
    rtype, cols = REPORT_PRESETS["campaign"]
    with pytest.raises(_Sentinel):
        run_report_request(rtype, cols, days=90,
                           start_date="2026-05-01", end_date="2026-08-14")
