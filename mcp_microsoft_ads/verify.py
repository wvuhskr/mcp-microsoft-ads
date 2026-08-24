"""Read-back verification. NEVER verify bid strategy against campaign.BiddingScheme
(reads None on MS) — fetch the ad group and check InheritedBidStrategyType/TargetCpa."""


def _dig(obj, dotted: str):
    for part in dotted.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def verify_fields(fetch_fn, expect: dict) -> dict:
    actual_obj = fetch_fn()
    actual, mismatches = {}, []
    for field, want in expect.items():
        got = _dig(actual_obj, field)
        actual[field] = got
        if got != want:
            mismatches.append({"field": field, "expected": want, "actual": got})
    return {"verified": not mismatches, "expected": expect, "actual": actual,
            "mismatches": mismatches}
