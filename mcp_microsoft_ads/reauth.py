#!/usr/bin/env python3
"""One-time interactive OAuth re-auth for the Microsoft Ads MCP.

Run when the server dies with AADSTS50076 — the tenant now enforces MFA (Conditional
Access) and the stored refresh_token predates it, so silent refresh can no longer mint an
access token. This walks the auth-code flow in the browser (which satisfies MFA once),
mints a fresh refresh_token carrying the MFA claim, verifies it with one silent refresh,
and writes it into the credentials file (auth.creds_path()) via the same atomic, .bak-first
writer the server uses (auth.persist_rotated_token). Secrets are never printed.

    .venv/bin/python -m mcp_microsoft_ads.reauth              # interactive (clipboard, macOS only)
    .venv/bin/python -m mcp_microsoft_ads.reauth --url-file P  # read the redirect URL from a file
                                                                # instead (Windows/Linux path)
    .venv/bin/python -m mcp_microsoft_ads.reauth --selftest    # offline check of code parsing (no network)

The redirect URL / auth code is ~1.2 KB — too long to paste into a terminal prompt
(macOS cooks input at ~1 KB / MAX_CANON and just beeps). On macOS the code is read from
the clipboard via pbpaste (you press Enter, nothing to paste); on Windows/Linux (no
pbpaste) use --url-file instead — paste the redirect URL into a file and pass its path.

After a successful run: restart Claude Code so the MCP reloads the rotated token
(the stale one lives in the running process's memory).
"""
import argparse
import json
import secrets
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from .auth import (
    CredsError,
    _validated_tenant,
    creds_path,
    load_creds,
    load_static_creds,
    persist_rotated_token,
    write_initial_refresh_token,
)

REDIRECT_URI = "http://localhost"
# authorize needs openid/profile + offline_access (offline_access is what returns a
# refresh token); the token/refresh calls use the bare manage scope.
AUTHORIZE_SCOPE = "openid profile https://ads.microsoft.com/msads.manage offline_access"
TOKEN_SCOPE = "https://ads.microsoft.com/msads.manage offline_access"


def _endpoint(tenant, kind):
    return f"https://login.microsoftonline.com/{tenant or 'common'}/oauth2/v2.0/{kind}"


def authorize_url(client_id, tenant, state):
    q = urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "scope": AUTHORIZE_SCOPE, "state": state,
        "prompt": "login",  # force a fresh interactive sign-in so MFA is satisfied
    })
    return _endpoint(tenant, "authorize") + "?" + q


def extract_code_and_state(pasted):
    """Parse a full localhost redirect URL and return (code, state); both may be
    None.

    Bare codes (input with no "code=" marker) are REJECTED -- returns (None, None),
    same as empty input. A bare code carries no state, and state verification must
    never be optional: accepting it would let anyone who controls the clipboard/
    url-file content skip the anti-tampering check entirely. Only a full redirect
    URL, verified against the state main() generated, is accepted.
    """
    pasted = (pasted or "").strip()
    if not pasted or "code=" not in pasted:
        return None, None
    qs = urllib.parse.urlparse(pasted).query or pasted.split("?", 1)[-1]
    params = urllib.parse.parse_qs(qs)
    code = (params.get("code") or [None])[0]
    state = (params.get("state") or [None])[0]
    return code, state


def extract_code(pasted):
    """Parse a full localhost redirect URL; return the code, or None (see
    extract_code_and_state -- bare codes are rejected, not just unstated)."""
    return extract_code_and_state(pasted)[0]


