"""Agency use: several client accounts through one sign-in.

Reads may reach any account on the allowed list (or, with MS_ADS_READ_SCOPE=all, any
account the signed-in user can see). Changes only ever reach the allowed list, must
name their account once a pick is in place, and run under that account's own rules.
All fakes are offline; the default account is 111222333 (conftest).
"""
from types import SimpleNamespace as NS

import pytest

import mcp_microsoft_ads.server  # noqa: F401  — importing registers all tools
from mcp_microsoft_ads import accounts, client, rails, settings
from mcp_microsoft_ads.app import mcp
from mcp_microsoft_ads.tools import entities, infra, status

DEFAULT, DENTAL, PLUMBING = 111222333, 222000111, 333000222

ROWS = [
    NS(Id=DEFAULT, Number="X000AAAA", Name="Example Home Services", ParentCustomerId=444555666,
       CurrencyCode="USD", AccountLifeCycleStatus="Active"),
    NS(Id=DENTAL, Number="B002BBBB", Name="Northwind Dental", ParentCustomerId=900000001,
       CurrencyCode="USD", AccountLifeCycleStatus="Active"),
    NS(Id=PLUMBING, Number="C003CCCC", Name="Northwind Plumbing", ParentCustomerId=900000002,
       CurrencyCode="CAD", AccountLifeCycleStatus="Active"),
]


class FakeApi:
    """One fake for every service: records which account each call ran under."""

    def __init__(self):
        self.searches = 0
        self.calls = []  # (operation, account id active when it ran, AccountId argument)
        self.factory = NS(create=lambda t: NS(Predicate=None, long=None, Index=None, Size=None,
                                              Campaign=None))

    def svc(self, name):
        return self

    def GetUser(self, UserId):
        return NS(User=NS(Id=777888999, UserName="user@example.com", CustomerId=444555666),
                  CustomerRoles=NS(CustomerRole=[NS(RoleId=41, CustomerId=444555666)]))

    def SearchAccounts(self, Predicates, Ordering, PageInfo):
        self.searches += 1
        self.calls.append(("SearchAccounts", client.account_id(), None))
        return NS(AdvertiserAccount=list(ROWS))

    def GetCampaignsByAccountId(self, AccountId, CampaignType):
        self.calls.append(("GetCampaignsByAccountId", client.account_id(), AccountId))
        return NS(Campaign=[NS(Id=5, Name="Brand", Status="Active")])

    def GetCampaignsByIds(self, AccountId, CampaignIds, CampaignType, **kw):
        self.calls.append(("GetCampaignsByIds", client.account_id(), AccountId))
        return NS(Campaigns=NS(Campaign=[NS(Id=5, Name="Brand", Status="Active")]),
                  PartialErrors=None)

    def UpdateCampaigns(self, AccountId, Campaigns):
        self.calls.append(("UpdateCampaigns", client.account_id(), AccountId))
        return NS(BatchError=None)


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(client, "svc", fake.svc)
    return fake


@pytest.fixture
def no_api(monkeypatch):
    def boom(name):
        raise AssertionError(f"unexpected API call to {name}")
    monkeypatch.setattr(client, "svc", boom)


# ---------------------------------------------------------------- client: active account

def test_default_account_when_nothing_is_active():
    assert client.active() is None
    assert (client.account_id(), client.customer_id()) == (DEFAULT, 444555666)


def test_active_account_applies_inside_the_block_only():
    acct = client.Account(id=DENTAL, customer_id=900000001)
    with client.use(acct):
        assert (client.account_id(), client.customer_id()) == (DENTAL, 900000001)
        assert client.active() is acct
    assert client.account_id() == DEFAULT


def test_active_account_is_cleared_when_the_block_raises():
    with pytest.raises(RuntimeError):
        with client.use(client.Account(id=DENTAL, customer_id=900000001)):
            raise RuntimeError("boom")
    assert client.active() is None


def test_other_account_gets_its_own_authorization_and_never_mutates_the_default(monkeypatch):
    default_auth = NS(account_id=DEFAULT, customer_id=444555666, developer_token="tok",
                      authentication=object())
    monkeypatch.setattr(client, "_AUTH", default_auth)
    monkeypatch.setattr(client, "_CREDS", {"account_id": DEFAULT, "customer_id": 444555666})
    with client.use(client.Account(id=DENTAL, customer_id=900000001)):
        other = client.authorization()
    assert (other.account_id, other.customer_id) == (DENTAL, 900000001)
    assert other.developer_token == "tok"
    assert other.authentication is default_auth.authentication
    assert (default_auth.account_id, default_auth.customer_id) == (DEFAULT, 444555666)
    assert client.authorization() is default_auth


