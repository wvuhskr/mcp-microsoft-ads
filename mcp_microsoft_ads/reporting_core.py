"""Reporting service async flow. SDK's ReportingServiceManager handles
submit -> poll -> download; we build requests + parse CSV."""
import csv
import io
import tempfile

from bingads.v13.reporting import ReportingDownloadParameters, ReportingServiceManager

from . import client

# tool key -> (request type name, default columns)
REPORT_PRESETS = {
    "campaign": ("CampaignPerformanceReportRequest",
                 ["TimePeriod", "CampaignName", "CampaignId", "CampaignStatus", "Impressions",
                  "Clicks", "Spend", "Conversions", "CostPerConversion", "Ctr", "AverageCpc"]),
    "ad": ("AdPerformanceReportRequest",
           ["TimePeriod", "CampaignName", "AdGroupName", "AdId", "AdTitle", "Impressions",
            "Clicks", "Spend", "Conversions", "Ctr"]),
    "keyword": ("KeywordPerformanceReportRequest",
                ["TimePeriod", "CampaignName", "AdGroupName", "Keyword", "KeywordId",
                 "BidMatchType", "Impressions", "Clicks", "Spend", "Conversions",
                 "CostPerConversion", "QualityScore"]),
    "geo": ("GeographicPerformanceReportRequest",
            ["TimePeriod", "CampaignName", "Country", "State", "MetroArea", "City",
             "Impressions", "Clicks", "Spend", "Conversions"]),
    "search_terms": ("SearchQueryPerformanceReportRequest",
                     ["TimePeriod", "CampaignName", "AdGroupName", "SearchQuery", "Keyword",
                      "Impressions", "Clicks", "Spend", "Conversions", "Ctr"]),
}

_PREDEFINED = {7: "LastSevenDays", 14: "LastFourteenDays", 30: "LastThirtyDays"}


def parse_report_csv(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    ncols = len(rows[0]) if rows else 0
    return [r for r in rows if None not in r and sum(v is not None and v != "" for v in r.values()) > 1
            and len(r) == ncols]


def _column_array(svc, report_type: str, columns: list[str]):
    # CampaignPerformanceReportRequest -> ArrayOfCampaignPerformanceReportColumn
    base = report_type.replace("Request", "Column")
    arr = svc.factory.create(f"ArrayOf{base}")
    setattr(arr, base, columns)
    return arr


def _custom_date(svc, iso: str):
    """YYYY-MM-DD -> reporting Date suds object. Live-verified 2026-08-14
    (106-day AccountPerformance pull via CustomDateRangeStart/End)."""
    y, m, d = (int(p) for p in iso.split("-"))
    obj = svc.factory.create("Date")
    obj.Year, obj.Month, obj.Day = y, m, d
    return obj


def run_report_request(report_type: str, columns: list[str], days: int = 30,
                        aggregation: str = "Daily",
                        start_date: str | None = None,
                        end_date: str | None = None) -> list[dict]:
    # live-probed: default deviates from brief's "Summary" — every preset includes a
    # TimePeriod column, which the API rejects for Summary aggregation
    # (InvalidTimePeriodColumnForSummaryReport). "Daily" is the smallest aggregation that
    # accepts TimePeriod, so it's the safe default; callers can still override.
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must be given together")
    if start_date is None and days not in _PREDEFINED:
        raise ValueError(f"days must be one of {sorted(_PREDEFINED)}, got {days}")
    svc = client.svc("ReportingService")
    req = svc.factory.create(report_type)
    req.Format = "Csv"
    req.FormatVersion = "2.0"
    req.ExcludeReportHeader = True
    req.ExcludeReportFooter = True
    req.ExcludeColumnHeaders = False
    req.ReturnOnlyCompleteData = False
    req.Aggregation = aggregation
    req.Columns = _column_array(svc, report_type, columns)
    scope = svc.factory.create("AccountThroughCampaignReportScope")
    # live-probed: AccountIds needs a real ArrayOflong suds object (dict assignment faults
    # with "Invalid client data"); ReportingService resolves the Arrays schema as ns1 (differs
    # from CampaignManagementService's ns3 used elsewhere in this codebase).
    account_ids = svc.factory.create("ns1:ArrayOflong")
    account_ids.long = [client.account_id()]
    scope.AccountIds = account_ids
    scope.Campaigns = None
    req.Scope = scope
    time_obj = svc.factory.create("ReportTime")
    if start_date is not None:
        time_obj.PredefinedTime = None
        time_obj.CustomDateRangeStart = _custom_date(svc, start_date)
        time_obj.CustomDateRangeEnd = _custom_date(svc, end_date)
    else:
        time_obj.PredefinedTime = _PREDEFINED[days]
        time_obj.CustomDateRangeStart = None
        time_obj.CustomDateRangeEnd = None
    req.Time = time_obj

    manager = ReportingServiceManager(authorization_data=client.authorization(),
                                       poll_interval_in_milliseconds=2000)
    with tempfile.TemporaryDirectory(prefix="msads-report-") as workdir:
        params = ReportingDownloadParameters(
            report_request=req,
            result_file_directory=workdir,
            result_file_name="report.csv",
            overwrite_result_file=True,
            timeout_in_milliseconds=120000,
        )
        path = manager.download_file(params)
        if path is None:
            return []  # no data for range — NOT an error; report as empty, never fabricate
        with open(path, encoding="utf-8-sig") as f:
            return parse_report_csv(f.read())
