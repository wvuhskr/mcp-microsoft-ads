"""Which account a tool call runs in — the agency rules, in one place.

- Allowed list: the configured account plus MS_ADS_ACCOUNT_IDS. CHANGES only ever reach
  this list.
- Read scope (MS_ADS_READ_SCOPE): `list` (default) keeps look-ups and reports on the
  allowed list too; `all` lets them reach every account the signed-in user can see.
- The pick (use_account) applies to look-ups and reports only. Once a pick is in place a
  change must name its account on the call itself, so a change can never land in an
  account that was merely picked earlier in the conversation.

Every setting is read at call time and a garbled value refuses instead of guessing.
"""
import functools
import inspect
import os
import re
import time
from typing import Annotated

from pydantic import Field

from . import client, rails, settings
from .app import mcp

ACCOUNT_IDS_ENV = "MS_ADS_ACCOUNT_IDS"
READ_SCOPE_ENV = "MS_ADS_READ_SCOPE"
_SCOPES = ("list", "all")
_ID = re.compile(r"[1-9][0-9]*")  # ASCII digits only: '１２３' must not pass as an id
_LIST_TTL_SECONDS = 600
_PAGE_SIZE, _MAX_PAGES = 100, 10

_LIST = None  # (fetched at, rows, truncated)
_PICKED: int | None = None


def reset() -> None:
    """Forget the pick and the cached account list (tests, and a fresh start)."""
    global _LIST, _PICKED
    _LIST = None
    _PICKED = None


# ---------------------------------------------------------------- settings

def allowed_ids() -> set[int]:
    """The accounts this setup may CHANGE: the configured one plus MS_ADS_ACCOUNT_IDS."""
    ids = {client.default_account_id()}
    raw = os.environ.get(ACCOUNT_IDS_ENV)
    if raw is None or not raw.strip():
        return ids
    for token in raw.split(","):
        token = token.strip()
        if not _ID.fullmatch(token):
            raise rails.RailViolation(
                f"{ACCOUNT_IDS_ENV}={raw!r} is not valid — it must be Microsoft Ads account "
                "ids (digits only) separated by commas, for example 123456789,987654321")
        ids.add(int(token))
    return ids


def read_scope() -> str:
    raw = os.environ.get(READ_SCOPE_ENV)
    if raw is None:
        return "list"
    scope = raw.strip().lower()
    if scope not in _SCOPES:
        raise rails.RailViolation(
            f"{READ_SCOPE_ENV}={raw!r} is not valid — use 'list' (look only at the configured "
            f"account and those in {ACCOUNT_IDS_ENV}) or 'all' (look at every account this "
            "sign-in can see; changes still only reach the listed accounts)")
    return scope


def changes_enabled() -> bool:
    return rails.parse_bool_env("MS_ADS_ENABLE_WRITES", False)


def _readable(account_id: int) -> bool:
    return account_id in allowed_ids() or read_scope() == "all"


# ---------------------------------------------------------------- the account list

def search_all() -> tuple[list, bool]:
    """Every account the signed-in user can see, as raw API objects, plus whether the
    1000-account safety cap cut the list short. Always asked from the configured account,
    whatever account the current call is in."""
    with client.use(None):
        svc = client.svc("CustomerManagementService")
        user = svc.GetUser(UserId=None).User
        pred = svc.factory.create("ns5:ArrayOfPredicate")
        p = svc.factory.create("ns5:Predicate")
        p.Field, p.Operator, p.Value = "UserId", "Equals", str(user.Id)
        pred.Predicate = [p]
        found = []
        for index in range(_MAX_PAGES):
            page = svc.factory.create("ns5:Paging")
            page.Index, page.Size = index, _PAGE_SIZE
            resp = svc.SearchAccounts(Predicates=pred, Ordering=None, PageInfo=page)
            batch = client.as_list(getattr(resp, "AdvertiserAccount", None))
            found.extend(batch)
            if len(batch) < _PAGE_SIZE:
                return found, False
        return found, True


def _row(a) -> dict:
    return {"account_id": int(a.Id), "number": getattr(a, "Number", None),
            "name": getattr(a, "Name", None),
            "customer_id": getattr(a, "ParentCustomerId", None),
            "currency": getattr(a, "CurrencyCode", None),
            "status": getattr(a, "AccountLifeCycleStatus", None)}


def _listing() -> tuple[list[dict], bool]:
    # ponytail: one cached list of everything the sign-in can see (ten minutes), capped at
    # 1000 accounts by search_all. A sign-in with more needs a per-id lookup here.
    global _LIST
    now = time.monotonic()
    if _LIST is None or now - _LIST[0] > _LIST_TTL_SECONDS:
        found, truncated = search_all()
        _LIST = (now, [_row(a) for a in found], truncated)
    return _LIST[1], _LIST[2]


