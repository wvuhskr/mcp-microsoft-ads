from mcp_microsoft_ads.reporting_core import _PREDEFINED


def test_predefined_periods_use_exact_v13_names():
    # The Reporting API rejects spelled-out names such as LastThirtyDays.
    assert _PREDEFINED == {7: "LastSevenDays", 14: "Last14Days", 30: "Last30Days"}
