from types import SimpleNamespace as NS

from mcp_microsoft_ads import client
from mcp_microsoft_ads.tools import infra


def fake_customer_svc(user_id=777888999):
    def _svc(name):
        assert name == "CustomerManagementService"
        acct = NS(Id=111222333, Number="X000AAAA", ParentCustomerId=444555666,
                  AccountLifeCycleStatus="Active", Name="Example Home Services")
        factory = NS(create=lambda t: NS(Predicate=None, long=None,
                                         Index=None, Size=None))
        return NS(
            GetUser=lambda UserId: NS(User=NS(Id=user_id, UserName="user@example.com",
                                              Name=NS(FirstName="Jamie", LastName="Doe"),
                                              CustomerId=444555666),
                                      CustomerRoles=NS(CustomerRole=[NS(RoleId=41, CustomerId=444555666)])),
            # matches live SearchAccounts shape: ArrayOfAdvertiserAccount returned
            # directly, not nested under a .Accounts wrapper (verified via live smoke)
            SearchAccounts=lambda Predicates, Ordering, PageInfo: NS(
                AdvertiserAccount=[acct]),
            factory=factory,
        )
    return _svc

def test_list_accounts(monkeypatch):
    monkeypatch.setattr(client, "svc", fake_customer_svc())
    out = infra.list_accounts()
    assert out["accounts"][0]["Number"] == "X000AAAA"
    assert out["accounts"][0]["Id"] == 111222333

def test_get_account_info(monkeypatch):
    monkeypatch.setattr(client, "svc", fake_customer_svc())
    monkeypatch.setattr(client, "customer_id", lambda: 444555666)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    out = infra.get_account_info()
    assert out["user"]["UserName"] == "user@example.com"
    assert 41 in out["role_ids"]
    assert out["configured"] == {"customer_id": 444555666, "account_id": 111222333}

def test_bare_single_item_shapes(monkeypatch):
    """Single account / single customer role collapse to bare objects (client.as_list)."""
    acct = NS(Id=111222333, Number="X000AAAA", Name="Example Home Services")
    monkeypatch.setattr(client, "svc", lambda name: NS(
        GetUser=lambda UserId: NS(User=NS(Id=1, UserName="user@example.com"),
                                  CustomerRoles=NS(CustomerRole=NS(RoleId=41, CustomerId=444555666))),
        SearchAccounts=lambda Predicates, Ordering, PageInfo: NS(AdvertiserAccount=acct),
        factory=NS(create=lambda t: NS(Predicate=None, long=None, Index=None, Size=None)),
    ))
    monkeypatch.setattr(client, "customer_id", lambda: 444555666)
    monkeypatch.setattr(client, "account_id", lambda: 111222333)
    assert infra.list_accounts()["accounts"][0]["Number"] == "X000AAAA"
    assert infra.get_account_info()["role_ids"] == [41]

def test_list_accounts_pages_past_100(monkeypatch):
    """Old code sent a single Index=0/Size=100 SearchAccounts call, silently returning
    only the first 100 accounts while the docstring implied all of them (Codex review
    2026-08-24). Serve 100 + 100 + 30 across three pages; pre-fix this returns 100."""
    def make_acct(i):
        return NS(Id=i, Number=f"X{i:07d}", Name=f"Account {i}")
    pages = [[make_acct(i) for i in range(100)],
             [make_acct(100 + i) for i in range(100)],
             [make_acct(200 + i) for i in range(30)]]
    seen_indexes = []
    def search(Predicates, Ordering, PageInfo):
        seen_indexes.append(PageInfo.Index)
        return NS(AdvertiserAccount=pages[PageInfo.Index])
    monkeypatch.setattr(client, "svc", lambda name: NS(
        GetUser=lambda UserId: NS(User=NS(Id=1, CustomerId=2),
                                  CustomerRoles=NS(CustomerRole=[NS(RoleId=41)])),
        SearchAccounts=search,
        factory=NS(create=lambda t: NS(Predicate=None, long=None, Index=None, Size=None)),
    ))
    out = infra.list_accounts()
    assert len(out["accounts"]) == 230
    assert seen_indexes == [0, 1, 2]
    assert "truncated" not in out
