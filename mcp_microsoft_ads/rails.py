"""Safety rails. Every write tool: preview -> create_draft; confirm_and_apply -> apply_draft."""
import math
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from . import audit, settings
from .app import ToolError

SMART_BIDDING = {"MaxConversions", "MaxConversionValue", "TargetCpa", "TargetRoas"}

# Allowlist (Task C hardening): %-bid adjustments and keyword bid edits are permitted ONLY
# for these effective strategies. Derived from the BiddingScheme-derived Type strings in the
# bingads WSDL (campaignmanagement_service.xml) and client.effective_strategy's normalization
# (client.py:73, which returns InheritedBidStrategyType or falls back to Type — same string
# space as these WSDL type names minus the "BiddingScheme" suffix):
#   - ManualCpc:    advertiser sets the CPC bid directly — the canonical manual scheme.
#   - EnhancedCpc:  advertiser-set base bid, MS applies small per-auction nudges — still
#                   advertiser-controlled at the bid/adjustment level (Task 25e's "manual" case).
#   - ManualCpv:    advertiser sets cost-per-view directly (video/CPV campaigns).
#   - ManualCpm:    advertiser sets cost-per-mille directly (impression-based campaigns).
#   - ManualCpa:    advertiser sets cost-per-acquisition bid directly (Shopping).
# Deliberately excluded: MaxClicks/Max*/Target*/Commission/PercentCpc (MS-optimized formulas,
# same failure mode as SMART_BIDDING) and InheritFromParent (not a concrete strategy — the
# real scheme lives on the parent and isn't knowable from this string alone, so it fails
# closed like an unrecognized strategy rather than being assumed safe). Note: excluding
# MaxClicks/PercentCpc here is OWNER POLICY (owner's standing no-%-adjustments-on-auto-bidding
# rule), not a platform fact — the MS UI itself accepts bid adjustments under MaxClicks.
MANUAL_BIDDING = {"ManualCpc", "EnhancedCpc", "ManualCpv", "ManualCpm", "ManualCpa"}

# Bounds are criterion-dependent on MS (e.g. device criteria accept a -100 floor); only
# audience bid adjustments are exposed today, so -90 covers that case. Revisit if a
# device_bid_adjustment_pct (or other criterion type) is added.
MIN_BID_ADJUSTMENT_PCT = -90
MAX_BID_ADJUSTMENT_PCT = 900


class RailViolation(ValueError, ToolError):
    """Intentional refusal, safe to show through both MCP SDK generations."""


class PartialWriteError(ToolError):
    """Plan item 11: raised by a multi-step apply_fn when step 1 landed but a later
    step raised (not a PARTIAL-ERROR abort dict -- an actual exception: SOAP fault,
    network, suds error). `.partial` carries every landed ID/action so the caller can
    reconcile account state before retrying -- a retry re-runs ALL steps from scratch,
    it does not resume. No compensation/rollback re-architecture in v1; this only makes
    the landed state visible instead of letting it vanish into a bare error string."""

    def __init__(self, message: str, partial: dict):
        self.partial = partial
        super().__init__(
            f"{message} — landed state: {partial} — reconcile account state before "
            "retrying (a retry re-runs ALL steps from scratch, it does not resume).")


def parse_positive_float_env(name: str, default: float) -> float:
    """Shared numeric-rail parser (plan item 9), read at CALL time (matching
    parse_bool_env / draft_ttl_seconds -- never cached at import). Unset -> default.
    Set -> must parse to a finite, strictly positive float (zero is NOT positive --
    reject it), else fails closed with a RailViolation naming the var, the bad value,
    and the requirement. Bare float(os.environ.get(...)) let garbage raise a raw
    ValueError and let nan/inf/negative through silently -- both are rail bypasses."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        val = float(raw)
    except ValueError:
        raise RailViolation(
            f"{name}={raw!r} is not a valid number — must be a finite positive number")
    if not math.isfinite(val) or val <= 0:
        raise RailViolation(
            f"{name}={raw!r} must be a finite positive number (got {val})")
    return val


def max_daily_budget() -> float:
    # Cap is in the account's currency (not USD) -- Microsoft Ads accounts are not
    # all USD-denominated, and this cap compares directly against amounts read from
    # the account's own budget/bid fields. Default 1000, owner decision (unchanged).
    return parse_positive_float_env("MS_ADS_MAX_DAILY_BUDGET", 1000)


def max_cpc() -> float:
    # Cap is in the account's currency (not USD) -- same reasoning as max_daily_budget.
    # Default 50, owner decision (unchanged).
    return parse_positive_float_env("MS_ADS_MAX_CPC", 50)


def parse_bool_env(name: str, default: bool = False) -> bool:
    """Strict boolean env parsing, read at CALL time (never cached at import, matching
    draft_ttl_seconds). Unset -> default. Accepted tokens (stripped, lowercased):
    true/1 -> True, false/0 -> False. Anything else fails closed with a clear error
    naming the var, the bad value, and the accepted tokens -- shared by
    MS_ADS_ENABLE_WRITES here and reused as-is by a later MS_ADS_ALLOW_APPLY_RECOMMENDATION
    gate (kept generic on purpose, not built here)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in ("true", "1"):
        return True
    if val in ("false", "0"):
        return False
    raise RailViolation(
        f"{name}={raw!r} is not a valid boolean — accepted tokens: true/false/1/0 (case-insensitive)")


