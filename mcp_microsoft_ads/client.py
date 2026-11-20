"""Single choke point for MS API access. Tools never construct ServiceClient
themselves — tests monkeypatch svc().

Which account a call runs in: the configured one (credentials file) unless an Account is
active for the current tool call (see use()). Every other account gets its OWN
AuthorizationData and service clients; the configured account's objects are never
mutated, because tools can run in threads and a shared header would leak one client's
call into another client's account."""
import contextlib
import contextvars
from dataclasses import dataclass

from bingads import AuthorizationData, ServiceClient

from . import auth

_AUTH = None
_CREDS = None
_SERVICES = {}
_AUTHS = {}


@dataclass(frozen=True)
class Account:
    """An account a tool call runs in. source: 'explicit' (the call named it) or 'pick'
    (use_account). name/number/currency are known only when it came from the account list."""
    id: int
    customer_id: int
    name: str | None = None
    number: str | None = None
    currency: str | None = None
    source: str = "explicit"


_ACTIVE: contextvars.ContextVar = contextvars.ContextVar("active_account", default=None)
_IN_CALL: contextvars.ContextVar = contextvars.ContextVar("account_chosen", default=False)


def reset():
    global _AUTH, _CREDS
    _AUTH = None
    _CREDS = None
    _SERVICES.clear()
    _AUTHS.clear()


def _authorization():
    global _AUTH, _CREDS
    if _AUTH is None:
        _AUTH, _CREDS = auth.build_authorization()
    return _AUTH


def creds() -> dict:
    _authorization()
    return _CREDS


def active() -> Account | None:
    """The account this tool call named or picked; None means the configured account."""
    return _ACTIVE.get()


@contextlib.contextmanager
def use(account: Account | None):
    """Run a block in `account` (None = the configured account), then restore."""
    token, flag = _ACTIVE.set(account), _IN_CALL.set(True)
    try:
        yield account
    finally:
        _IN_CALL.reset(flag)
        _ACTIVE.reset(token)


def in_call() -> bool:
    """True inside use(): the account for this tool call has already been chosen (even
    when the choice is the configured account), so nothing may choose it again."""
    return _IN_CALL.get()


def default_account_id() -> int:
    return int(creds()["account_id"])


def default_customer_id() -> int:
    return int(creds()["customer_id"])


def account_id() -> int:
    acct = _ACTIVE.get()
    return acct.id if acct is not None else default_account_id()


def customer_id() -> int:
    acct = _ACTIVE.get()
    return acct.customer_id if acct is not None else default_customer_id()


def _other() -> Account | None:
    """The active account when it is not simply the configured one."""
    acct = _ACTIVE.get()
    return acct if acct is not None and acct.id != default_account_id() else None


def authorization():
    base = _authorization()
    acct = _other()
    if acct is None:
        return base
    key = (acct.id, acct.customer_id)
    if key not in _AUTHS:
        # Same sign-in (shared authentication object, so token refreshes are shared),
        # different account headers.
        _AUTHS[key] = AuthorizationData(account_id=acct.id, customer_id=acct.customer_id,
                                        developer_token=base.developer_token,
                                        authentication=base.authentication)
    return _AUTHS[key]


def svc(name: str):
    acct = _other()
    key = (name, None) if acct is None else (name, acct.id, acct.customer_id)
    if key not in _SERVICES:
        _SERVICES[key] = ServiceClient(service=name, version=13,
                                       authorization_data=authorization(),
                                       environment="production")
    return _SERVICES[key]


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