def _not_found(ref):
    cut = " (the account list was cut short at 1000 accounts)" if _listing()[1] else ""
    return rails.RailViolation(
        f"ACCOUNT_NOT_FOUND: {ref!r} is not an account id or account number this sign-in "
        f"can see{cut}. Use find_accounts to look an account up by name.")


def _row_for(account_id: int) -> dict:
    for row in _listing()[0]:
        if row["account_id"] == account_id:
            return row
    raise _not_found(account_id)


# ---------------------------------------------------------------- choosing the account

def _text(ref) -> str:
    if isinstance(ref, bool) or not isinstance(ref, (str, int)):
        raise rails.RailViolation(f"ACCOUNT_NOT_FOUND: {ref!r} is not an account id")
    return str(ref).strip()


def _exact_id(ref) -> int:
    """An account id, or an account number (the X1234567 style code), to its id."""
    text = _text(ref)
    if _ID.fullmatch(text):
        return int(text)
    for row in _listing()[0]:
        if (row["number"] or "").casefold() == text.casefold():
            return row["account_id"]
    raise _not_found(ref)


def _account(account_id: int, source: str) -> client.Account:
    if not _readable(account_id):
        raise rails.RailViolation(
            f"ACCOUNT_NOT_ALLOWED: account {account_id} is not one this setup may look at. "
            f"Add it to {ACCOUNT_IDS_ENV}, or set {READ_SCOPE_ENV}=all to look at (never "
            "change) every account this sign-in can see.")
    if account_id == client.default_account_id():
        known = _configured_row()
        return client.Account(id=account_id, customer_id=client.default_customer_id(),
                              name=known.get("name"), number=known.get("number"),
                              currency=known.get("currency"), source=source)
    row = _row_for(account_id)
    if row["customer_id"] is None:
        raise rails.RailViolation(
            f"ACCOUNT_NOT_FOUND: Microsoft did not say which customer owns account {account_id}")
    return client.Account(id=account_id, customer_id=int(row["customer_id"]), name=row["name"],
                          number=row["number"], currency=row["currency"], source=source)


def _configured_row() -> dict:
    """Name, number and currency of the configured account. A single-account setup never
    asks Microsoft for this (it uses whatever is already in hand); once several accounts
    are in play the account list is fetched so previews and results can name it."""
    if _several_configured():
        rows = _listing()[0]
    elif _LIST is not None:
        rows = _LIST[1]
    else:
        return {}
    return next((r for r in rows if r["account_id"] == client.default_account_id()), {})


def _several_configured() -> bool:
    return read_scope() == "all" or len(allowed_ids()) > 1


def enter(ref) -> client.Account | None:
    """The account a tool call runs in. No account named: the pick if there is one, else
    None (the configured account). A named account must be an exact id or account number."""
    # Parsed on every call, so a garbled setting refuses everything, including a look-up
    # in the configured account, instead of quietly being ignored until it matters.
    allowed_ids()
    read_scope()
    if ref is None or (isinstance(ref, str) and not ref.strip()):
        picked = _PICKED  # read once: tools can run in threads and the pick can be cleared
        return None if picked is None else _account(picked, "pick")
    return _account(_exact_id(ref), "explicit")


_CHOSEN_BY = {"explicit": "account_id argument", "pick": "use_account pick",
              "configured": "configured account"}


def describe(acct: client.Account | None) -> dict:
    if acct is None:
        known = _configured_row()
        acct = client.Account(id=client.default_account_id(), customer_id=0,
                              name=known.get("name"), currency=known.get("currency"),
                              source="configured")
    out = {"account_id": acct.id}
    if acct.name is not None:
        out["name"] = acct.name
    if acct.currency is not None:
        out["currency"] = acct.currency
    out["chosen_by"] = _CHOSEN_BY[acct.source]
    return out


# ---------------------------------------------------------------- changes

def check_change_named(acct: client.Account | None) -> None:
    """Draft time: under a pick, a change must name its account on the call itself."""
    if (acct is None and _PICKED is not None) or (acct is not None and acct.source == "pick"):
        raise rails.RailViolation(
            "ACCOUNT_REQUIRED: an account is picked with use_account, and a pick only "
            "applies to look-ups and reports. Pass account_id on this call to say which "
            "account the change is for.")


def check_change_listed(acct: client.Account | None) -> None:
    """Draft time AND apply time: changes only ever reach the allowed list."""
    if acct is not None and acct.id not in allowed_ids():
        raise rails.RailViolation(
            f"ACCOUNT_NOT_ALLOWED: account {acct.id} can be looked at but not changed. "
            f"Changes only reach the configured account and those in {ACCOUNT_IDS_ENV}.")