def check_writes_enabled() -> None:
    """Belt-and-suspenders gate (plan item 8): called from BOTH create_draft and
    apply_draft, so a draft created while writes were enabled cannot be applied after
    the flag is cleared. Off by default -- strangers get a read-only server out of the
    box. Refusal names the var and how to enable it."""
    if not parse_bool_env("MS_ADS_ENABLE_WRITES", False):
        raise RailViolation(
            "mutating tools are disabled: set MS_ADS_ENABLE_WRITES=true to enable writes")


def check_apply_recommendation_allowed(tool: str) -> None:
    """Second, tool-specific gate (plan item 11b) on top of check_writes_enabled: even
    with global writes on, apply_recommendation's monetary effect can't be bounded by
    the budget/bid ceilings (a recommendation can be anything Microsoft's optimizer
    proposes), so it needs its own default-off opt-in. Hardcoding this one tool name
    here (rather than a generic per-tool registry) is deliberate -- it's the only tool
    with unbounded effect today; dismiss_recommendation has no monetary effect and stays
    ungated. Called from both create_draft and apply_draft, same belt-and-suspenders
    shape as check_writes_enabled, so a draft made while the flag was on cannot be
    applied after it's cleared."""
    if tool != "apply_recommendation":
        return
    if not parse_bool_env("MS_ADS_ALLOW_APPLY_RECOMMENDATION", False):
        raise RailViolation(
            "apply_recommendation is disabled: MS_ADS_ENABLE_WRITES alone is not enough "
            "for this tool -- set MS_ADS_ALLOW_APPLY_RECOMMENDATION=true to enable it")


def draft_ttl_seconds() -> float:
    # Owner decision, 2026-07-31: a draft previews the account at draft time; past
    # this age the account may have moved underneath it, so apply_draft must refuse a
    # stale preview instead of letting it silently become tonight's write.
    return parse_positive_float_env("MS_ADS_DRAFT_TTL_SECONDS", 3600)


def _check_valid_amount(amount, label: str):
    """Shared guard (plan item 9) for check_budget/check_bid: reject a requested amount
    that is non-finite (nan/inf), non-positive (0, negative), or not a real number --
    bool counts as int in Python, so an isinstance(amount, bool) check catches a stray
    True/False before it's silently treated as 1/0. Runs before the cap comparison."""
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise RailViolation(f"{label} {amount!r} is not a valid number")
    if not math.isfinite(amount) or amount <= 0:
        raise RailViolation(f"{label} {amount} must be a finite positive number")


def check_budget(amount: float):
    _check_valid_amount(amount, "daily budget")
    if amount > max_daily_budget():
        raise RailViolation(f"daily budget {amount} exceeds cap {max_daily_budget()} (MS_ADS_MAX_DAILY_BUDGET)")


def check_bid(amount: float):
    _check_valid_amount(amount, "bid/tCPA")
    if amount > max_cpc():
        raise RailViolation(f"bid/tCPA {amount} exceeds cap {max_cpc()} (MS_ADS_MAX_CPC)")


def check_content(texts):
    for t in texts:
        low = (t or "").lower()
        for term in settings.blocked_terms():
            if term in low:
                raise RailViolation(
                    f"blocked term '{term}' in '{t}' — see blocked_terms in {settings.config_path()}")


