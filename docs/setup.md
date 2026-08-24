# Setup: from zero to a working `refresh_token`

This walks a developer with a Microsoft Advertising account and **no prior Azure
experience** through everything needed before the [README](../README.md)'s "First-run
walkthrough" applies: a developer token, an Azure app registration, and a completed
OAuth sign-in.

Azure's own portal UI moves around over time. The steps below describe it "as of this
writing" (2026); if a menu has moved, use Microsoft's own docs, linked at each step, as
the source of truth for the current UI. Everything this doc says about what *this
server's code* does — the redirect URI, the scopes it requests, the files it writes —
is accurate to the code and won't drift the way portal screenshots do.

## 1. Prerequisites

- A Microsoft Advertising account with API access enabled. If you don't have API
  access yet, see step 2.
- Python 3.12 or newer (`python3 --version`).
- This repo cloned locally, then:

  ```bash
  python3 -m venv .venv  # requires Python >= 3.12
  .venv/bin/pip install -e ".[dev]"
  ```

  (Same command as the README's Install section — this doc doesn't repeat the rest of
  that section; see the README for MCP client configuration and the environment
  variable reference.)

## 2. Developer token

The developer token identifies your application to the Microsoft Advertising API,
separately from the OAuth sign-in in step 6. Get it from the Microsoft Advertising web
UI: sign in, open **Account settings**, and look for the developer token page. Exact
menu wording changes over time — Microsoft's own current instructions are the
authoritative source:

<https://learn.microsoft.com/en-us/advertising/guides/get-started>

Copy the token somewhere safe; it's one of the six static credential fields in step 5.

## 3. Azure app registration

Microsoft Advertising's OAuth sign-in runs through Azure Active Directory (Entra ID),
even for an account with no Microsoft 365 subscription or admin behind it — a personal
Microsoft account is enough. You need an **app registration** so this server has a
`client_id` and `client_secret` to authenticate with.

1. Go to <https://portal.azure.com> and sign in (a personal Microsoft account works —
   Azure will create a free directory for you if you don't already have one; you don't
   need any paid subscription for this step, just the free app-registration feature).
2. In the search box at the top, type **App registrations** and open it.
3. Click **New registration**.
4. **Name**: anything memorable, e.g. `mcp-microsoft-ads`.
5. **Supported account types**: choose **"Accounts in any organizational directory and
   personal Microsoft accounts"** (this is the option Azure's SDK/API calls
   `AzureADandPersonalMicrosoftAccount`). This matters because most Microsoft
   Advertising logins are personal Microsoft accounts (an `@outlook.com`/`@hotmail.com`
   address, or a personal account added to Advertising), not a work/school account
   under a company's Microsoft 365 tenant. Picking a narrower option (e.g. "single
   tenant") will reject sign-in for a personal-account user with a tenant/directory
   error. You do **not** need to be an M365 admin, and no admin needs to approve
   anything for this option.
