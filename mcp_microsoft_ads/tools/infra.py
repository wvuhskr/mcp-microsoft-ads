from .. import accounts, client
from ..app import mcp
from ..util import suds_to_dict


def _get_user():
    return client.svc("CustomerManagementService").GetUser(UserId=None)


@mcp.tool()
def health_check() -> dict:
    """Verify auth chain: token refresh + Customer Management GetUser round-trip."""
    u = _get_user()
    return {"ok": True, "user_id": u.User.Id, "customer_id": u.User.CustomerId}


@mcp.tool()
def list_accounts() -> dict:
    """List ALL advertiser accounts visible to the authenticated user, as Microsoft
    returns them (paged SearchAccounts under the hood; `truncated` is true only if the
    1000-account safety cap was hit). This always shows everything the sign-in can
    see, whatever MS_ADS_READ_SCOPE says, because it is how you find the ids to allow.
    For a short, searchable list of the accounts this setup may look at or change, use
    find_accounts."""
    found, truncated = accounts.search_all()
    out = {"accounts": [suds_to_dict(a) for a in found]}
    if truncated:
        out["truncated"] = True
    return out


@mcp.tool()
def find_accounts(query: str = "", limit: int = 50) -> dict:
    """Find client accounts by part of a name, an account number or an account id. Leave
    query empty to list every account this setup may look at. Each row says whether
    changes are possible there (`can_change`). Read-only.

    NOT live-verified against a sign-in with more than one account."""
    return accounts.find_accounts(query, limit)


@mcp.tool()
def use_account(account: str | int | None = None) -> dict:
    """Pick the account that look-ups and reports use from now on, by account id, account
    number, or a name that matches exactly one account. Call with no account to go back
    to the configured one. The pick never applies to changes: a change must name its
    account with account_id on the call itself. Changes nothing in Microsoft Ads.

    NOT live-verified against a sign-in with more than one account."""
    return accounts.use_account(account)


@mcp.tool()
def current_account() -> dict:
    """Which account is picked, which is configured, how far look-ups reach
    (`read_scope`), and which accounts changes may reach. Read-only."""
    return accounts.current_account()


@mcp.tool()
def get_account_info() -> dict:
    """Authenticated user + roles + configured account/customer ids."""
    r = _get_user()
    roles = [cr.RoleId for cr in client.as_list(getattr(r.CustomerRoles, "CustomerRole", None))]
    return {"user": suds_to_dict(r.User), "role_ids": roles,
            "configured": {"customer_id": client.customer_id(), "account_id": client.account_id()}}