def test_service_clients_are_kept_per_account(monkeypatch):
    built = []

    def fake_service_client(service, version, authorization_data, environment):
        built.append((service, authorization_data.account_id))
        return NS(auth=authorization_data)

    monkeypatch.setattr(client, "ServiceClient", fake_service_client)
    monkeypatch.setattr(client, "_AUTH", NS(account_id=DEFAULT, customer_id=444555666,
                                            developer_token="tok", authentication=object()))
    monkeypatch.setattr(client, "_CREDS", {"account_id": DEFAULT, "customer_id": 444555666})
    monkeypatch.setattr(client, "_SERVICES", {})
    monkeypatch.setattr(client, "_AUTHS", {})
    a = client.svc("CampaignManagementService")
    with client.use(client.Account(id=DENTAL, customer_id=900000001)):
        b = client.svc("CampaignManagementService")
        assert client.svc("CampaignManagementService") is b
    assert client.svc("CampaignManagementService") is a
    assert a.auth.account_id == DEFAULT and b.auth.account_id == DENTAL
    assert built == [("CampaignManagementService", DEFAULT), ("CampaignManagementService", DENTAL)]


# ---------------------------------------------------------------- allowed list and scope

def test_allowed_list_is_the_configured_account_by_default():
    assert accounts.allowed_ids() == {DEFAULT}