def check_limits_currency(acct: client.Account | None) -> None:
    """Draft time AND apply time: the environment spending limits are plain numbers
    written for the configured account. They may cover another account only when it is
    known to use the same currency; otherwise that account needs limits of its own."""
    if acct is None or acct.id == client.default_account_id():
        return
    if settings.cap("max_daily_budget") is not None:
        return  # it has its own pair of limits (settings enforces the pair)
    base = _configured_row().get("currency")
    if acct.currency is None or base is None or acct.currency != base:
        raise rails.RailViolation(
            f"LIMITS_REQUIRED: account {acct.id} is in {acct.currency or 'an unknown currency'} "
            f"and the shared spending limits are written for the configured account "
            f"({base or 'currency unknown'}). Give this account its own max_daily_budget and "
            f"max_cpc under accounts: {acct.id} in {settings.config_path()} before changing it.")


def several(acct: client.Account | None) -> bool:
    """True once more than one account is in play, so drafts must say which one."""
    return acct is not None or _several_configured()


# ---------------------------------------------------------------- tools' shared logic

def _public(row: dict) -> dict:
    return {k: row[k] for k in ("account_id", "number", "name", "currency", "status")}


def find_accounts(query: str = "", limit: int = 50) -> dict:
    rows, truncated = _listing()
    needle = (query or "").strip().casefold()
    allowed, can_change = allowed_ids(), changes_enabled()
    hits = [r for r in rows if _readable(r["account_id"]) and (
        not needle or needle in (r["name"] or "").casefold()
        or needle in (r["number"] or "").casefold() or needle in str(r["account_id"]))]
    hits.sort(key=lambda r: ((r["name"] or "").casefold(), r["account_id"]))
    limit = max(1, min(int(limit), 200))
    out = {"accounts": [{**_public(r), "can_change": can_change and r["account_id"] in allowed}
                        for r in hits[:limit]],
           "count": len(hits), "read_scope": read_scope(), "changes_enabled": can_change}
    if truncated or len(hits) > limit:
        out["truncated"] = True
    return out


def _friendly_id(ref) -> int:
    """use_account also takes a name, when exactly one readable account matches it."""
    text = _text(ref)
    if _ID.fullmatch(text):
        return int(text)
    readable = [r for r in _listing()[0] if _readable(r["account_id"])]
    fold = text.casefold()
    for key, exact in (("number", True), ("name", True), ("name", False)):
        hits = [r for r in readable if (fold == (r[key] or "").casefold() if exact
                                        else fold in (r[key] or "").casefold())]
        if len(hits) == 1:
            return hits[0]["account_id"]
        if len(hits) > 1:
            names = ", ".join(f"{r['name']} ({r['account_id']})" for r in sorted(
                hits, key=lambda r: (r["name"] or "").casefold())[:10])
            raise rails.RailViolation(
                f"ACCOUNT_AMBIGUOUS: {text!r} matches more than one account: {names}. "
                "Pick one by its account id.")
    raise _not_found(ref)


def use_account(account=None) -> dict:
    global _PICKED
    if account is None or (isinstance(account, str) and not account.strip()):
        _PICKED = None
        return {"picked": None, "note": "Look-ups and reports use the configured account again."}
    acct = _account(_friendly_id(account), "pick")
    _PICKED = acct.id
    return {"picked": describe(acct),
            "note": "Look-ups and reports now use this account. A change must still name "
                    "its account with account_id on the call itself."}


def current_account() -> dict:
    picked = _PICKED
    shown = None
    if picked is not None:
        try:
            shown = describe(_account(picked, "pick"))
        except rails.RailViolation as e:
            shown = {"account_id": picked, "unavailable": str(e)}
    return {"picked": shown, "configured_account_id": client.default_account_id(),
            "read_scope": read_scope(), "change_account_ids": sorted(allowed_ids()),
            "changes_enabled": changes_enabled()}


# ---------------------------------------------------------------- the tool wrapper

_ACCOUNT_PARAM = inspect.Parameter(
    "account_id", inspect.Parameter.KEYWORD_ONLY, default=None,
    annotation=Annotated[str | int | None, Field(description=(
        "Which account to run this in: an account id or account number from "
        "find_accounts. Leave out to use the account picked with use_account (look-ups "
        "and reports only) or, with no pick, the configured account. A change must name "
        "its account here whenever a pick is in place."))])


def account_tool():
    """Register a tool that runs in one account. Adds an optional `account_id` argument,
    runs the tool with that account active, and names the account in the result whenever
    it is not simply the configured one."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, account_id=None, **kwargs):
            if account_id is None and client.in_call():
                # A tool calling another tool stays in the account already chosen: it must
                # never re-read the pick halfway through (least of all while a change lands).
                return fn(*args, **kwargs)
            acct = enter(account_id)
            with client.use(acct):
                result = fn(*args, **kwargs)
            if several(acct) and isinstance(result, dict) and "account" not in result:
                result = {**result, "account": describe(acct)}
            return result

        sig = inspect.signature(fn)
        wrapper.__signature__ = sig.replace(parameters=[*sig.parameters.values(), _ACCOUNT_PARAM])
        wrapper.__annotations__ = {**getattr(fn, "__annotations__", {}),
                                   "account_id": _ACCOUNT_PARAM.annotation}
        mcp.tool()(wrapper)
        return wrapper
    return deco