6. **Redirect URI**: platform **Web**, value exactly

   ```
   http://localhost
   ```

   This is not a placeholder — it's the literal string the code sends on every
   authorize request and every token exchange (`REDIRECT_URI` in
   `mcp_microsoft_ads/reauth.py`, and `redirection_uri` in
   `mcp_microsoft_ads/auth.py`'s `build_authorization`). No port, no path, no trailing
   slash. It must be registered under the **Web** platform (not "Mobile and desktop
   applications" / public client) because this flow uses a client secret. After you
   sign in, the browser will try to load `http://localhost/?code=...&state=...` and
   fail to connect — that's expected; nothing runs on port 80 locally. You copy the URL
   from the address bar, you don't need the page to load. (Step 6 below covers this.)
7. Click **Register**.
8. On the app's **Overview** page, copy the **Application (client) ID** — that's
   `client_id`.
9. Go to **Certificates & secrets** → **New client secret**. Give it a description and
   an expiry (Azure defaults to something short, like 6 or 12 months — pick whatever
   your renewal process can live with, and note the expiry date somewhere, since an
   expired secret makes every future sign-in fail with an invalid-client error until
   you generate a new one and update your credentials file). Copy the secret **value**
   (not the "Secret ID") immediately — Azure only shows it once. That's `client_secret`.
10. `tenant` is the directory this app was registered in. For the account type in step
    5, the default value `common` works for both personal and work/school accounts —
    it's what `mcp_microsoft_ads/reauth.py` falls back to when a `tenant` value isn't
    given yet (`creds.get("tenant", "common")` in `main()`). You can also use a specific
    tenant GUID or domain if you have one; `mcp_microsoft_ads/auth.py`'s
    `_validated_tenant` accepts a GUID, a domain name, or `common`/`organizations`/
    `consumers`, and rejects anything else before it ever reaches Microsoft (it's
    spliced directly into the login URL).

Microsoft's own current app-registration walkthrough, if the portal has moved since
this was written:
<https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app>

## 4. Scopes this server requests

You don't configure scopes anywhere in the portal for this flow — they're fixed in
code. Two places request scope, for two different purposes:

- **Bootstrap/reauth sign-in** (`mcp_microsoft_ads/reauth.py`): the browser authorize
  request asks for `openid profile https://ads.microsoft.com/msads.manage
  offline_access` (`AUTHORIZE_SCOPE`); the token exchange and the one-time verify
  refresh use `https://ads.microsoft.com/msads.manage offline_access` (`TOKEN_SCOPE`).
  `offline_access` is what makes Microsoft hand back a `refresh_token` at all;
  `msads.manage` is the Microsoft Advertising management scope this server's tools
  need.
- **Every subsequent refresh while the server runs** (`mcp_microsoft_ads/auth.py`,
  `build_authorization`): goes through the `bingads` SDK's `OAuthWebAuthCodeGrant`,
  which uses its own fixed Microsoft Ads scope internally — not a value this project
  sets. (`auth.py`'s module docstring: "`scope` is not part of the schema... the SDK's
  fixed Microsoft Ads scope is used.") If an existing credentials file has a leftover
  `scope:` key from an older version, it's read and ignored with a one-line stderr
  notice — safe to delete, never required.

No "API permissions" step is needed in the Azure app registration for this — the
`msads.manage` scope is a Microsoft Advertising resource scope requested at OAuth time,
not an Azure AD API permission you grant/consent to in the portal.

## 5. Credentials file and environment variables

Seven fields total. Six are static and can live in either the environment or the
credentials file (env wins when both are set); the seventh, `refresh_token`, is
file-only — Microsoft rotates it on every refresh, and only the file gives the next
run somewhere to read the new value from (an env var can't be rewritten by the
process using it).

| Field | Env var |
|---|---|
| `developer_token` | `MS_ADS_DEVELOPER_TOKEN` |
| `client_id` | `MS_ADS_CLIENT_ID` |
| `client_secret` | `MS_ADS_CLIENT_SECRET` |
| `customer_id` | `MS_ADS_CUSTOMER_ID` |
| `account_id` | `MS_ADS_ACCOUNT_ID` |
| `tenant` | `MS_ADS_TENANT` |
| `refresh_token` | — file only, written by the bootstrap/reauth flow in step 6 |

`customer_id` and `account_id` come from your Microsoft Advertising account (Accounts
& Billing in the Advertising UI — not something this doc verifies against code, since
it's plain account lookup, not an OAuth mechanic).

The file lives at `~/.mcp-microsoft-ads/credentials.yaml` by default, overridable with
`MS_ADS_CREDENTIALS_PATH`. If you're setting all six static fields as env vars, you
don't need to create the file yourself at all — the bootstrap sign-in in step 6 creates
it with just `refresh_token` inside, `chmod 0600`, in a `0700` parent directory.

If you'd rather put everything in the file (or are still missing some env vars), a
sample with obviously fake values:

```yaml
developer_token: "AAAA-BBBBBBBBBBBB"
client_id: "00000000-0000-0000-0000-000000000000"
client_secret: "fake~ClientSecretValueGoesHere1234567890"
customer_id: "1234567"
account_id: "987654321"
tenant: "common"
```

(`refresh_token` isn't shown above because you don't have one yet — step 6 creates it.)

## 6. Bootstrap sign-in

Once the six static fields are in place (env vars, the file, or a mix), run:

```bash
.venv/bin/python -m mcp_microsoft_ads.reauth
```

This is the exact flow the code runs (`mcp_microsoft_ads/reauth.py`, `main()`):

1. It tries to load full credentials; since there's no `refresh_token` yet, it falls
   back to the six static fields and prints `no refresh_token found — first-run
   bootstrap: complete sign-in below to create the credentials file.`
2. It prints an authorize URL — open it in a browser, sign in as the Microsoft
   Advertising user, and complete MFA if your account requires it (the request
   includes `prompt=login` specifically to force a fresh interactive sign-in so MFA
   gets satisfied now rather than later).
3. After you approve, the browser is redirected to
   `http://localhost/?code=...&state=...`. **The page will fail to load — that's
   expected**, nothing is listening on port 80 on your machine. What matters is the
   full URL sitting in your browser's address bar.
4. **Getting that URL into the tool**: the URL is roughly 1.2 KB, too long to paste
   into most terminals (macOS's terminal driver caps a single line of input around 1
   KB and will just refuse the rest / beep). So:
   - **On macOS**, the default path: copy the URL (Cmd-C from the address bar), then
     press Enter at the prompt — the tool reads the clipboard itself via `pbpaste`, you
     don't paste into the terminal at all.
   - **On Windows or Linux** (no `pbpaste`), use the file fallback instead: paste the
     full redirect URL into any text file, then run:
     ```bash
     .venv/bin/python -m mcp_microsoft_ads.reauth --url-file /path/to/that/file.txt
     ```
5. The tool parses `code` and `state` out of the URL you provided. Two checks, both
   hard failures if they don't hold:
   - **A bare code with no full URL is rejected outright** — you must paste the whole
     `http://localhost/?code=...&state=...` string, not just the code value. This is
     deliberate: a bare code carries no `state` to check against step 2's request, so
     accepting one would skip the tamper check below entirely.
   - **State must match.** The tool generated a random `state` value before building
     the authorize URL in step 2; if the URL you paste back carries a different (or
     missing) `state`, it aborts with a message about possible tampering or a
     stale/reused URL, and asks you to re-copy the URL from a fresh sign-in. This is
     the standard OAuth anti-CSRF/anti-replay check — there's no way to bypass it, by
     design.
6. On a valid code+state, it exchanges the code for a token, then immediately does one
   extra silent refresh to verify the new `refresh_token` actually works (Microsoft
   rotates the refresh token on every use, so what gets saved is whatever came back
   from that verification refresh, not the first one).
7. It writes the file: on a true first run, `write_initial_refresh_token` creates
   `~/.mcp-microsoft-ads/credentials.yaml` (and its parent directory, mode `0700`) if
   they don't exist, and writes the new file `0600`.
8. **Restart Claude Code (or whatever MCP client is running the server)** afterward —
   the already-running server process still has the old (or no) token in memory.

## 7. Refresh-token rotation — why the file matters

Microsoft mints a brand new `refresh_token` on **every** refresh, not just at sign-in;
the previous one stops working. `mcp_microsoft_ads/auth.py`'s `build_authorization`
wires a callback (`_on_refresh`) into every SDK refresh that calls
`persist_rotated_token`, which:

- reads the credentials file fully before opening any write handle (never truncates it
  first),
- refuses to run if the token it's about to replace isn't actually present in the file
  (a stale/already-rotated token in memory would otherwise silently no-op or corrupt
  the file),
- backs up the file to `<path>.bak` (`chmod 0600`) before writing,
- writes the replacement atomically (`tempfile` + `os.replace`) and `chmod 0600`.

Two consequences that follow directly from "the file's content changes underneath the
process, out of band, on every use":

- **File permissions are `0600` / directory `0700`** — the refresh token is a live
  bearer credential to your ad account, rewritten automatically, so both the file and
  its parent directory are kept private to your user by every writer.
- **One client per credentials file.** If two MCP clients (say, Claude Code and Claude
  Desktop) point at the same credentials file at the same time, one client's refresh
  can invalidate the token the other client is still holding in memory, and there's no
  cross-process locking at this layer to prevent that race. If you want more than one
  concurrent client, give each one its own credentials file (`MS_ADS_CREDENTIALS_PATH`
  set differently per client) and run this bootstrap once per file. (Same rule stated
  from the running-server side in the README's "Don't point two concurrent clients at
  the same credentials file" section.)

## 8. Verify it worked

Start the server **without** `MS_ADS_ENABLE_WRITES` set (read-only by default) and, from
your MCP client, call in order:

1. `health_check`
2. `list_accounts`
3. `get_account_info`

Check the account ID, name, and currency that come back against the account you meant
to point this at before doing anything else. Only once that's confirmed should you
consider turning on writes — see the README's Environment variables table and
"First-run walkthrough" step 4 for the write-enabling flags; this doc doesn't repeat
that table.

## 9. Troubleshooting

- **`<path> not found — create the credentials file or export the MS_ADS_* env vars`**
  — none of the six static env vars are set and no file exists yet at
  `MS_ADS_CREDENTIALS_PATH` (or the default). Set the env vars, or create the file from
  the sample in step 5, then run bootstrap.
- **`creds incomplete, missing: [...]`** — one or more of the six static fields is
  missing from both the environment and the file. The message lists exactly which
  fields and which env vars would satisfy them.
- **`invalid tenant value: '...' — expected a GUID, a domain name, or
  common/organizations/consumers`** — your `tenant` value has characters that can't
  legally appear in a URL path segment, or is empty. Use `common` unless you have a
  specific reason to pin a tenant.
- **`no authorization code found in ...`** (step 6.4–6.5) — you pasted a bare code, an
  empty clipboard, or something that isn't the full redirect URL. Re-copy the entire
  `http://localhost/?code=...&state=...` string from the address bar after a fresh sign-in.
- **`redirect state does not match (missing or mismatched) — possible tampering or a
  stale/reused URL`** — the `state` in the URL you pasted doesn't match the one this
  run generated. Usually means you reused a URL from an earlier attempt; start the
  sign-in again from a fresh authorize link and copy that run's redirect URL.
- **`token exchange failed: ...`** — the code exchange itself was rejected by
  Microsoft; the message includes whatever `error`/`error_description` Microsoft's
  token endpoint returned, which for Azure AD is normally an `AADSTS#####` code. Two
  worth knowing by name:
  - **AADSTS50076** — the sign-in needs MFA (Conditional Access) that a plain refresh
    can't satisfy; this reauth flow exists specifically to clear it, since it forces an
    interactive browser sign-in (`prompt=login`).
  - **AADSTS7000215** — invalid client secret. Usually means the secret expired (see
    step 3.9) or was copied wrong; generate a new one in **Certificates & secrets** and
    update `client_secret`.
  For anything else, the `error_description` text in the failure message is Microsoft's
  own explanation — Microsoft's reference list is at
  <https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes>.
- **`WARNING: new refresh_token did not verify: ...`** — the code exchange briefly
  succeeded but the immediate verification refresh failed. Re-run bootstrap/reauth from
  a fresh sign-in.
