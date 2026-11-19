# mcp-microsoft-ads

**Unofficial — not affiliated with, endorsed by, or supported by Microsoft.** This is
a third-party MCP (Model Context Protocol) server for Microsoft Advertising. "MCP" is
the open protocol that lets an AI assistant (Claude, or any other MCP client) call
tools against a real system — here, your Microsoft Ads account.

**In plain terms:** this connects an AI assistant such as Claude to your Microsoft
Advertising (Bing Ads) account, so you can ask questions about performance in plain
English and, if you choose to allow it, have the assistant make changes for you. It
starts read-only. Changes are something you switch on, and every change is previewed
before it happens.

48 tools cover reporting, keyword research, and campaign, ad group, keyword,
negative-keyword, location, ad-schedule, extension, image, audience, and
conversion-goal management.

## What you can ask

Once it is connected, talk to your assistant normally. For example:

- "How did each campaign do over the last 30 days? Show spend, conversions and cost
  per conversion."
- "Which search terms spent money last month without converting? Check them against
  my existing negative keyword lists and suggest only new negatives."
- "Split last month's conversions by conversion goal for each campaign."
- "What was my impression share last quarter, and did I lose more to budget or to rank?"
- "Find keyword ideas for 'emergency plumber' and estimate clicks at a $12 bid."
- "Are any ads or keywords disapproved?"
- "Pause the ad group called Test, and show me the change before you make it."
  (needs changes switched on, see [Turning on changes](#turning-on-changes))

Every report comes back with a source note: the account, the report type, the date
range, the columns pulled, and how conversions were counted.

## Quick start

You need a Microsoft Advertising account and about 20 minutes, most of it on
Microsoft's side. All commands below go in a terminal (the Terminal app on a Mac,
PowerShell on Windows).

1. **Install uv**, a small free tool that downloads and runs this server for you. One
   command, from <https://docs.astral.sh/uv/getting-started/installation/>.
2. **Get two things from Microsoft**: a developer token and an "app registration".
   [docs/setup.md](docs/setup.md) steps 2 and 3 walk through both, click by click.
   This is the slow part, and you only do it once.
3. **Run the guided setup.** It asks for the values from step 2, opens the Microsoft
   sign-in, and saves everything in a private file on your computer:
   ```bash
   uvx mcp-microsoft-ads setup
   ```
   This installs the package from [PyPI](https://pypi.org/project/mcp-microsoft-ads/).
4. **Connect your assistant.** Setup ends by printing a short block of text. Paste it
   into your assistant's connector settings ([MCP client configuration](#mcp-client-configuration)
   shows where) and restart the assistant.
5. **Check it.** Ask the assistant: "Run a Microsoft Ads health check and list my
   accounts." Confirm the account name and currency are the ones you expect.

If the sign-in ever stops working (Microsoft asks for a fresh sign-in from time to
time), run the same command with `reauth` in place of `setup`.

## The tools

You never call these by name; the assistant picks them. The list is here so you know
what is possible.

**Look, never change (always available)**

| Tool | What it does |
|---|---|
| `health_check`, `list_accounts`, `get_account_info` | Confirm the connection and which account you are in |
| `get_campaign_performance`, `get_ad_performance`, `get_keyword_performance` | Ready-made performance reports for the last 7, 14 or 30 days |
| `get_search_terms` | What people actually typed before clicking |
| `get_geo_performance` | Performance by country, state, metro area and city |
| `run_report`, `describe_report` | Build-your-own report: any Microsoft report, any columns, any dates. `describe_report` lists what is available |
| `get_entities` | Current settings of campaigns, ad groups, keywords, ads, asset groups, schedules and locations |
| `get_negative_keywords` | Campaign negatives plus every shared negative list |
| `list_extensions`, `get_conversion_actions`, `get_policy_issues` | Extensions, conversion goals, disapproved ads and keywords |
| `discover_keywords`, `get_keyword_forecasts` | Keyword ideas and traffic estimates |
| `search_geo_targets` | Find a location to target or exclude |
| `list_recommendations` | Microsoft's recommendations (not available on every account) |

**Make changes (only when you switch changes on; each one is previewed first)**

| Tool | What it does |
|---|---|
| `confirm_and_apply` | Carries out a previewed change. Nothing changes without it |
| `pause_entity`, `enable_entity`, `remove_entity` | Pause, enable or delete a campaign, ad group, keyword or ad |
| `update_campaign`, `update_ad_group`, `update_keyword_bid` | Budget, status, target cost per conversion, manual bids |
| `draft_campaign`, `create_pmax_campaign`, `create_portfolio_bidding_strategy` | New campaigns (always created paused) and shared bid strategies |
| `draft_responsive_search_ad`, `draft_keywords`, `remove_keywords` | New ads and keywords (keywords created paused) |
| `add_negative_keywords`, `remove_negative_keywords` | Negatives on a campaign, ad group or shared list; duplicates are skipped |
| `set_campaign_schedule` | Replace a campaign's ad schedule |
| `exclude_geo_target`, `remove_geo_target` | Location exclusions |
| `draft_sitelinks`, `create_callouts`, `create_structured_snippets`, `remove_extension` | Ad extensions |
| `upload_image_asset` | Add an image to the account's library |
| `create_custom_audience`, `add_audience_targeting` | Remarketing lists and audience targeting |
| `create_conversion_action`, `set_conversion_action_primary_status` | Conversion goals |
| `apply_recommendation`, `dismiss_recommendation` | Act on Microsoft's recommendations (extra switch required) |

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

Backed by 420+ offline tests (no live API calls) that exercise the rails, the SOAP
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
Smart-Bidding fail-closed guards, JSONL audit log, 420+ offline tests) are what this
project is built around.

