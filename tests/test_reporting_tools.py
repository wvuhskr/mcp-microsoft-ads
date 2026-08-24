from mcp_microsoft_ads.tools import reporting


def test_preset_tools_delegate(monkeypatch):
    calls = []
    monkeypatch.setattr(reporting, "run_report_request",
                        lambda rtype, cols, days, aggregation="Summary": calls.append((rtype, days)) or
                        [{"CampaignName": "HVAC - Search - MS", "Spend": "321.50"}])
    out = reporting.get_campaign_performance(days=7)
    assert out["row_count"] == 1
    assert out["rows"][0]["Spend"] == "321.50"
    assert calls[0] == ("CampaignPerformanceReportRequest", 7)
    assert out["provenance"]["account_id"] == 111222333


def test_run_report_passthrough(monkeypatch):
    monkeypatch.setattr(reporting, "run_report_request",
                        lambda rtype, cols, days, aggregation, start_date=None, end_date=None: [{"X": "1"}])
    out = reporting.run_report("AgeGenderAudienceReportRequest", ["X"], days=30, aggregation="Daily")
    assert out["rows"] == [{"X": "1"}]
    assert out["provenance"]["date_range_days"] == 30


def test_run_report_custom_range_provenance(monkeypatch):
    seen = {}
    monkeypatch.setattr(reporting, "run_report_request",
                        lambda rtype, cols, days, aggregation, start_date=None, end_date=None:
                        seen.update(start=start_date, end=end_date) or [{"X": "1"}])
    out = reporting.run_report("AccountPerformanceReportRequest", ["X"],
                               start_date="2026-05-01", end_date="2026-08-14")
    assert seen == {"start": "2026-05-01", "end": "2026-08-14"}
    prov = out["provenance"]
    assert prov["start_date"] == "2026-05-01" and prov["end_date"] == "2026-08-14"
    assert "date_range_days" not in prov  # days=30 default is meaningless here


def test_empty_report_reported_not_fabricated(monkeypatch):
    monkeypatch.setattr(reporting, "run_report_request", lambda *a, **k: [])
    out = reporting.get_search_terms(days=7)
    assert out["row_count"] == 0 and out["rows"] == []
