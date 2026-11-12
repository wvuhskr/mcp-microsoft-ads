from .. import client
from ..app import mcp
from ..reporting_core import (
    _PREDEFINED,
    REPORT_PRESETS,
    report_catalog,
    run_report_request,
)

_CONVERSIONS_NOTE = ("Conversions/Revenue are Microsoft-counted, for the conversion goals the "
                     "account includes in the Conversions column; the attribution window is "
                     "set per goal in Microsoft Ads. Add the Goal column to split by goal.")


def _wrap(rows: list[dict], report_type: str, days: int, columns: list[str] | None = None,
          aggregation: str = "Daily") -> dict:
    prov = {"source": "MS Ads Reporting API v13", "report_type": report_type,
            "account_id": client.account_id(), "date_range_days": days,
            "period": _PREDEFINED.get(days), "aggregation": aggregation,
            "conversions_note": _CONVERSIONS_NOTE}
    if columns is not None:
        prov["columns"] = columns
    return {"rows": rows, "row_count": len(rows), "provenance": prov}


def _preset(key: str, days: int) -> dict:
    rtype, cols = REPORT_PRESETS[key]
    return _wrap(run_report_request(rtype, cols, days), rtype, days, cols)


@mcp.tool()
def get_campaign_performance(days: int = 30) -> dict:
    """How each campaign did, day by day: impressions, clicks, spend, conversions,
    cost per conversion, click-through rate, average cost per click.

    days must be 7, 14, or 30 (one row per day). For any other date range, other
    columns, or totals instead of daily rows, use run_report."""
    return _preset("campaign", days)


@mcp.tool()
def get_ad_performance(days: int = 30) -> dict:
    """How each ad did, day by day: impressions, clicks, spend, conversions,
    click-through rate, with its campaign and ad group.

    days must be 7, 14, or 30 (one row per day). For any other date range, other
    columns, or totals instead of daily rows, use run_report."""
    return _preset("ad", days)


@mcp.tool()
def get_keyword_performance(days: int = 30) -> dict:
    """How each keyword did, day by day: match type, impressions, clicks, spend,
    conversions, cost per conversion, Quality Score.

    days must be 7, 14, or 30 (one row per day). For any other date range, other
    columns, or totals instead of daily rows, use run_report."""
    return _preset("keyword", days)


@mcp.tool()
def get_geo_performance(days: int = 30) -> dict:
    """Where the clicks came from, day by day: country, state, metro area, city, with
    impressions, clicks, spend, conversions.

    days must be 7, 14, or 30 (one row per day). For any other date range, other
    columns, or totals instead of daily rows, use run_report."""
    return _preset("geo", days)


@mcp.tool()
def get_search_terms(days: int = 30) -> dict:
    """What people actually typed before clicking (search terms report), day by day,
    with the keyword that matched and impressions, clicks, spend, conversions. The usual
    starting point for finding wasted spend and negative-keyword candidates.

    days must be 7, 14, or 30 (one row per day). For any other date range, other
    columns, or totals instead of daily rows, use run_report."""
    return _preset("search_terms", days)


@mcp.tool()
def describe_report(report_type: str | None = None) -> dict:
    """Look up what run_report accepts — call this before run_report instead of guessing
    names. No argument: every report type. With a report_type (e.g.
    'CampaignPerformanceReportRequest'): every valid column name for it. Read offline
    from Microsoft's own API definition shipped with the SDK; makes no API call."""
    catalog = report_catalog()
    if report_type is None:
        return {"report_types": list(catalog), "aggregations": _AGGREGATIONS}
    if report_type not in catalog:
        raise ValueError(f"unknown report_type '{report_type}' — call describe_report() "
                         "with no argument for the list")
    return {"report_type": report_type, "columns": catalog[report_type],
            "aggregations": _AGGREGATIONS}


_AGGREGATIONS = ["Summary", "Daily", "Weekly", "Monthly", "Yearly", "Hourly", "HourOfDay",
                 "DayOfWeek", "WeeklyStartingMonday"]


@mcp.tool()
def run_report(report_type: str, columns: list[str], days: int = 30,
               aggregation: str = "Daily",
               start_date: str | None = None, end_date: str | None = None) -> dict:
    """Build-your-own report: any Microsoft Ads report type, any columns, any date range.
    Use describe_report to look up valid report types and column names.

    Common recipes (report_type: useful columns beyond Impressions/Clicks/Spend):
    - Totals for a period: any type with aggregation="Summary" and NO TimePeriod column.
    - Conversions split by goal: CampaignPerformanceReportRequest with Goal, GoalType,
      Conversions, Revenue (also valid on ad group / keyword / search-term reports).
    - Revenue and return: CampaignPerformanceReportRequest with Revenue, ReturnOnAdSpend.
    - Impression share and why it was lost: CampaignPerformanceReportRequest with
      ImpressionSharePercent, ImpressionLostToBudgetPercent, ImpressionLostToRankAggPercent.
    - Device split: add DeviceType to a campaign / ad group / keyword report.
    - Whole account: AccountPerformanceReportRequest with AccountName, CurrencyCode.
    - Performance Max asset groups: AssetGroupPerformanceReportRequest.
    - Age and gender: AgeGenderAudienceReportRequest with AgeGroup, Gender.

    aggregation: "Daily" (default) works with any column set; Weekly / Monthly /
    DayOfWeek / HourOfDay also need the TimePeriod column. "Summary" is only valid when
    columns exclude TimePeriod — the live API raises
    InvalidTimePeriodColumnForSummaryReport otherwise.

    Dates: days must be 7, 14, or 30. Or pass start_date/end_date (YYYY-MM-DD, both or
    neither) for an explicit range of any length. Live-verified 2026-08-14 (106-day
    AccountPerformance custom-range pull)."""
    rows = run_report_request(report_type, columns, days, aggregation, start_date, end_date)
    out = _wrap(rows, report_type, days, columns, aggregation)
    if start_date is not None:
        prov = out["provenance"]
        del prov["date_range_days"], prov["period"]
        prov["start_date"], prov["end_date"] = start_date, end_date
    return out
