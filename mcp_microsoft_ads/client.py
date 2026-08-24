"""Single choke point for MS API access. Tools never construct ServiceClient
themselves — tests monkeypatch svc()."""
from bingads import ServiceClient

from . import auth

_AUTH = None
_CREDS = None
_SERVICES = {}


def reset():
    global _AUTH, _CREDS
    _AUTH = None
    _CREDS = None
    _SERVICES.clear()


def _authorization():
    global _AUTH, _CREDS
    if _AUTH is None:
        _AUTH, _CREDS = auth.build_authorization()
    return _AUTH


def creds() -> dict:
    _authorization()
    return _CREDS


def authorization():
    return _authorization()


def account_id() -> int:
    return int(creds()["account_id"])


def customer_id() -> int:
    return int(creds()["customer_id"])


def svc(name: str):
    if name not in _SERVICES:
        _SERVICES[name] = ServiceClient(service=name, version=13,
                                        authorization_data=_authorization(),
                                        environment="production")
    return _SERVICES[name]


def blank(svc_obj, type_name: str):
    """Factory-create `type_name` then null every field, so an Update* call only touches
    what you explicitly set afterward. Live-verified (Task 15): the raw factory default
    (e.g. AdGroup.AdRotation.Type == '') fails server-side deserialization with
    "invalid enum value ''" — MS Update* payloads must be built this way, not as bare
    SimpleNamespace/dict objects (those also fail, with a SOAP "expecting Element,
    encountered Text" fault, before ever reaching business logic). No-op against test
    fakes whose factory.create() stand-ins lack suds' __keylist__.

    Full-object survival LIVE-VERIFIED 2026-07-30, scoped to UpdateCampaigns/Campaign only:
    a blank()-built Campaign (Id + DailyBudget only) changed ONLY DailyBudget —
    GetCampaignsByIds before/after diff with all 37 CampaignAdditionalField parts
    requested (23 fields actually returned) showed every one of those 23 read-back fields
    (BiddingScheme, Languages, TimeZone, Settings, ...) and all attached criterions
    (schedule rows, geo exclusion) untouched. Null fields mean "leave unchanged", not
    "overwrite" — for this entity. Other Update* entities (AdGroup, Ad, Keyword, ...) are
    documented to follow the same contract but have not been probed live here.
    """
    obj = svc_obj.factory.create(type_name)
    for k in list(getattr(obj, "__keylist__", None) or []):
        setattr(obj, k, None)
    return obj


def effective_strategy(ad_group) -> str | None:
    """Given an ad-group suds object, its effective bid strategy: BiddingScheme.
    InheritedBidStrategyType, falling back to BiddingScheme.Type (live-verified). Shared
    so every caller (audiences.py, keywords_write.py, adgroups_write.py) reads it the same
    way instead of each carrying its own copy of this fallback."""
    bs = getattr(ad_group, "BiddingScheme", None)
    if bs is None:
        return None
    return getattr(bs, "InheritedBidStrategyType", None) or getattr(bs, "Type", None)


def as_list(x):
    """suds unmarshals single-item arrays as a bare object (or bare int, for a
    `long` array like `CampaignCriterionIds.long`) rather than a list — normalize.
    Public: every response id-extraction site (schedule/geo/negatives/keywords
    writes) must route through this, not raw `getattr(..., "long", None) or []`,
    which silently truncates/mishandles a single-item result."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _error_dict(e) -> dict:
    return {"index": getattr(e, "Index", None), "code": getattr(e, "ErrorCode", None),
            "number": getattr(e, "Code", None), "message": getattr(e, "Message", None)}


def partial_errors(resp) -> list[dict]:
    """MS returns per-item errors inside HTTP-200 bodies. Empty list == clean.
    Sweeps both response shapes: flat `PartialErrors` (ArrayOfBatchError, most
    Update*/Delete* calls) and nested `NestedPartialErrors` (ArrayOfBatchErrorCollection,
    Add* calls like AddCampaignCriterions) — each BatchErrorCollection carries its own
    Code/Index/Message plus a nested BatchErrors.BatchError list. Both shapes flatten
    into the same list-of-dicts.

    suds strips one wrapper level when a `*Response` element declares exactly ONE
    child (suds `bindings/binding.py::get_reply` returns the single child unmarshalled;
    with 2+ children the wrapper is kept). For the 15 Update*/Delete* operations whose
    response's ONLY declared child is PartialErrors/NestedPartialErrors, `resp` IS the
    ArrayOfBatchError(Collection) itself — `resp.PartialErrors` doesn't exist, so both
    branches below fall back to `resp` directly when the named wrapper is absent."""
    pe = getattr(resp, "PartialErrors", None) or resp
    out = [_error_dict(e) for e in as_list(getattr(pe, "BatchError", None))]

    npe = getattr(resp, "NestedPartialErrors", None) or resp
    for coll in as_list(getattr(npe, "BatchErrorCollection", None)):
        out.append(_error_dict(coll))
        inner = getattr(coll, "BatchErrors", None)
        out.extend(_error_dict(e) for e in as_list(getattr(inner, "BatchError", None) if inner else None))
    return out


def long_ids(container) -> list[int]:
    """Add* responses return ArrayOfNullableOflong: a NIL entry marks a batch item that
    FAILED (its error is in PartialErrors, keyed by Index). Skip nils — int(None) would
    raise TypeError inside apply(), after the write has already landed."""
    return [int(x) for x in as_list(getattr(container, "long", None)) if x is not None]
