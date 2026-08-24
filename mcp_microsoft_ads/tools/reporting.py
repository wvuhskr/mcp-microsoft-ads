from .. import client
from ..app import mcp
from ..reporting_core import REPORT_PRESETS, run_report_request


def _wrap(rows: list[dict], report_type: str, days: int) -> dict:
    return {"rows": rows, "row_count": len(rows),
            "provenance": {"source": "MS Ads Reporting API v13", "report_type": report_type,
                           "account_id": client.account_id(), "date_range_days": days}}


def _preset(key: str, days: int) -> dict:
    rtype, cols = REPORT_PRESETS[key]
    return _wrap(run_report_request(rtype, cols, days), rtype, days)


@mcp.tool()
def get_campaign_performance(days: int = 30) -> dict:
    """Campaign performance (impressions/clicks/spend/conversions/CPA)."""
    return _preset("campaign", days)


@mcp.tool()
def get_ad_performance(days: int = 30) -> dict:
    """Per-ad performance."""
    return _preset("ad", days)


@mcp.tool()
def get_keyword_performance(days: int = 30) -> dict:
    """Per-keyword performance incl. QualityScore."""
    return _preset("keyword", days)


@mcp.tool()
def get_geo_performance(days: int = 30) -> dict:
    """Geographic performance."""
    return _preset("geo", days)


@mcp.tool()
def get_search_terms(days: int = 30) -> dict:
    """Search query report."""
    return _preset("search_terms", days)


@mcp.tool()
def run_report(report_type: str, columns: list[str], days: int = 30,
               aggregation: str = "Daily",
               start_date: str | None = None, end_date: str | None = None) -> dict:
    """Arbitrary report (replaces Google run_gaql for metrics pulls). report_type =
    exact v13 request type name e.g. 'AgeGenderAudienceReportRequest'.

    aggregation: "Daily" (default) works with any column set. "Summary" is only valid
    when columns exclude TimePeriod — the live API raises
    InvalidTimePeriodColumnForSummaryReport otherwise. days must be 7, 14, or 30.

    start_date/end_date (YYYY-MM-DD, both or neither) use an explicit custom date
    range instead of days — no length restriction. Live-verified 2026-08-14
    (106-day AccountPerformance custom-range pull)."""
    rows = run_report_request(report_type, columns, days, aggregation, start_date, end_date)
    out = _wrap(rows, report_type, days)
    if start_date is not None:
        prov = out["provenance"]
        del prov["date_range_days"]
        prov["start_date"], prov["end_date"] = start_date, end_date
    return out
