# mcp-microsoft-ads

**Unofficial — not affiliated with, endorsed by, or supported by Microsoft.** This is
a third-party MCP (Model Context Protocol) server for Microsoft Advertising. "MCP" is
the open protocol that lets an AI assistant (Claude, or any other MCP client) call
tools against a real system — here, your Microsoft Ads account.

The safety-first Microsoft Ads MCP: 47 tools covering campaign, ad group, keyword,
negative-keyword, geo-targeting, ad-schedule, extension, media, audience, and
conversion-goal management, plus performance reporting and Ad Insight keyword
research — built so that giving an AI assistant write access to a live ad account
doesn't mean giving it a blank check.

## Why "safety-first"

Every mutating tool is **off by default**. Turning writes on is one env var, and even
then every write goes through the same layered gate before a single call reaches
Microsoft's API:

1. **`MS_ADS_ENABLE_WRITES=true`** — without it, every write tool refuses to run.
   Out of the box this is a read-only server.
2. **Draft → confirm.** A write tool never mutates on its own call. It returns a
   preview (a `draft_id` and exactly what would change); nothing lands until you — or
   your MCP client — calls `confirm_and_apply(draft_id)`.
3. **Spend ceilings.** Draft creation and apply both reject amounts above
   `MS_ADS_MAX_DAILY_BUDGET` (default 1000) or `MS_ADS_MAX_CPC` (default 50), **in the
   account's own currency**, not USD.
4. **Smart Bidding fail-closed guards.** Bid/adjustment writes are allowlisted to
   manual bidding strategies (`ManualCpc`, `EnhancedCpc`, `ManualCpv`, `ManualCpm`,
   `ManualCpa`); an unrecognized or automated strategy (`MaxConversions`, `TargetCpa`,
   `TargetRoas`, …) is refused rather than assumed safe.
5. **JSONL audit log.** Every draft and every apply is appended to a local,
   append-only JSONL file — a plain-text paper trail of what this server actually did.

Backed by 380+ offline tests (no live API calls) that exercise the rails, the SOAP
plumbing quirks, and the draft/confirm lifecycle against fakes.

### Be honest about what "draft → confirm" is *not*

Draft → confirm is a **preview gate against single-call accidents** — a typo in a bid,
a copy-paste mistake, calling the wrong tool. It is **not human-approval access
control**: the same MCP client that created the draft can call `confirm_and_apply`
immediately afterward, with no human in the loop required by the server. If you want
a human to review every write, that has to be enforced by your own workflow (a
separate approval step, a human-only client, etc.) — this server doesn't do it for you.

One write is called out separately because the layered story above can't fully bound
it: **`apply_recommendation`** hands off to a Microsoft-decided mutation — you preview
only the recommendation ID, not the change itself, so the actual monetary effect isn't
something this server's spend ceilings can check in advance. It's kept (Microsoft's
recommendations can be useful), loudly labeled as the exception it is, and gated
behind **both** `MS_ADS_ENABLE_WRITES` and its own separate flag,
`MS_ADS_ALLOW_APPLY_RECOMMENDATION` — so turning on writes generally does not turn
this one on.

### What this server does *not* do

- No Quality Score diagnostics.
- No automatic retry/backoff on transient API failures (a failed call fails; you
  retry).
- Built on Microsoft's SOAP Bing Ads SDK, which Microsoft has announced end-of-life —
  see below.

## SOAP end-of-life and the REST roadmap

