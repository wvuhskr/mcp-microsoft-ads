"""Regression coverage for mcp_microsoft_ads/reauth.py (moved from root-level reauth.py,
PLAN.md Section B item 7). No network calls: _post_token is monkeypatched everywhere it
would otherwise call urlopen.
"""
from mcp_microsoft_ads import reauth

FAKE_CREDS = {
    "client_id": "cid-guid",
    "client_secret": "sekret",
    "refresh_token": "OLDTOKEN",
    "tenant": "common",
}


# ---------------------------------------------------------------------------
# extract_code
# ---------------------------------------------------------------------------

def test_extract_code_full_redirect_url():
    assert reauth.extract_code("http://localhost/?code=ABC123&state=x") == "ABC123"


def test_extract_code_param_order_variant():
    # code appears after state in the query string -- must not depend on position.
    assert reauth.extract_code("http://localhost/?state=x&code=A.B-C_D0") == "A.B-C_D0"


def test_extract_code_bare_code_rejected():
    # Bare codes (no full redirect URL, so no state to verify) are no longer
    # accepted -- discriminating: pre-fix this returned "ABC123".
    assert reauth.extract_code("  ABC123  ") is None


def test_extract_code_empty_string():
    assert reauth.extract_code("") is None


def test_extract_code_none():
    assert reauth.extract_code(None) is None


# ---------------------------------------------------------------------------
# extract_code_and_state
# ---------------------------------------------------------------------------

def test_extract_code_and_state_full_url():
    assert reauth.extract_code_and_state("http://localhost/?code=ABC123&state=st1") == ("ABC123", "st1")


def test_extract_code_and_state_url_missing_state():
    assert reauth.extract_code_and_state("http://localhost/?code=ABC123") == ("ABC123", None)


def test_extract_code_and_state_bare_code_rejected():
    # No "code=" marker -> no full URL -> nothing to verify a state against.
    # Discriminating: pre-fix this returned ("ABC123", None).
    assert reauth.extract_code_and_state("  ABC123  ") == (None, None)


def test_extract_code_and_state_empty():
    assert reauth.extract_code_and_state("") == (None, None)


# ---------------------------------------------------------------------------
# authorize_url
# ---------------------------------------------------------------------------

def test_authorize_url_explicit_tenant_prefix():
    url = reauth.authorize_url("cid", "mytenant", "st")
    assert url.startswith("https://login.microsoftonline.com/mytenant/oauth2/v2.0/authorize?")


def test_authorize_url_prompt_login_present():
    assert "prompt=login" in reauth.authorize_url("cid", "common", "st")


def test_authorize_url_tenant_none_uses_common():
    url = reauth.authorize_url("cid", None, "st")
    assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")


# ---------------------------------------------------------------------------
# _post_token error path (fake urlopen, no network)
# ---------------------------------------------------------------------------

class _FakeHTTPResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_post_token_http_error_with_json_body(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=30):
        raise urllib.error.HTTPError(
            "url", 400, "Bad Request", {},
            fp=_FakeHTTPResponse(b'{"error": "invalid_grant", "error_description": "bad code"}'),
        )

    monkeypatch.setattr(reauth.urllib.request, "urlopen", fake_urlopen)
    result = reauth._post_token("common", {"grant_type": "authorization_code"})
    assert result == {"error": "invalid_grant", "error_description": "bad code"}


def test_post_token_http_error_non_json_body_falls_back(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=30):
        raise urllib.error.HTTPError(
            "url", 503, "Service Unavailable", {}, fp=_FakeHTTPResponse(b"<html>nope</html>"),
        )

    monkeypatch.setattr(reauth.urllib.request, "urlopen", fake_urlopen)
    result = reauth._post_token("common", {"grant_type": "authorization_code"})
    assert result == {"error": "http_503"}


# ---------------------------------------------------------------------------
# main() flow with fakes
# ---------------------------------------------------------------------------

