from .. import client
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
    """List ALL advertiser accounts visible to the authenticated user (paged
    SearchAccounts under the hood; `truncated` is true only if the 1000-account
    safety cap was hit — raise MAX_PAGES if that ever happens for real)."""
    svc = client.svc("CustomerManagementService")
    user = _get_user().User
    pred = svc.factory.create("ns5:ArrayOfPredicate")
    p = svc.factory.create("ns5:Predicate")
    p.Field, p.Operator, p.Value = "UserId", "Equals", str(user.Id)
    pred.Predicate = [p]
    PAGE_SIZE, MAX_PAGES = 100, 10  # old code returned only the first 100 silently
    accounts = []
    for index in range(MAX_PAGES):
        page = svc.factory.create("ns5:Paging")
        page.Index, page.Size = index, PAGE_SIZE
        resp = svc.SearchAccounts(Predicates=pred, Ordering=None, PageInfo=page)
        batch = client.as_list(getattr(resp, "AdvertiserAccount", None))
        accounts.extend(batch)
        if len(batch) < PAGE_SIZE:
            return {"accounts": [suds_to_dict(a) for a in accounts]}
    return {"accounts": [suds_to_dict(a) for a in accounts], "truncated": True}


@mcp.tool()
def get_account_info() -> dict:
    """Authenticated user + roles + configured account/customer ids."""
    r = _get_user()
    roles = [cr.RoleId for cr in client.as_list(getattr(r.CustomerRoles, "CustomerRole", None))]
    return {"user": suds_to_dict(r.User), "role_ids": roles,
            "configured": {"customer_id": client.customer_id(), "account_id": client.account_id()}}