def _reject_not_manual_bidding(effective_strategy, subject: str):
    """Shared allowlist decision (Task C — inverts the old SMART_BIDDING blocklist):
    permitted ONLY when effective_strategy is in MANUAL_BIDDING. Anything else — a
    confirmed Smart Bidding scheme, an unreadable strategy, or an unrecognized one
    (e.g. TargetImpressionShare, CostPerSale, or any future scheme) — rejects, each
    with a distinguishable message."""
    if effective_strategy in SMART_BIDDING:
        raise RailViolation(
            f"{subject} rejected: effective strategy {effective_strategy!r} is Smart Bidding. "
            "Allowed levers: pause/enable, schedule windows, tCPA, budget.")
    if not effective_strategy:
        # Owner decision (Task 25e): fail closed. Live-demonstrated on a campaign with zero
        # ad groups — effective_strategy read back None and the adjustment would have been
        # allowed through. Message must read distinctly from the Smart-Bidding branch above:
        # this is "couldn't tell", not "confirmed Smart Bidding".
        raise RailViolation(
            f"{subject} rejected: effective bid strategy could not be determined, "
            "so this fails closed rather than assuming it's safe — a campaign with zero ad "
            "groups is the usual cause. Allowed levers: pause/enable, schedule windows, tCPA, budget.")
    # Truthy, not Smart Bidding, not in MANUAL_BIDDING: an unrecognized strategy. Same
    # fail-closed stance, third distinct message — this is neither "confirmed Smart" nor
    # "couldn't tell", it's "recognized a strategy we don't allowlist as manual".
    raise RailViolation(
        f"{subject} rejected: effective strategy {effective_strategy!r} is not a recognized manual "
        "bidding strategy (allowlist: " + ", ".join(sorted(MANUAL_BIDDING)) + "), so this fails "
        "closed rather than assuming it's safe. Allowed levers: pause/enable, schedule windows, tCPA, budget.")


def check_no_pct_adjustment(effective_strategy, kwargs: dict):
    pct = {k: v for k, v in kwargs.items() if k.endswith("_bid_adjustment_pct") and v is not None}
    if not pct:
        return
    if effective_strategy not in MANUAL_BIDDING:
        _reject_not_manual_bidding(effective_strategy, f"%-bid adjustments {sorted(pct)}")
    for k, v in pct.items():
        if not (MIN_BID_ADJUSTMENT_PCT <= v <= MAX_BID_ADJUSTMENT_PCT):
            raise RailViolation(
                f"{k}={v} rejected: Microsoft Ads only accepts bid adjustments in "
                f"[{MIN_BID_ADJUSTMENT_PCT}, {MAX_BID_ADJUSTMENT_PCT}] (percent).")


def check_manual_bid_allowed(effective_strategy, subject: str):
    """Shared fail-closed guard for any fixed-amount bid write (keyword Bid, ad-group
    CpcBid): permitted ONLY for MANUAL_BIDDING strategies. MS silently ignores such
    bids under Smart Bidding instead of erroring (live-verified for UpdateKeywords —
    empty PartialErrors, bid unchanged on read-back), so it must be caught before the
    call, not left to look like a no-op success. Same fail-closed stance as
    check_no_pct_adjustment when the strategy can't be read at all or isn't a
    recognized manual scheme."""
    if effective_strategy in MANUAL_BIDDING:
        return
    _reject_not_manual_bidding(effective_strategy, subject)


def check_keyword_bid_allowed(effective_strategy):
    """Pre-reject at draft time (Task 25e owner decision) — see check_manual_bid_allowed;
    kept as the keyword-specific entry point its call sites and tests already use."""
    check_manual_bid_allowed(effective_strategy, "keyword bid change")


@dataclass
class Draft:
    id: str
    tool: str
    preview: dict
    apply_fn: Callable[[], dict]
    validate_fn: Callable[[], None] | None = None
    created_at: float = field(default_factory=time.time)


_DRAFTS: dict[str, Draft] = {}


def _prune_expired_drafts() -> None:
    # Bounds _DRAFTS (queued finding: "unbounded dict, no TTL/lock"). ponytail: single-
    # threaded stdio server — an inline sweep on normal draft traffic is the whole fix;
    # no lock, no background thread needed (out of scope, owner decision 2026-07-31).
    # Trade-off, known and accepted (owner decision 2026-07-31): sweeps every expired draft, not
    # just the one being requested, so an unrelated create_draft/apply_draft can sweep
    # away a different stale draft first — its later apply_draft then reports the generic
    # "unknown or already-applied" instead of the more specific "expired" message. No
    # safety impact (pop-before-check still prevents double-apply); not fixed with
    # tombstoning, just naming the diagnostic-clarity gap honestly.
    ttl = draft_ttl_seconds()
    now = time.time()
    for k in [k for k, v in _DRAFTS.items() if now - v.created_at > ttl]:
        del _DRAFTS[k]