## Install for development

Most people should use the [Quick start](#quick-start) instead. To work on the code:

```bash
git clone https://github.com/wvuhskr/mcp-microsoft-ads.git
cd mcp-microsoft-ads
python3 -m venv .venv  # requires Python >= 3.12
.venv/bin/pip install -e ".[dev]"
```

In a clone, `.venv/bin/mcp-microsoft-ads setup` runs the same guided setup.

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
atomically (a `.bak` is kept). Both replacement files are created with
owner-only access before any credential content is written. Rotation changes only
the refresh-token value; unusual YAML aliases or block values may be rewritten
without their original comments to preserve the credential values safely.

### Image upload safety

`upload_image_asset` accepts valid PNG or JPEG files only. Local safety limits
are 5 MiB per file and 20 million pixels; these are this server's limits, not
Microsoft account limits. Animated and malformed images are rejected locally.
The preview includes a SHA-256 digest, a fingerprint of the file's contents.
Changing the file after preview requires a new draft. Validated bytes are uploaded
unchanged, including image metadata, so use assets intended for advertising.

### Supported tool library versions

The Model Context Protocol (MCP) library must be at least 1.30.0 and below 3.
The automated checks test the oldest supported version and the current version.
Intentional safety refusals and partial-write warnings remain visible to the
assistant in both library generations; unexpected errors retain the library's
normal handling.

## Turning on changes

Out of the box the server can only look. To let the assistant make changes:

1. **Confirm the account first.** While still read-only, have the assistant run
   `health_check`, then `list_accounts` and `get_account_info`. Check the account ID,
   name and currency against the account you mean. The spending limits below are
   enforced in that account's currency.
2. **Add the switch** to the connector block (see the next section) and restart the
   assistant: `MS_ADS_ENABLE_WRITES` set to `true`. Optionally lower the spending
   limits `MS_ADS_MAX_DAILY_BUDGET` and `MS_ADS_MAX_CPC` to fit your account.
3. **Optional house rules.** Copy [advertiser.example.yaml](advertiser.example.yaml) to
   `~/.mcp-microsoft-ads/advertiser.yaml` to ban words from ad copy, set your website,
   and keep the "no Performance Max campaign without a target" rule on.

## MCP client configuration

This is the block the guided setup prints. It tells your assistant how to start the
server.

**Claude Desktop**: Settings > Developer > Edit Config opens
`claude_desktop_config.json`. **Claude Code**: put it in `.mcp.json` in your project.

```json
{
  "mcpServers": {
    "microsoft-ads": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/wvuhskr/mcp-microsoft-ads", "mcp-microsoft-ads"]
    }
  }
}
```

With changes switched on, the same block gains an `env` section:

```json
{
  "mcpServers": {
    "microsoft-ads": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/wvuhskr/mcp-microsoft-ads", "mcp-microsoft-ads"],
      "env": { "MS_ADS_ENABLE_WRITES": "true", "MS_ADS_MAX_DAILY_BUDGET": "500" }
    }
  }
}
```

If your assistant reports it cannot find `uvx`, replace `"uvx"` with the full path
that `which uvx` (Mac/Linux) or `where uvx` (Windows) prints in a terminal. Working
from a clone instead: use `"command": "/full/path/to/mcp-microsoft-ads/.venv/bin/mcp-microsoft-ads"`
with no `args`.

### Several assistant sessions at once

Each open assistant session starts its own copy of this server, and they can all share
one credentials file on the same computer. Microsoft issues a new `refresh_token` on
each refresh but does not revoke the earlier ones when it does
([Microsoft's refresh-token documentation](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)),
so when two sessions refresh close together the newer token simply replaces the other
on disk. Each write is atomic, so the file always holds one complete, working token.
A file with no usable token at all is still refused rather than overwritten.

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
.venv/bin/python -m pytest -q     # offline test suite, 420+ tests, no live API calls
.venv/bin/python -m ruff check .  # lint
```

## Documentation

- [docs/setup.md](docs/setup.md) — Azure app registration, developer token, full
  bootstrap.
- [advertiser.example.yaml](advertiser.example.yaml) — optional house rules, with comments.
- [SECURITY.md](SECURITY.md) — how to report a vulnerability.
- [CHANGELOG.md](CHANGELOG.md) — release history.

## License and support

MIT-licensed — see [LICENSE](LICENSE). This is a best-effort, unpaid open-source
project: no SLA, no guaranteed response time. Issues are watched and triaged on a
best-effort basis.