def test_allowed_list_adds_the_listed_accounts(monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", f" {DENTAL}, {PLUMBING} ")
    assert accounts.allowed_ids() == {DEFAULT, DENTAL, PLUMBING}


@pytest.mark.parametrize("bad", ["abc", "222000111;333000222", "-5", "１２３", "0", "1.5"])
def test_a_garbled_allowed_list_refuses_instead_of_guessing(monkeypatch, bad):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", bad)
    with pytest.raises(rails.RailViolation, match="MS_ADS_ACCOUNT_IDS"):
        accounts.allowed_ids()


def test_read_scope_defaults_to_list_and_accepts_all(monkeypatch):
    assert accounts.read_scope() == "list"
    monkeypatch.setenv("MS_ADS_READ_SCOPE", " ALL ")
    assert accounts.read_scope() == "all"


def test_an_unknown_read_scope_refuses(monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "manager")
    with pytest.raises(rails.RailViolation, match="MS_ADS_READ_SCOPE"):
        accounts.read_scope()


# ---------------------------------------------------------------- choosing the account

def test_no_account_named_means_the_configured_one(no_api):
    assert accounts.enter(None) is None


def test_naming_the_configured_account_needs_no_lookup(no_api):
    acct = accounts.enter(str(DEFAULT))
    assert (acct.id, acct.customer_id, acct.source) == (DEFAULT, 444555666, "explicit")


def test_an_unlisted_account_is_refused_without_asking_microsoft(no_api):
    with pytest.raises(rails.RailViolation, match="ACCOUNT_NOT_ALLOWED"):
        accounts.enter(str(DENTAL))


def test_a_listed_account_resolves_with_its_own_customer_id(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    acct = accounts.enter(str(DENTAL))
    assert (acct.id, acct.customer_id, acct.name, acct.currency) == (
        DENTAL, 900000001, "Northwind Dental", "USD")
    assert acct.source == "explicit"


def test_the_account_lookup_always_runs_under_the_configured_account(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", f"{DENTAL},{PLUMBING}")
    with client.use(client.Account(id=DENTAL, customer_id=900000001)):
        accounts.enter(str(PLUMBING))
    assert api.calls == [("SearchAccounts", DEFAULT, None)]


def test_account_number_works_as_well_as_the_id(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    assert accounts.enter("b002bbbb").id == DENTAL


def test_read_scope_all_reaches_any_account_the_user_can_see(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    assert accounts.enter(str(PLUMBING)).customer_id == 900000002


def test_an_account_the_user_cannot_see_is_not_found(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    with pytest.raises(rails.RailViolation, match="ACCOUNT_NOT_FOUND"):
        accounts.enter("999999999")


def test_a_listed_account_the_user_cannot_see_is_not_found(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", "999999999")
    with pytest.raises(rails.RailViolation, match="ACCOUNT_NOT_FOUND"):
        accounts.enter("999999999")


def test_a_name_is_not_accepted_where_an_account_must_be_exact(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    with pytest.raises(rails.RailViolation, match="find_accounts"):
        accounts.enter("Northwind Dental")


def test_the_account_list_is_fetched_once_and_reused(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    accounts.enter(str(DENTAL))
    accounts.enter(str(PLUMBING))
    assert api.searches == 1


def test_the_account_list_is_refetched_after_ten_minutes(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    now = [1000.0]
    monkeypatch.setattr(accounts.time, "monotonic", lambda: now[0])
    accounts.enter(str(DENTAL))
    now[0] += 601
    accounts.enter(str(DENTAL))
    assert api.searches == 2


# ---------------------------------------------------------------- find_accounts

def test_find_accounts_matches_part_of_a_name_under_scope_all(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    out = accounts.find_accounts("northwind")
    assert [(r["account_id"], r["name"], r["currency"], r["can_change"]) for r in out["accounts"]] == [
        (DENTAL, "Northwind Dental", "USD", True), (PLUMBING, "Northwind Plumbing", "CAD", False)]
    assert out["read_scope"] == "all"


def test_find_accounts_only_shows_the_allowed_list_under_scope_list(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    out = accounts.find_accounts("")
    assert [r["account_id"] for r in out["accounts"]] == [DEFAULT, DENTAL]


def test_find_accounts_matches_id_and_number(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    assert [r["account_id"] for r in accounts.find_accounts("c003")["accounts"]] == [PLUMBING]
    assert [r["account_id"] for r in accounts.find_accounts(str(DENTAL))["accounts"]] == [DENTAL]


def test_find_accounts_does_not_promise_changes_while_changes_are_off(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ENABLE_WRITES", "false")
    out = accounts.find_accounts("")
    assert [r["can_change"] for r in out["accounts"]] == [False]
    assert out["changes_enabled"] is False


# ---------------------------------------------------------------- the pick (reports only)

def test_pick_by_unique_name_then_reads_use_it(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    out = accounts.use_account("northwind dental")
    assert out["picked"]["account_id"] == DENTAL
    acct = accounts.enter(None)
    assert (acct.id, acct.customer_id, acct.source) == (DENTAL, 900000001, "pick")


def test_an_ambiguous_name_is_refused_and_lists_the_candidates(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    with pytest.raises(rails.RailViolation, match="Northwind Dental.*Northwind Plumbing"):
        accounts.use_account("northwind")
    assert accounts.enter(None) is None


def test_a_pick_outside_the_readable_accounts_is_refused(api):
    with pytest.raises(rails.RailViolation, match="ACCOUNT_NOT_ALLOWED"):
        accounts.use_account(str(DENTAL))


def test_clearing_the_pick_goes_back_to_the_configured_account(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    accounts.use_account(str(DENTAL))
    assert accounts.use_account(None)["picked"] is None
    assert accounts.enter(None) is None


def test_a_pick_stops_applying_when_the_scope_is_narrowed(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    accounts.use_account(str(DENTAL))
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "list")
    with pytest.raises(rails.RailViolation, match="ACCOUNT_NOT_ALLOWED"):
        accounts.enter(None)


def test_current_account_reports_pick_scope_and_change_list(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(PLUMBING))
    accounts.use_account(str(DENTAL))
    out = accounts.current_account()
    assert out["picked"]["account_id"] == DENTAL
    assert out["configured_account_id"] == DEFAULT
    assert out["read_scope"] == "all"
    assert out["change_account_ids"] == [DEFAULT, PLUMBING]
    assert out["changes_enabled"] is True


# ---------------------------------------------------------------- every tool takes an account

EXEMPT = {"health_check", "list_accounts", "get_account_info", "confirm_and_apply",
          "describe_report", "find_accounts", "use_account", "current_account"}


def _schemas():
    return {name: tool.parameters for name, tool in mcp._tool_manager._tools.items()}


def test_every_account_tool_accepts_an_account_and_the_exempt_ones_do_not():
    schemas = _schemas()
    assert EXEMPT <= set(schemas)
    missing = sorted(n for n, s in schemas.items()
                     if n not in EXEMPT and "account_id" not in s["properties"])
    assert missing == []
    extra = sorted(n for n in EXEMPT if "account_id" in schemas[n]["properties"])
    assert extra == []


def test_the_account_argument_is_optional_and_described():
    prop = _schemas()["get_entities"]["properties"]["account_id"]
    assert "account_id" not in _schemas()["get_entities"].get("required", [])
    assert "find_accounts" in prop["description"]


def test_a_read_runs_in_the_named_account_and_says_so(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    out = entities.get_entities("campaigns", account_id=str(DENTAL))
    assert ("GetCampaignsByAccountId", DENTAL, DENTAL) in api.calls
    assert out["account"] == {"account_id": DENTAL, "name": "Northwind Dental", "currency": "USD",
                              "chosen_by": "account_id argument"}
    assert client.active() is None


def test_a_read_with_no_account_is_unchanged(api):
    out = entities.get_entities("campaigns")
    assert api.calls == [("GetCampaignsByAccountId", DEFAULT, DEFAULT)]
    assert "account" not in out


def test_a_read_follows_the_pick(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    accounts.use_account(str(PLUMBING))
    out = entities.get_entities("campaigns")
    assert ("GetCampaignsByAccountId", PLUMBING, PLUMBING) in api.calls
    assert out["account"]["chosen_by"] == "use_account pick"


def test_the_account_is_cleared_after_a_tool_fails(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    with pytest.raises(ValueError, match="parent_id"):
        entities.get_entities("keywords", account_id=str(DENTAL))
    assert client.active() is None


def test_list_accounts_still_lists_everything_visible(api):
    assert [a["Id"] for a in infra.list_accounts()["accounts"]] == [DEFAULT, DENTAL, PLUMBING]


# ---------------------------------------------------------------- changes

def _noop():
    return {"ok": True}


def test_a_change_with_no_account_and_no_pick_goes_to_the_configured_account(no_api):
    out = rails.create_draft("pause_entity", {"x": 1}, _noop)
    assert "account" not in out  # single-account setups see exactly what they saw before


def test_a_change_under_a_pick_must_name_its_account(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    accounts.use_account(str(DENTAL))
    with pytest.raises(rails.RailViolation, match="ACCOUNT_REQUIRED"):
        status.pause_entity("campaign", 5)
    assert not any(op == "UpdateCampaigns" for op, _, _ in api.calls)
    assert rails._DRAFTS == {}


def test_a_change_to_a_readable_but_unlisted_account_is_refused(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    with pytest.raises(rails.RailViolation, match="ACCOUNT_NOT_ALLOWED"):
        status.pause_entity("campaign", 5, account_id=str(PLUMBING))
    assert rails._DRAFTS == {}


def test_a_change_names_its_account_and_limits_and_lands_there(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    draft = status.pause_entity("campaign", 5, account_id=str(DENTAL))
    assert draft["account"] == {"account_id": DENTAL, "name": "Northwind Dental", "currency": "USD",
                                "chosen_by": "account_id argument"}
    assert draft["limits"] == {"max_daily_budget": 1000, "max_cpc": 50,
                               "source": "environment defaults"}
    assert client.active() is None
    api.calls.clear()
    out = rails.apply_draft(draft["draft_id"])
    assert out["applied"] is True and out["account"]["account_id"] == DENTAL
    assert ("UpdateCampaigns", DENTAL, DENTAL) in api.calls
    assert {acct for _, acct, _ in api.calls} == {DENTAL}
    assert client.active() is None


def test_a_change_under_a_pick_works_once_it_names_the_account(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    accounts.use_account(str(PLUMBING))
    draft = status.pause_entity("campaign", 5, account_id=str(DENTAL))
    assert draft["account"]["account_id"] == DENTAL


def test_naming_the_configured_account_under_a_pick_is_allowed(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    accounts.use_account(str(PLUMBING))
    draft = status.pause_entity("campaign", 5, account_id=str(DEFAULT))
    assert draft["account"]["account_id"] == DEFAULT
    rails.apply_draft(draft["draft_id"])
    assert ("UpdateCampaigns", DEFAULT, DEFAULT) in api.calls


def test_the_configured_account_is_named_once_several_accounts_are_in_play(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    draft = rails.create_draft("pause_entity", {"x": 1}, _noop)
    assert draft["account"] == {"account_id": DEFAULT, "name": "Example Home Services",
                                "currency": "USD", "chosen_by": "configured account"}
    out = entities.get_entities("campaigns")
    assert out["account"] == draft["account"]


def test_a_draft_is_refused_but_kept_when_its_account_leaves_the_list(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    draft = status.pause_entity("campaign", 5, account_id=str(DENTAL))
    monkeypatch.delenv("MS_ADS_ACCOUNT_IDS")
    api.calls.clear()
    with pytest.raises(rails.RailViolation, match="ACCOUNT_NOT_ALLOWED"):
        rails.apply_draft(draft["draft_id"])
    assert api.calls == [] and draft["draft_id"] in rails._DRAFTS


def test_the_draft_check_runs_in_the_drafts_account(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    seen = []
    with client.use(accounts.enter(str(DENTAL))):
        draft = rails.create_draft("pause_entity", {"x": 1}, lambda: seen.append(client.account_id()),
                                   validate_fn=lambda: seen.append(client.account_id()))
    rails.apply_draft(draft["draft_id"])
    assert seen == [DENTAL, DENTAL]


def test_the_account_is_cleared_when_the_change_fails(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))

    def boom():
        raise RuntimeError("SOAP fault")

    with client.use(accounts.enter(str(DENTAL))):
        draft = rails.create_draft("pause_entity", {"x": 1}, boom)
    with pytest.raises(RuntimeError):
        rails.apply_draft(draft["draft_id"])
    assert client.active() is None


def test_the_audit_log_records_the_account(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    events = []
    monkeypatch.setattr(rails.audit, "log_event", lambda tool, phase, data: events.append((phase, data)))
    draft = status.pause_entity("campaign", 5, account_id=str(DENTAL))
    rails.apply_draft(draft["draft_id"])
    assert [(phase, data["account"]["account_id"]) for phase, data in events] == [
        ("draft", DENTAL), ("apply", DENTAL)]


# ---------------------------------------------------------------- per-account rules

def _rules(tmp_path, monkeypatch, text):
    path = tmp_path / "advertiser.yaml"
    path.write_text(text)
    monkeypatch.setenv(settings.ENV_VAR, str(path))
    settings._reset()
    return settings.load()


RULES = f"""
blocked_terms: [guaranteed]
advertiser_domain: example.com
keyword_research: {{location_id: 190, language: English}}
accounts:
  {DENTAL}:
    blocked_terms: [Painless]
    advertiser_domain: dental.example
    require_pmax_target_cpa: false
    keyword_research: {{location_id: 32}}
    max_daily_budget: 300
    max_cpc: 12.5
  "{PLUMBING}":
    blocked_terms: [sewer]
"""


def test_an_account_adds_its_banned_words_to_the_shared_ones(tmp_path, monkeypatch):
    _rules(tmp_path, monkeypatch, RULES)
    assert settings.blocked_terms() == ("guaranteed",)
    with client.use(client.Account(id=DENTAL, customer_id=1)):
        assert settings.blocked_terms() == ("guaranteed", "painless")
    with client.use(client.Account(id=PLUMBING, customer_id=1)):
        assert settings.blocked_terms() == ("guaranteed", "sewer")


def test_an_account_overrides_site_pmax_rule_and_research_defaults(tmp_path, monkeypatch):
    _rules(tmp_path, monkeypatch, RULES)
    assert settings.advertiser_domain() == "example.com"
    assert settings.require_pmax_target_cpa() is True
    with client.use(client.Account(id=DENTAL, customer_id=1)):
        assert settings.advertiser_domain() == "dental.example"
        assert settings.require_pmax_target_cpa() is False
        assert settings.keyword_research() == {
            "location_id": 32, "language": "English",
            "network": "OwnedAndOperatedAndSyndicatedSearch"}
    with client.use(client.Account(id=PLUMBING, customer_id=1)):
        assert settings.advertiser_domain() == "example.com"
        assert settings.require_pmax_target_cpa() is True


def test_an_accounts_own_spend_limits_replace_the_shared_ones(tmp_path, monkeypatch):
    _rules(tmp_path, monkeypatch, RULES)
    monkeypatch.setenv("MS_ADS_MAX_DAILY_BUDGET", "5000")
    assert (rails.max_daily_budget(), rails.max_cpc()) == (5000, 50)
    with client.use(client.Account(id=DENTAL, customer_id=1)):
        assert (rails.max_daily_budget(), rails.max_cpc()) == (300, 12.5)
        rails.check_budget(300)
        with pytest.raises(rails.RailViolation, match=rf"cap 300 \(advertiser.yaml accounts section for {DENTAL}\)"):
            rails.check_budget(301)
        with pytest.raises(rails.RailViolation, match=r"cap 12.5 \(advertiser.yaml accounts"):
            rails.check_bid(13)
    with client.use(client.Account(id=PLUMBING, customer_id=1)):
        assert (rails.max_daily_budget(), rails.max_cpc()) == (5000, 50)
        with pytest.raises(rails.RailViolation, match=r"cap 5000.0 \(MS_ADS_MAX_DAILY_BUDGET\)"):
            rails.check_budget(5001)


def test_banned_words_are_enforced_per_account(tmp_path, monkeypatch):
    _rules(tmp_path, monkeypatch, RULES)
    rails.check_content(["Painless whitening"])
    with client.use(client.Account(id=DENTAL, customer_id=1)):
        with pytest.raises(rails.RailViolation, match="blocked term 'painless'"):
            rails.check_content(["Painless whitening"])


def test_the_draft_shows_the_accounts_own_limits(api, tmp_path, monkeypatch):
    _rules(tmp_path, monkeypatch, RULES)
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    draft = status.pause_entity("campaign", 5, account_id=str(DENTAL))
    assert draft["limits"] == {"max_daily_budget": 300, "max_cpc": 12.5,
                               "source": f"advertiser.yaml accounts section for {DENTAL}"}


@pytest.mark.parametrize("body,message", [
    ("accounts: [1]", "'accounts' must be a mapping"),
    ("accounts: {abc: {}}", "must be a Microsoft Ads account id"),
    ("accounts: {true: {}}", "must be a Microsoft Ads account id"),
    ("accounts: {222000111: [x]}", "must be a mapping"),
    ("accounts: {222000111: {colour: red}}", "unknown key"),
    ("accounts: {222000111: {accounts: {}}}", "unknown key"),
    ("accounts: {222000111: {max_cpc: 5}}", "max_daily_budget and max_cpc together"),
    ("accounts: {222000111: {max_daily_budget: 5}}", "max_daily_budget and max_cpc together"),
    ("accounts: {222000111: {max_daily_budget: 0, max_cpc: 5}}", "must be a positive number"),
    ("accounts: {222000111: {max_daily_budget: true, max_cpc: 5}}", "must be a positive number"),
    ("accounts: {222000111: {max_daily_budget: .inf, max_cpc: 5}}", "must be a positive number"),
    ("accounts: {222000111: {blocked_terms: nope}}", "'blocked_terms' must be a list"),
    ("accounts: {222000111: {}, '222000111': {}}", "listed more than once"),
    ("max_cpc: 5", "unknown key"),
])
def test_a_bad_accounts_section_stops_startup_with_a_named_reason(tmp_path, monkeypatch, body, message):
    with pytest.raises(ValueError, match=message):
        _rules(tmp_path, monkeypatch, body)


def test_rules_without_an_accounts_section_never_ask_which_account(tmp_path, monkeypatch):
    _rules(tmp_path, monkeypatch, "blocked_terms: [guaranteed]")

    def boom():
        raise AssertionError("must not need the account")

    monkeypatch.setattr(client, "account_id", boom)
    assert settings.blocked_terms() == ("guaranteed",)
    assert (rails.max_daily_budget(), rails.max_cpc()) == (1000, 50)


def test_a_change_never_rereads_the_pick_while_it_lands(api, monkeypatch):
    """A draft for the configured account, confirmed after another account was picked:
    a tool called from inside the apply step must stay in the draft's account."""
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    draft = rails.create_draft("pause_entity", {"x": 1}, lambda: entities.get_entities("campaigns"))
    accounts.use_account(str(PLUMBING))
    api.calls.clear()
    rails.apply_draft(draft["draft_id"])
    assert api.calls == [("GetCampaignsByAccountId", DEFAULT, DEFAULT)]


def test_a_tool_calling_another_tool_stays_in_the_named_account(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    with client.use(accounts.enter(str(DENTAL))):
        entities.get_entities("campaigns")
    assert api.calls[-1] == ("GetCampaignsByAccountId", DENTAL, DENTAL)


@pytest.fixture(autouse=True)
def _no_leftover_drafts():
    rails._DRAFTS.clear()
    yield
    rails._DRAFTS.clear()


def test_a_name_only_matches_accounts_this_setup_may_look_at(api, monkeypatch):
    """Scope `list`: 'northwind' would match two accounts, but only one is allowed, so
    the other is never offered (or named in an error)."""
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    assert accounts.use_account("northwind")["picked"]["account_id"] == DENTAL


def test_the_configured_account_is_named_when_the_list_is_already_in_hand(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_READ_SCOPE", "all")
    out = accounts.use_account("example home")
    assert out["picked"] == {"account_id": DEFAULT, "name": "Example Home Services",
                             "currency": "USD", "chosen_by": "use_account pick"}
    assert api.searches == 1


# ---------------------------------------------------------------- review fixes

@pytest.mark.parametrize("name,value", [("MS_ADS_ACCOUNT_IDS", "12x,abc"),
                                        ("MS_ADS_READ_SCOPE", "everything")])
def test_a_garbled_setting_refuses_even_a_look_up_in_the_configured_account(api, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(rails.RailViolation, match=name):
        entities.get_entities("campaigns")
    assert api.calls == []


def test_shared_limits_do_not_cover_an_account_in_another_currency(api, monkeypatch):
    """Plumbing is in CAD; the shared limits are numbers written for a USD account."""
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(PLUMBING))
    with pytest.raises(rails.RailViolation, match="LIMITS_REQUIRED.*CAD.*USD"):
        status.pause_entity("campaign", 5, account_id=str(PLUMBING))
    assert rails._DRAFTS == {}


def test_an_account_in_another_currency_works_with_its_own_limits(api, tmp_path, monkeypatch):
    _rules(tmp_path, monkeypatch, f"accounts: {{{PLUMBING}: {{max_daily_budget: 400, max_cpc: 9}}}}")
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(PLUMBING))
    draft = status.pause_entity("campaign", 5, account_id=str(PLUMBING))
    assert draft["limits"]["max_daily_budget"] == 400 and draft["account"]["currency"] == "CAD"


def test_a_draft_is_refused_but_kept_when_its_own_limits_are_removed(api, tmp_path, monkeypatch):
    _rules(tmp_path, monkeypatch, f"accounts: {{{PLUMBING}: {{max_daily_budget: 400, max_cpc: 9}}}}")
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(PLUMBING))
    draft = status.pause_entity("campaign", 5, account_id=str(PLUMBING))
    _rules(tmp_path, monkeypatch, "blocked_terms: []")
    api.calls.clear()
    with pytest.raises(rails.RailViolation, match="LIMITS_REQUIRED"):
        rails.apply_draft(draft["draft_id"])
    assert api.calls == [] and draft["draft_id"] in rails._DRAFTS


def test_an_account_whose_currency_is_unknown_needs_its_own_limits(api, monkeypatch):
    monkeypatch.setenv("MS_ADS_ACCOUNT_IDS", str(DENTAL))
    monkeypatch.setattr(ROWS[1], "CurrencyCode", None)
    with pytest.raises(rails.RailViolation, match="LIMITS_REQUIRED"):
        status.pause_entity("campaign", 5, account_id=str(DENTAL))


def _tags(n):
    return NS(GetUetTagsByIds=lambda TagIds: NS(UetTags=NS(UetTag=[NS(Id=i + 1) for i in range(n)])))


def test_a_choice_between_tracking_tags_is_refused_outside_the_configured_account():
    from mcp_microsoft_ads.tools.conversions import uet_tag_id
    assert uet_tag_id(_tags(2)) == 1  # configured account: unchanged, first tag
    with client.use(client.Account(id=DENTAL, customer_id=900000001)):
        assert uet_tag_id(_tags(1)) == 1
        with pytest.raises(rails.RailViolation, match="TAG_AMBIGUOUS"):
            uet_tag_id(_tags(2))