This server is built on the `bingads` SOAP SDK. Microsoft has announced that SOAP is
end-of-life on **2027-01-31**, and recommends migrating before **2026-10-01** (new
API features ship REST-only from then). See Microsoft's
[Migrate to REST API](https://learn.microsoft.com/en-us/advertising/guides/migrate-to-rest?view=bingads-13)
guide and the
[Python SDK SOAP-to-REST migration guide](https://learn.microsoft.com/en-us/advertising/guides/python-sdk-migration-soap-to-rest?view=bingads-13).
Porting this server's tools to REST is the roadmap item — SOAP still works today and
isn't going away until the EOL date, but don't plan around it long-term.

## How this compares

| | write access | write safety layer | notes |
|---|---|---|---|
| **mcp-microsoft-ads** (this project) | yes, opt-in | draft→confirm + spend ceilings + Smart-Bidding guards + audit log | SOAP today, REST planned |
| [mharnett/mcp-bing-ads](https://github.com/mharnett/mcp-bing-ads) | — | — | see their repo for current scope |
| bit-of-a-shambles Bing Ads MCP server | — | — | see that project's repo for current scope |
| CData Microsoft Ads MCP connector | read-only | n/a | no write path |
| Microsoft's official hosted MCP | — | — | see Microsoft's docs for current scope |

Comparison last checked 2026-08-24. This project doesn't claim to know the exact
current feature set of the other rows
beyond what's read-only vs. not — check each project's own repo/docs for details. The
differentiators above (opt-in writes, two-phase draft→confirm, spend ceilings,
Smart-Bidding fail-closed guards, JSONL audit log, 380+ offline tests) are what this
project is built around.

## Install

```bash
git clone https://github.com/wvuhskr/mcp-microsoft-ads.git
cd mcp-microsoft-ads
python3 -m venv .venv  # requires Python >= 3.12
.venv/bin/pip install -e ".[dev]"
```

Full setup — Azure app registration, developer token, first OAuth sign-in — is in
[docs/setup.md](docs/setup.md). This section only covers running the server once
credentials already exist.

### Credentials

Credentials live outside the repo, in a YAML file (default
`~/.mcp-microsoft-ads/credentials.yaml`, override with `MS_ADS_CREDENTIALS_PATH`):
`developer_token`, `client_id`, `client_secret`, `refresh_token`, `customer_id`,
`account_id`, `tenant`.

The six static fields (everything except `refresh_token`) can instead be set as
`MS_ADS_DEVELOPER_TOKEN`, `MS_ADS_CLIENT_ID`, `MS_ADS_CLIENT_SECRET`,
`MS_ADS_CUSTOMER_ID`, `MS_ADS_ACCOUNT_ID`, `MS_ADS_TENANT` env vars — env wins over the
file when both are present. `refresh_token` is file-only: Microsoft rotates it on
every use, and a rotated token has to be persisted somewhere for the next run, which
an env var can't do. The server persists each rotation back to the credentials file
atomically (a `.bak` is kept).

### First-run walkthrough

1. **Bootstrap sign-in:**
   ```bash
   .venv/bin/python -m mcp_microsoft_ads.reauth
   ```
   Walks an interactive OAuth sign-in in your browser and writes the resulting
   `refresh_token` into the credentials file. See `docs/setup.md` for the Azure app
   registration and developer token this step depends on.
2. **Start the server read-only** (don't set `MS_ADS_ENABLE_WRITES` yet) and, from
   your MCP client, call `health_check`, then `list_accounts` and/or
   `get_account_info`.
3. **Confirm the account is the one you mean** — check the account ID, account name,
   and account currency returned by those calls against the account you intend to
   let this server touch. The spend ceilings below are enforced in that account's
   currency, so a currency mismatch is worth catching before writes are ever on.
4. **Only then** set `MS_ADS_ENABLE_WRITES=true` (and, if you want it,
   `MS_ADS_ALLOW_APPLY_RECOMMENDATION=true`) and restart the server.

## MCP client configuration

Both configs assume the venv above lives at `~/mcp-microsoft-ads/.venv` — replace that
with the actual absolute path on your machine (both clients need an absolute path,
not `~`).

**Claude Code** (`.mcp.json` in your project, or via `claude mcp add`):

```json
{
  "mcpServers": {
    "microsoft-ads": {
      "command": "/home/you/mcp-microsoft-ads/.venv/bin/python",
      "args": ["-m", "mcp_microsoft_ads.server"]
    }
  }
}
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "microsoft-ads": {
      "command": "/home/you/mcp-microsoft-ads/.venv/bin/python",
      "args": ["-m", "mcp_microsoft_ads.server"]
    }
  }
}
```

### Don't point two concurrent clients at the same credentials file

**Sharing one credentials file between concurrent clients is unsupported.** The
`refresh_token` is a rotating, shared, mutable file: every refresh mints a new token
and immediately invalidates the old one, and the read-modify-write that persists the
rotation can't be safely serialized across two separate processes at this layer. Two
clients racing to refresh from the same file can strand each other with an
invalidated token. If you want more than one concurrent client (e.g. Claude Code and
Claude Desktop open at once), give each one its own credentials file and its own Azure
app registration — set `MS_ADS_CREDENTIALS_PATH` differently per client and bootstrap
each with its own `reauth` run.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `MS_ADS_CREDENTIALS_PATH` | `~/.mcp-microsoft-ads/credentials.yaml` | credentials file location |
| `MS_ADS_ADVERTISER_CONFIG` | `~/.mcp-microsoft-ads/advertiser.yaml` | optional advertiser settings (blocked terms, advertiser domain, keyword-research defaults) — missing file means defaults |
| `MS_ADS_AUDIT_PATH` | `~/.mcp-microsoft-ads/audit.jsonl` | JSONL audit log location |
| `MS_ADS_ENABLE_WRITES` | `false` | master switch for all mutating tools |
| `MS_ADS_ALLOW_APPLY_RECOMMENDATION` | `false` | second, separate switch required (in addition to `MS_ADS_ENABLE_WRITES`) for `apply_recommendation` specifically |
| `MS_ADS_MAX_DAILY_BUDGET` | `1000` | daily-budget rail ceiling, **in the account's own currency** |
| `MS_ADS_MAX_CPC` | `50` | bid/tCPA rail ceiling, **in the account's own currency** |
| `MS_ADS_DRAFT_TTL_SECONDS` | `3600` | how long a draft stays valid before `confirm_and_apply` refuses it as stale |
| `MS_ADS_DEVELOPER_TOKEN`, `MS_ADS_CLIENT_ID`, `MS_ADS_CLIENT_SECRET`, `MS_ADS_CUSTOMER_ID`, `MS_ADS_ACCOUNT_ID`, `MS_ADS_TENANT` | — | the six static credential fields; env wins over the credentials file when both are set |

**Currency note:** `MS_ADS_MAX_DAILY_BUDGET` and `MS_ADS_MAX_CPC` are compared directly
against amounts in the account's own currency, not converted to or from USD. A `1000`
ceiling means 1000 units of whatever currency the account is denominated in.

## Test / lint

```bash
.venv/bin/python -m pytest -q     # offline test suite, ~370 tests, no live API calls
.venv/bin/python -m ruff check .  # lint
```

## Documentation

- [docs/setup.md](docs/setup.md) — Azure app registration, developer token, full
  bootstrap.
- [SECURITY.md](SECURITY.md) — how to report a vulnerability.
- [CHANGELOG.md](CHANGELOG.md) — release history.

## License and support

MIT-licensed — see [LICENSE](LICENSE). This is a best-effort, unpaid open-source
project: no SLA, no guaranteed response time. Issues are watched and triaged on a
best-effort basis.
