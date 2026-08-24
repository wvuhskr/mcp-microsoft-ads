from types import SimpleNamespace as NS

from mcp_microsoft_ads import verify


def test_verified_flat_and_nested():
    obj = NS(DailyBudget=200.0, Status="Active", CpcBid=NS(Amount=12.0))
    out = verify.verify_fields(lambda: obj, {"DailyBudget": 200.0, "CpcBid.Amount": 12.0})
    assert out["verified"] is True and out["mismatches"] == []


def test_mismatch_reported():
    obj = NS(DailyBudget=150.0)
    out = verify.verify_fields(lambda: obj, {"DailyBudget": 200.0})
    assert out["verified"] is False
    assert out["mismatches"] == [{"field": "DailyBudget", "expected": 200.0, "actual": 150.0}]


def test_missing_attr_is_mismatch():
    out = verify.verify_fields(lambda: NS(), {"Nope.Deep": 1})
    assert out["verified"] is False