def test_main_url_file_happy_path(monkeypatch, tmp_path):
    url_file = tmp_path / "redirect.txt"
    url_file.write_text("http://localhost/?code=AUTHCODE&state=x")

    monkeypatch.setattr(reauth, "load_creds", lambda: dict(FAKE_CREDS))
    monkeypatch.setattr(reauth, "creds_path", lambda: "/fake/path/creds.yaml")
    # main() generates state via secrets.token_hex(8); pin it to match the fixture's
    # state=x so the new verification step doesn't reject this happy-path redirect.
    monkeypatch.setattr(reauth.secrets, "token_hex", lambda n: "x")

    def fake_post_token(tenant, data):
        if data["grant_type"] == "authorization_code":
            return {"refresh_token": "MID_ROTATE_TOKEN"}
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "MID_ROTATE_TOKEN"
        # MS rotates the refresh token again on the verify refresh -- this is the one
        # that must actually get persisted, not the authorization_code grant's token.
        return {"access_token": "AT123", "refresh_token": "FINAL_TOKEN"}

    monkeypatch.setattr(reauth, "_post_token", fake_post_token)

    persisted = {}

    def fake_persist(old, new, path):
        persisted["old"], persisted["new"], persisted["path"] = old, new, path
        return True

    monkeypatch.setattr(reauth, "persist_rotated_token", fake_persist)

    import sys
    monkeypatch.setattr(sys, "argv", ["reauth.py", "--url-file", str(url_file)])
    rc = reauth.main()

    assert rc == 0
    assert persisted == {"old": "OLDTOKEN", "new": "FINAL_TOKEN", "path": "/fake/path/creds.yaml"}


def test_main_no_code_found_returns_2(monkeypatch, tmp_path):
    url_file = tmp_path / "redirect.txt"
    url_file.write_text("")  # nothing pasted -- no code, no bare-code fallback either

    monkeypatch.setattr(reauth, "load_creds", lambda: dict(FAKE_CREDS))

    def fail_post_token(*a, **k):
        raise AssertionError("_post_token must not be called when no code was found")

    monkeypatch.setattr(reauth, "_post_token", fail_post_token)

    import sys
    monkeypatch.setattr(sys, "argv", ["reauth.py", "--url-file", str(url_file)])
    rc = reauth.main()

    assert rc == 2


def test_main_state_mismatch_returns_2_no_token_exchange(monkeypatch, tmp_path):
    # Redirect carries a state that does not match the one main() generated -- possible
    # tampering or a stale/reused URL. Must reject before any token exchange.
    url_file = tmp_path / "redirect.txt"
    url_file.write_text("http://localhost/?code=AUTHCODE&state=WRONG")

    monkeypatch.setattr(reauth, "load_creds", lambda: dict(FAKE_CREDS))
    monkeypatch.setattr(reauth.secrets, "token_hex", lambda n: "RIGHT")

    def fail_post_token(*a, **k):
        raise AssertionError("_post_token must not be called on state mismatch")

    monkeypatch.setattr(reauth, "_post_token", fail_post_token)

    import sys
    monkeypatch.setattr(sys, "argv", ["reauth.py", "--url-file", str(url_file)])
    rc = reauth.main()

    assert rc == 2


def test_main_full_url_missing_state_returns_2(monkeypatch, tmp_path):
    # A full redirect URL with no state param at all is unverifiable -- reject it too,
    # distinct from the bare-code case where there was never a state to check.
    url_file = tmp_path / "redirect.txt"
    url_file.write_text("http://localhost/?code=AUTHCODE")

    monkeypatch.setattr(reauth, "load_creds", lambda: dict(FAKE_CREDS))
    monkeypatch.setattr(reauth.secrets, "token_hex", lambda n: "RIGHT")

    def fail_post_token(*a, **k):
        raise AssertionError("_post_token must not be called when state is missing from a full URL")

    monkeypatch.setattr(reauth, "_post_token", fail_post_token)

    import sys
    monkeypatch.setattr(sys, "argv", ["reauth.py", "--url-file", str(url_file)])
    rc = reauth.main()

    assert rc == 2


def test_main_bare_code_rejected_no_state_verification_bypass(monkeypatch, tmp_path):
    # A bare pasted code (no full redirect URL) used to skip state verification
    # entirely -- that was the bypass. Discriminating: pre-fix this returned 0 and
    # called _post_token; post-fix it must be rejected before any token exchange.
    url_file = tmp_path / "redirect.txt"
    url_file.write_text("AUTHCODE")

    monkeypatch.setattr(reauth, "load_creds", lambda: dict(FAKE_CREDS))
    monkeypatch.setattr(reauth, "creds_path", lambda: "/fake/path/creds.yaml")
    monkeypatch.setattr(reauth.secrets, "token_hex", lambda n: "RIGHT")

    def fail_post_token(*a, **k):
        raise AssertionError("_post_token must not be called for a bare-code (no state) input")

    monkeypatch.setattr(reauth, "_post_token", fail_post_token)
    monkeypatch.setattr(reauth, "persist_rotated_token", lambda old, new, path: True)

    import sys
    monkeypatch.setattr(sys, "argv", ["reauth.py", "--url-file", str(url_file)])
    rc = reauth.main()

    assert rc == 2


def test_selftest_flag_still_works(monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "argv", ["reauth.py", "--selftest"])
    assert reauth.main() == 0
    out = capsys.readouterr().out
    assert "selftest ok" in out


