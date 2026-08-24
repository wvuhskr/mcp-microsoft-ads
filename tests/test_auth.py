import os
import stat

import pytest
import yaml

from mcp_microsoft_ads import auth

VALID = """\
developer_token: "DEVTOK"
client_id: "cid-guid"
client_secret: "sekret"
refresh_token: "OLDTOKEN"
customer_id: "444555666"
account_id: "111222333"
tenant: "common"
"""

VALID_WITH_SCOPE = VALID + 'scope: "https://ads.microsoft.com/msads.manage offline_access"\n'

@pytest.fixture
def creds_file(tmp_path):
    p = tmp_path / "microsoft-ads.yaml"
    p.write_text(VALID)
    os.chmod(p, 0o600)
    return str(p)

@pytest.fixture(autouse=True)
def _no_ms_ads_env(monkeypatch):
    """Env wins over the file, so a real MS_ADS_* export in the dev shell would
    silently mask every file-reading assertion below."""
    for field in auth.ENV_CREDS:
        monkeypatch.delenv(auth.env_var_for(field), raising=False)


def test_load_creds_ok(creds_file):
    c = auth.load_creds(creds_file)
    assert c["refresh_token"] == "OLDTOKEN"
    assert c["account_id"] == "111222333"

def test_load_creds_missing_field(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("developer_token: x\n")
    with pytest.raises(auth.CredsError, match="missing"):
        auth.load_creds(str(p))

def test_env_overrides_file_for_static_creds(creds_file, monkeypatch, capsys):
    monkeypatch.setenv("MS_ADS_CLIENT_SECRET", "from-env")
    c = auth.load_creds(creds_file)
    assert c["client_secret"] == "from-env"       # env wins
    assert c["developer_token"] == "DEVTOK"       # unset field still falls back
    # the fallback has to be visible, and on stderr — stdout is the JSON-RPC channel
    err = capsys.readouterr().err
    assert "MS_ADS_DEVELOPER_TOKEN" in err and "MS_ADS_CLIENT_SECRET" not in err

def test_all_static_from_env_needs_only_refresh_token_in_file(tmp_path, monkeypatch, capsys):
    p = tmp_path / "only-refresh.yaml"
    p.write_text('refresh_token: "OLDTOKEN"\n')
    for field in auth.ENV_CREDS:
        monkeypatch.setenv(auth.env_var_for(field), f"env-{field}")
    c = auth.load_creds(str(p))
    assert c["refresh_token"] == "OLDTOKEN"
    assert c["client_id"] == "env-client_id"
    assert capsys.readouterr().err == ""          # fully migrated = silent

def test_refresh_token_is_never_read_from_env(tmp_path, monkeypatch):
    """It rotates; a value from the environment could not be persisted, so an
    env-only refresh_token would authenticate once and then die."""
    p = tmp_path / "no-refresh.yaml"
    p.write_text('developer_token: "x"\n')
    for field in auth.ENV_CREDS:
        monkeypatch.setenv(auth.env_var_for(field), f"env-{field}")
    monkeypatch.setenv("MS_ADS_REFRESH_TOKEN", "should-be-ignored")
    with pytest.raises(auth.CredsError, match="refresh_token"):
        auth.load_creds(str(p))

def test_load_creds_blank_refresh_token_treated_as_missing(tmp_path):
    """Discriminating: pre-fix, `"refresh_token" in file_creds` is True for a blank
    value, so load_creds treats it as present -- the reauth path then calls
    persist_rotated_token(old="", ...), which corrupts the file (see
    test_persist_rotated_token_refuses_blank_old). Post-fix, blank must be absent."""
    p = tmp_path / "blank-rt.yaml"
    p.write_text(VALID.replace('refresh_token: "OLDTOKEN"', 'refresh_token: ""'))
    with pytest.raises(auth.CredsError, match="missing"):
        auth.load_creds(str(p))


def test_load_creds_whitespace_refresh_token_treated_as_missing(tmp_path):
    p = tmp_path / "ws-rt.yaml"
    p.write_text(VALID.replace('refresh_token: "OLDTOKEN"', 'refresh_token: "   "'))
    with pytest.raises(auth.CredsError, match="missing"):
        auth.load_creds(str(p))


def test_load_creds_non_string_refresh_token_treated_as_missing(tmp_path):
    p = tmp_path / "int-rt.yaml"
    p.write_text(VALID.replace('refresh_token: "OLDTOKEN"', "refresh_token: 12345"))
    with pytest.raises(auth.CredsError, match="missing"):
        auth.load_creds(str(p))


def test_load_static_creds_blank_refresh_token_is_absent_not_bootstrap_blocker(creds_file):
    """A blank refresh_token in an otherwise-complete file must be BOOTSTRAPPABLE
    (write_initial_refresh_token's job), not treated as an already-present token."""
    p = creds_file
    text = open(p).read().replace('refresh_token: "OLDTOKEN"', 'refresh_token: ""')
    open(p, "w").write(text)
    c = auth.load_static_creds(p)
    assert "refresh_token" not in c


def test_load_creds_missing_file(tmp_path):
    with pytest.raises(auth.CredsError, match="not found"):
        auth.load_creds(str(tmp_path / "nope.yaml"))

def test_load_creds_malformed_yaml(tmp_path):
    p = tmp_path / "malformed.yaml"
    p.write_text("key: [unclosed")
    with pytest.raises(auth.CredsError):
        auth.load_creds(str(p))

def test_persist_rotates_atomically(creds_file):
    assert auth.persist_rotated_token("OLDTOKEN", "NEWTOKEN", creds_file) is True
    raw = open(creds_file).read()
    assert "NEWTOKEN" in raw and "OLDTOKEN" not in raw
    assert auth.load_creds(creds_file)["client_secret"] == "sekret"  # rest intact
    assert stat.S_IMODE(os.stat(creds_file).st_mode) == 0o600
    assert open(creds_file + ".bak").read() == VALID  # backup = pre-rotation

def test_persist_noop_when_same(creds_file):
    assert auth.persist_rotated_token("OLDTOKEN", "OLDTOKEN", creds_file) is False
    assert auth.persist_rotated_token("OLDTOKEN", None, creds_file) is False
    assert "OLDTOKEN" in open(creds_file).read()

def test_persist_refuses_when_old_absent(creds_file):
    with pytest.raises(auth.CredsError, match="refusing"):
        auth.persist_rotated_token("NOT-IN-FILE", "NEW", creds_file)
    assert open(creds_file).read() == VALID  # untouched

def test_persist_rotated_token_refuses_blank_old(creds_file):
    """Discriminating for the blank-refresh_token-destroys-the-file defect: pre-fix,
    persist_rotated_token("", "NEW", path) does `"" in raw` (always True) then
    `raw.replace("", new)`, which interleaves NEW between every character of the
    file -- a catastrophic corruption, not just a wrong value. Post-fix this must
    raise before touching the file at all."""
    with pytest.raises(auth.CredsError, match="blank"):
        auth.persist_rotated_token("", "NEW", creds_file)
    assert open(creds_file).read() == VALID  # untouched, not interleaved-corrupted


def test_persist_rotated_token_refuses_whitespace_old(creds_file):
    with pytest.raises(auth.CredsError, match="blank"):
        auth.persist_rotated_token("   ", "NEW", creds_file)
    assert open(creds_file).read() == VALID


def test_persist_rotated_token_refuses_non_string_old(creds_file):
    with pytest.raises(auth.CredsError, match="blank"):
        auth.persist_rotated_token(None, "NEW", creds_file)
    assert open(creds_file).read() == VALID


def test_persist_bak_is_0600_even_if_source_is_not(creds_file):
    """shutil.copy2 inherits the source mode; a laxly-permissioned real creds file
    must not produce a laxly-permissioned backup."""
    os.chmod(creds_file, 0o644)
    auth.persist_rotated_token("OLDTOKEN", "NEWTOKEN", creds_file)
    assert stat.S_IMODE(os.stat(creds_file + ".bak").st_mode) == 0o600

def test_creds_path_default_no_env(monkeypatch):
    monkeypatch.delenv("MS_ADS_CREDENTIALS_PATH", raising=False)
    assert auth.creds_path().endswith(".mcp-microsoft-ads/credentials.yaml")

def test_creds_path_env_wins(monkeypatch, tmp_path):
    p = tmp_path / "somewhere.yaml"
    monkeypatch.setenv("MS_ADS_CREDENTIALS_PATH", str(p))
    assert auth.creds_path() == str(p)

def test_env_var_selects_creds_path_for_load_creds(creds_file, monkeypatch):
    monkeypatch.setenv("MS_ADS_CREDENTIALS_PATH", creds_file)
    c = auth.load_creds()  # no explicit path -> falls through to env var
    assert c["refresh_token"] == "OLDTOKEN"

def test_explicit_path_beats_env_for_load_creds(creds_file, monkeypatch, tmp_path):
    wrong = tmp_path / "wrong.yaml"
    wrong.write_text("developer_token: x\n")
    monkeypatch.setenv("MS_ADS_CREDENTIALS_PATH", str(wrong))
    c = auth.load_creds(creds_file)  # explicit arg wins over env
    assert c["refresh_token"] == "OLDTOKEN"

def test_explicit_path_beats_env_for_persist_rotated_token(creds_file, monkeypatch, tmp_path):
    wrong = tmp_path / "wrong.yaml"
    wrong.write_text(VALID)
    monkeypatch.setenv("MS_ADS_CREDENTIALS_PATH", str(wrong))
    auth.persist_rotated_token("OLDTOKEN", "NEWTOKEN", creds_file)
    assert "NEWTOKEN" in open(creds_file).read()
    assert "NEWTOKEN" not in open(wrong).read()


# ---------------------------------------------------------------------------
# load_static_creds (B3 bootstrap)
# ---------------------------------------------------------------------------

STATIC_FIELDS = sorted(auth.ENV_CREDS)


def test_load_static_creds_all_env_no_file(tmp_path, monkeypatch):
    for field in auth.ENV_CREDS:
        monkeypatch.setenv(auth.env_var_for(field), f"env-{field}")
    c = auth.load_static_creds(str(tmp_path / "nope.yaml"))  # missing file is fine
    assert c["client_id"] == "env-client_id"
    assert "refresh_token" not in c  # nowhere to get it from


def test_load_static_creds_missing_file_and_missing_statics_raises(tmp_path, monkeypatch):
    with pytest.raises(auth.CredsError, match="missing"):
        auth.load_static_creds(str(tmp_path / "nope.yaml"))


def test_load_static_creds_names_only_missing_statics(tmp_path):
    # Even if the (nonexistent) file could have carried refresh_token, this error
    # must never demand it -- load_static_creds doesn't care about refresh_token.
    with pytest.raises(auth.CredsError) as exc:
        auth.load_static_creds(str(tmp_path / "nope.yaml"))
    assert "refresh_token" not in str(exc.value)


def test_load_static_creds_file_fallback_works(creds_file):
    c = auth.load_static_creds(creds_file)
    assert c["client_id"] == "cid-guid"
    assert c["refresh_token"] == "OLDTOKEN"  # rides along when present


def test_load_static_creds_refresh_token_absent_when_file_lacks_it(tmp_path, monkeypatch):
    p = tmp_path / "no-refresh.yaml"
    p.write_text("\n".join(f'{f}: "v-{f}"' for f in auth.ENV_CREDS) + "\n")
    c = auth.load_static_creds(str(p))
    assert "refresh_token" not in c


def test_load_static_creds_invalid_existing_file_still_raises(tmp_path):
    p = tmp_path / "malformed.yaml"
    p.write_text("key: [unclosed")
    with pytest.raises(auth.CredsError):
        auth.load_static_creds(str(p))


# ---------------------------------------------------------------------------
# write_initial_refresh_token (B3 bootstrap)
# ---------------------------------------------------------------------------

def test_write_initial_refresh_token_creates_file_and_parent_dir(tmp_path):
    path = str(tmp_path / "sub" / "dir" / "credentials.yaml")
    auth.write_initial_refresh_token("NEWRT", path)
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert yaml.safe_load(open(path)) == {"refresh_token": "NEWRT"}


def test_write_initial_refresh_token_merges_into_existing_static_file(tmp_path):
    p = tmp_path / "static.yaml"
    p.write_text('client_id: "cid-guid"\ndeveloper_token: "DEVTOK"\n')
    os.chmod(p, 0o644)
    auth.write_initial_refresh_token("NEWRT", str(p))
    data = yaml.safe_load(open(p))
    assert data == {"client_id": "cid-guid", "developer_token": "DEVTOK", "refresh_token": "NEWRT"}
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    bak = yaml.safe_load(open(str(p) + ".bak"))
    assert bak == {"client_id": "cid-guid", "developer_token": "DEVTOK"}
    assert stat.S_IMODE(os.stat(str(p) + ".bak").st_mode) == 0o600


def test_write_initial_refresh_token_refuses_when_token_already_present(creds_file):
    with pytest.raises(auth.CredsError, match="already has a refresh_token"):
        auth.write_initial_refresh_token("NEWRT", creds_file)
    # untouched -- no .bak, original content intact
    assert open(creds_file).read() == VALID
    assert not os.path.exists(creds_file + ".bak")


def test_write_initial_refresh_token_overwrites_blank_existing_token(tmp_path):
    """A blank refresh_token counts as absent (same rule as load_creds) -- this is
    the first-run repair path, so it must overwrite rather than refuse."""
    p = tmp_path / "blank-rt.yaml"
    p.write_text(VALID.replace('refresh_token: "OLDTOKEN"', 'refresh_token: ""'))
    auth.write_initial_refresh_token("NEWRT", str(p))
    data = yaml.safe_load(open(p))
    assert data["refresh_token"] == "NEWRT"


def test_write_initial_refresh_token_overwrites_non_string_token(tmp_path):
    """Same shared blank-rule as the loading layer: a non-string value (e.g. a
    stray int) counts as ABSENT too, not "already has a refresh_token" -- so this
    overwrites rather than refusing."""
    p = tmp_path / "int-rt.yaml"
    p.write_text(VALID.replace('refresh_token: "OLDTOKEN"', "refresh_token: 12345"))
    auth.write_initial_refresh_token("NEWRT", str(p))
    data = yaml.safe_load(open(p))
    assert data["refresh_token"] == "NEWRT"


# ---------------------------------------------------------------------------
# B4: scope removed from schema; tenant validated + passed into the SDK grant
# ---------------------------------------------------------------------------

def test_creds_complete_without_scope_field(creds_file):
    """Discriminating: VALID no longer has a scope key at all. Pre-B4 code
    requires "scope" in REQUIRED/ENV_CREDS and raises 'missing: [...scope...]'."""
    c = auth.load_creds(creds_file)
    assert "scope" not in c
    assert c["tenant"] == "common"


def test_load_static_creds_complete_without_scope(tmp_path, monkeypatch):
    for field in auth.ENV_CREDS:
        monkeypatch.setenv(auth.env_var_for(field), f"env-{field}")
    c = auth.load_static_creds(str(tmp_path / "nope.yaml"))
    assert "scope" not in c


def test_scope_not_in_env_creds_or_required():
    assert "scope" not in auth.ENV_CREDS
    assert "scope" not in auth.REQUIRED


def test_leftover_scope_key_in_file_ignored_with_stderr_warning(tmp_path, capsys):
    p = tmp_path / "with-scope.yaml"
    p.write_text(VALID_WITH_SCOPE)
    os.chmod(p, 0o600)
    c = auth.load_creds(str(p))
    assert "scope" not in c
    err = capsys.readouterr().err
    assert "scope" in err.lower()


def test_no_scope_key_no_warning(creds_file, capsys):
    auth.load_creds(creds_file)
    err = capsys.readouterr().err
    assert "leftover" not in err.lower() and "ignoring" not in err.lower()


def test_leftover_scope_warning_also_from_load_static_creds(tmp_path, capsys):
    p = tmp_path / "with-scope.yaml"
    p.write_text(VALID_WITH_SCOPE)
    auth.load_static_creds(str(p))
    err = capsys.readouterr().err
    assert "scope" in err.lower()


def test_ms_ads_scope_env_ignored(creds_file, monkeypatch):
    monkeypatch.setenv("MS_ADS_SCOPE", "should-be-ignored")
    c = auth.load_creds(creds_file)
    assert "scope" not in c


def test_validated_tenant_accepts_guid_domain_and_reserved_words():
    assert auth._validated_tenant("11111111-2222-3333-4444-555555555555") == \
        "11111111-2222-3333-4444-555555555555"
    assert auth._validated_tenant("example.onmicrosoft.com") == "example.onmicrosoft.com"
    assert auth._validated_tenant("common") == "common"
    assert auth._validated_tenant("organizations") == "organizations"
    assert auth._validated_tenant("consumers") == "consumers"
    assert auth._validated_tenant("  common  ") == "common"  # stripped


@pytest.mark.parametrize("bad", ["", "   ", "foo/bar", "a b", "foo\\bar", "a?b"])
def test_validated_tenant_rejects_junk(bad):
    with pytest.raises(auth.CredsError):
        auth._validated_tenant(bad)


def test_validated_tenant_truncates_huge_value_in_error():
    huge = "/" * 500  # illegal char, so it hits the reject path, not just "huge but valid"
    with pytest.raises(auth.CredsError) as exc:
        auth._validated_tenant(huge)
    assert len(str(exc.value)) < 200


def test_build_authorization_passes_tenant_into_sdk_grant(creds_file, monkeypatch):
    captured = {}
    real_grant_cls = auth.OAuthWebAuthCodeGrant

    class SpyGrant(real_grant_cls):
        def __init__(self, *a, **kw):
            captured.update(kw)
            super().__init__(*a, **kw)

        def request_oauth_tokens_by_refresh_token(self, *a, **kw):
            return None  # no live call

    monkeypatch.setattr(auth, "OAuthWebAuthCodeGrant", SpyGrant)
    auth.build_authorization(creds_file)
    assert captured.get("tenant") == "common"