def create_draft(tool: str, preview: dict, apply_fn: Callable[[], dict],
                 validate_fn: Callable[[], None] | None = None) -> dict:
    """validate_fn: optional re-check of the tool's draft-time policy rails (spend
    ceilings, bid-strategy allowlist), run again by apply_draft immediately before
    mutation. Rails read env at call time, so a ceiling lowered after drafting — or a
    bid strategy changed on the account — refuses the stale draft instead of applying
    it within the TTL window. A refusal does NOT consume the draft."""
    check_writes_enabled()  # before any preview/audit side effect
    check_apply_recommendation_allowed(tool)  # second gate, same "before side effects" rule
    _prune_expired_drafts()
    d = Draft(id=uuid.uuid4().hex[:12], tool=tool, preview=preview, apply_fn=apply_fn,
              validate_fn=validate_fn)
    audit.log_event(tool, "draft", {"draft_id": d.id, "preview": preview})
    _DRAFTS[d.id] = d
    return {"draft_id": d.id, "dry_run": True, "preview": preview,
            "next": f"confirm_and_apply(draft_id='{d.id}')"}


def apply_draft(draft_id: str) -> dict:
    check_writes_enabled()  # before pop -- a refused apply must NOT consume the draft
    peeked = _DRAFTS.get(draft_id)  # peek only -- the tool name is known from the draft
    if peeked is not None:
        check_apply_recommendation_allowed(peeked.tool)  # before pop, same non-consuming rule
        if peeked.validate_fn is not None and time.time() - peeked.created_at <= draft_ttl_seconds():
            # re-run the tool's policy rails against CURRENT env + account state, still
            # before pop: a rail refusal must not consume the draft (matching the gate
            # checks above). Expired drafts skip this — the pop path below raises the
            # dedicated expired message without burning a live API call first.
            peeked.validate_fn()
    d = _DRAFTS.pop(draft_id, None)
    _prune_expired_drafts()  # sweep other stale entries while we're touching the dict
    if d is None:
        raise RailViolation(f"unknown or already-applied draft_id '{draft_id}' — re-draft to retry")
    age = time.time() - d.created_at
    ttl = draft_ttl_seconds()
    if age > ttl:
        # Distinct message on purpose (owner decision 2026-07-31): "unknown" above means no such
        # draft; this means it existed and went stale — the caller needs to know its
        # preview no longer describes reality and re-draft rather than just retry.
        raise RailViolation(
            f"draft_id '{draft_id}' expired: drafted {age:.0f}s ago, TTL is {ttl:.0f}s "
            "(MS_ADS_DRAFT_TTL_SECONDS) — re-draft to retry")
    try:
        result = d.apply_fn()
    except Exception as e:
        try:
            error_data = {"draft_id": d.id, "preview": d.preview, "error": str(e)}
            if isinstance(e, PartialWriteError):
                error_data["partial"] = e.partial
            audit.log_event(d.tool, "error", error_data)
        except Exception as audit_exc:
            # (b) owner decision 2026-07-31: a broken audit log must not eat the real fault, so the
            # `raise` below still carries the ORIGINAL apply_fn exception unchanged — but
            # that means this audit failure itself is carried by nothing and would vanish
            # with zero trace. Print it to stderr (stdout is the MCP JSON-RPC stream —
            # writing there would corrupt the protocol) so it lands somewhere, mirroring
            # the audit_error key surfaced on the success path below.
            print(f"apply_draft: audit log failed for {d.tool} draft {d.id} (error phase): {audit_exc}",
                  file=sys.stderr)
        raise
    out = {"draft_id": d.id, "tool": d.tool, "applied": True, "result": result}
    try:
        audit.log_event(d.tool, "apply", {"draft_id": d.id, "preview": d.preview, "result": result})
    except Exception as audit_exc:
        # (a) owner decision 2026-07-31: apply_fn already landed on the live account — an audit
        # failure here must not tell the caller the write failed (that invites a retry,
        # i.e. a duplicate write). Surface the gap instead of hiding it.
        out["audit_error"] = str(audit_exc)
    return out