# ---------------------------------------------------------------------------
# main() first-run bootstrap (B3)
# ---------------------------------------------------------------------------

FAKE_STATIC_CREDS = {k: v for k, v in FAKE_CREDS.items() if k != "refresh_token"}


def test_main_bootstrap_happy_path_calls_initial_writer_not_persist(monkeypatch, tmp_path):
    """No creds file at all; statics arrive via env (as load_static_creds would
    read them). load_creds fails (no refresh_token anywhere) -> bootstrap mode ->
    the flow runs -> the FINAL (post-verify) token is written via the initial
    writer, and persist_rotated_token is never touched."""
    url_file = tmp_path / "redirect.txt"
    url_file.write_text("http://localhost/?code=AUTHCODE&state=x")

    monkeypatch.setattr(reauth, "load_creds",
                         lambda: (_ for _ in ()).throw(reauth.CredsError("no creds file")))
    monkeypatch.setattr(reauth, "load_static_creds", lambda: dict(FAKE_STATIC_CREDS))
    monkeypatch.setattr(reauth, "creds_path", lambda: "/fake/path/creds.yaml")
    monkeypatch.setattr(reauth.secrets, "token_hex", lambda n: "x")

    def fake_post_token(tenant, data):
        if data["grant_type"] == "authorization_code":
            return {"refresh_token": "MID_ROTATE_TOKEN"}
        assert data["grant_type"] == "refresh_token"
        return {"access_token": "AT123", "refresh_token": "FINAL_TOKEN"}

    monkeypatch.setattr(reauth, "_post_token", fake_post_token)

    persist_calls = []
    monkeypatch.setattr(reauth, "persist_rotated_token",
                         lambda *a, **k: persist_calls.append(a) or True)

    written = {}

    def fake_write_initial(refresh_token, path):
        written["refresh_token"], written["path"] = refresh_token, path

    monkeypatch.setattr(reauth, "write_initial_refresh_token", fake_write_initial)

    import sys
    monkeypatch.setattr(sys, "argv", ["reauth.py", "--url-file", str(url_file)])
    rc = reauth.main()

    assert rc == 0
    assert written == {"refresh_token": "FINAL_TOKEN", "path": "/fake/path/creds.yaml"}
    assert persist_calls == []


def test_main_bootstrap_prints_notice(monkeypatch, tmp_path, capsys):
    url_file = tmp_path / "redirect.txt"
    url_file.write_text("http://localhost/?code=AUTHCODE&state=x")

    monkeypatch.setattr(reauth, "load_creds",
                         lambda: (_ for _ in ()).throw(reauth.CredsError("no creds file")))
    monkeypatch.setattr(reauth, "load_static_creds", lambda: dict(FAKE_STATIC_CREDS))
    monkeypatch.setattr(reauth, "creds_path", lambda: "/fake/path/creds.yaml")
    monkeypatch.setattr(reauth.secrets, "token_hex", lambda n: "x")
    monkeypatch.setattr(reauth, "_post_token", lambda tenant, data: (
        {"refresh_token": "MID"} if data["grant_type"] == "authorization_code"
        else {"access_token": "AT", "refresh_token": "FINAL"}
    ))
    monkeypatch.setattr(reauth, "write_initial_refresh_token", lambda rt, path: None)

    import sys
    monkeypatch.setattr(sys, "argv", ["reauth.py", "--url-file", str(url_file)])
    rc = reauth.main()

    assert rc == 0
    assert "first-run bootstrap" in capsys.readouterr().out


def test_main_no_creds_anywhere_returns_2(monkeypatch, tmp_path, capsys):
    """Neither load_creds nor load_static_creds can produce anything -- print the
    static error and bail before any network call."""
    url_file = tmp_path / "redirect.txt"
    url_file.write_text("http://localhost/?code=AUTHCODE&state=x")

    monkeypatch.setattr(reauth, "load_creds",
                         lambda: (_ for _ in ()).throw(reauth.CredsError("no creds file")))
    monkeypatch.setattr(reauth, "load_static_creds",
                         lambda: (_ for _ in ()).throw(reauth.CredsError("missing MS_ADS_CLIENT_ID")))

    def fail_post_token(*a, **k):
        raise AssertionError("_post_token must not be called with no usable creds")

    monkeypatch.setattr(reauth, "_post_token", fail_post_token)

    import sys
    monkeypatch.setattr(sys, "argv", ["reauth.py", "--url-file", str(url_file)])
    rc = reauth.main()

    assert rc == 2
    assert "missing MS_ADS_CLIENT_ID" in capsys.readouterr().err