def _post_token(tenant, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(_endpoint(tenant, "token"), data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # 400s carry the AADSTS reason in the body
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": f"http_{e.code}"}


def _selftest():
    assert extract_code("http://localhost/?code=ABC123&state=x") == "ABC123"
    assert extract_code("http://localhost/?state=x&code=A.B-C_D0") == "A.B-C_D0"
    assert extract_code("  ABC123  ") is None  # bare code rejected, not just bare
    assert extract_code("") is None
    assert extract_code(None) is None
    assert extract_code_and_state("http://localhost/?code=ABC123&state=st1") == ("ABC123", "st1")
    assert extract_code_and_state("http://localhost/?code=ABC123") == ("ABC123", None)
    assert extract_code_and_state("ABC123") == (None, None)
    assert extract_code_and_state("") == (None, None)
    assert authorize_url("cid", "common", "st").startswith(
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")
    assert "prompt=login" in authorize_url("cid", None, "st")
    print("selftest ok")


def _redirect_from_clipboard():
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="offline parsing check, no network")
    ap.add_argument("--url-file", help="read the redirect URL from this file instead of the clipboard")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return 0

    bootstrap = False
    try:
        creds = load_creds()
    except CredsError as load_err:
        try:
            creds = load_static_creds()
        except CredsError as static_err:
            print(str(static_err), file=sys.stderr)
            return 2
        if creds.get("refresh_token"):
            # Statics + a refresh_token are both present in the file, so load_creds
            # should have succeeded too -- something else is wrong. Surface the
            # original error rather than guessing this is a bootstrap.
            print(str(load_err), file=sys.stderr)
            return 2
        bootstrap = True
        print("mcp-microsoft-ads: no refresh_token found — first-run bootstrap: "
              "complete sign-in below to create the credentials file.")

    try:
        tenant = _validated_tenant(creds.get("tenant", "common"))
    except CredsError as e:
        print(str(e), file=sys.stderr)
        return 2
    state = secrets.token_hex(8)

    print("\n1) Open this URL, sign in as the Microsoft Ads user, complete MFA, and approve:\n")
    print("   " + authorize_url(creds["client_id"], tenant, state))
    print("\n2) The browser lands on http://localhost/?code=... — the page won't load, that's fine.")
    print("   You must paste the FULL http://localhost/?code=...&state=... redirect URL —")
    print("   a bare code by itself is no longer accepted (there'd be no state to verify).")
    # The code is ~1.2 KB; a terminal paste blows past MAX_CANON and hangs. Read it from
    # the clipboard (you just copy the URL and press Enter) or from a file.
    if args.url_file:
        redirect = open(args.url_file).read()
    else:
        input("\n   Copy that whole http://localhost/... URL from the address bar (Cmd-C),"
              "\n   then press Enter here — I'll read it from your clipboard: ")
        redirect = _redirect_from_clipboard()
    code, cb_state = extract_code_and_state(redirect)
    if not code:
        where = args.url_file or "the clipboard"
        print(f"no authorization code found in {where} — paste the FULL "
              "http://localhost/?code=...&state=... redirect URL and try again (or use --url-file); "
              "a bare code is not accepted", file=sys.stderr)
        return 2
    # The redirect URL must carry the same state we generated, or this could be a
    # tampered/replayed/stale URL, or (pre-fix) a bare code that skipped verification
    # entirely -- reject unconditionally before any token exchange.
    if cb_state != state:
        print("redirect state does not match (missing or mismatched) — possible tampering "
              "or a stale/reused URL; re-copy the FULL redirect URL from a fresh sign-in and try again",
              file=sys.stderr)
        return 2

    tok = _post_token(tenant, {
        "client_id": creds["client_id"], "client_secret": creds["client_secret"],
        "code": code, "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code", "scope": TOKEN_SCOPE,
    })
    new_rt = tok.get("refresh_token")
    if not new_rt:
        print("token exchange failed:", tok.get("error"),
              (tok.get("error_description") or "")[:400], file=sys.stderr)
        return 2

    # Verify the new token does the exact thing that was broken: silently mint an access
    # token. MS rotates the refresh token on every grant, so persist whatever is current
    # AFTER this verify refresh, not the (now likely dead) auth-code token.
    check = _post_token(tenant, {
        "client_id": creds["client_id"], "client_secret": creds["client_secret"],
        "refresh_token": new_rt, "grant_type": "refresh_token", "scope": TOKEN_SCOPE,
    })
    if not check.get("access_token"):
        print("WARNING: new refresh_token did not verify:",
              (check.get("error_description") or "")[:400], file=sys.stderr)
        return 2
    final_rt = check.get("refresh_token") or new_rt

    path = creds_path()
    if bootstrap:
        write_initial_refresh_token(final_rt, path)
        print(f"\n✅ credentials file created at {path}.")
        print("   Restart Claude Code so the MCP picks up the new credentials.")
    elif persist_rotated_token(creds["refresh_token"], final_rt, path):
        print(f"\n✅ refresh_token updated in {path} (.bak saved).")
        print("   Restart Claude Code so the MCP reloads the new token.")
    else:
        # Same token back is implausible after a rotate, but fail loud rather than silent.
        print("\nno change written (new token matched the old one) — re-run if the MCP still fails.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
