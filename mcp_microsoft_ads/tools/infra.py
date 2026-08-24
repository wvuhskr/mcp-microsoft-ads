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
    """List advertiser accounts visible to the authenticated user."""
    svc = client.svc("CustomerManagementService")
    user = _get_user().User
    pred = svc.factory.create("ns5:ArrayOfPredicate")
    p = svc.factory.create("ns5:Predicate")
    p.Field, p.Operator, p.Value = "UserId", "Equals", str(user.Id)
    pred.Predicate = [p]
    page = svc.factory.create("ns5:Paging")
    page.Index, page.Size = 0, 100
    resp = svc.SearchAccounts(Predicates=pred, Ordering=None, PageInfo=page)
    accounts = client.as_list(getattr(resp, "AdvertiserAccount", None))
    return {"accounts": [suds_to_dict(a) for a in accounts]}


@mcp.tool()
def get_account_info() -> dict:
    """Authenticated user + roles + configured account/customer ids."""
    r = _get_user()
    roles = [cr.RoleId for cr in client.as_list(getattr(r.CustomerRoles, "CustomerRole", None))]
    return {"user": suds_to_dict(r.User), "role_ids": roles,
            "configured": {"customer_id": client.customer_id(), "account_id": client.account_id()}}
