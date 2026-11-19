# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [1.1.0] - 2026-11-17

Built for marketers, not only developers.

### Added

- Guided setup: `mcp-microsoft-ads setup` asks for the six credential values, saves
  them to an owner-only file, runs the sign-in, and prints the client config block.
  `mcp-microsoft-ads reauth` re-runs the sign-in alone. Setup never overwrites an
  existing credentials file.
- One-line run with no clone: `uvx --from git+https://github.com/wvuhskr/mcp-microsoft-ads
  mcp-microsoft-ads`. Packaging metadata and a secretless PyPI release workflow
  (trusted publishing, triggered on `v*` tags) are in place. Published to PyPI on
  2026-11-19: `uvx mcp-microsoft-ads setup`.
- `describe_report` tool (48 tools total): lists every report type and its valid
  column names, read offline from the API definition shipped with the SDK, so
  `run_report` no longer requires knowing Microsoft's internal names.
- Report results name the period, aggregation, columns, and how conversions are
  counted in their `provenance` block.
- `advertiser.example.yaml`: commented example of the optional house-rules file.
- Sign-in reads the clipboard on Windows and Linux as well as macOS (offline-tested
  only on those two).

### Fixed

- The ready-made reports failed for 14 and 30 days (30 is the default): the server
  sent period names the API rejects. Now `Last14Days` / `Last30Days`.

### Fixed

- Several assistant sessions sharing one credentials file no longer break each other:
  a token rotation now replaces whatever token another session already wrote, instead
  of refusing because it differs from the one in memory. Only a file with no usable
  token is refused.

### Changed

- README rewritten around what a marketer can ask, a five-step quick start, and a
  plain-language list of every tool. Report tool descriptions say what each returns.
- Compatible with both mcp 1.x and 2.x (mcp 2.0 renamed `FastMCP` to `MCPServer`,
  shimmed in `app.py`). Dependency floors: bingads 13.0.29, pyyaml>=6.0.3,
  bandit>=1.9.4, setuptools>=84; gitleaks-action v3.0.0 in CI.

### Security

- Validate image contents and size locally; require the applied file to match its
  preview, and refuse file access when account writes are disabled.
- Create credential backups privately and atomically without following an existing
  backup link. Rotate only the refresh-token field and suppress secret-bearing
  parser excerpts from credential errors.
- Preserve safety refusals and partial-write recovery details through MCP 2 tool
  dispatch. Require MCP 1.28.1 or newer, retaining support below major version 3.
- Restrict ordinary ad-group status updates to Active or Paused. Handle single-item
  ad and keyword status readbacks without reporting a successful write as a crash.
- Do not retain an applicable draft when recording its preview fails.
- Add dependency vulnerability scanning and an oldest-supported-MCP test job to
  automated checks. These changes have been verified offline, not against live ads.
- Minimum MCP library raised from 1.28.1 to 1.30.0: versions below 1.30.0 carry
  known vulnerabilities (CVE-2026-59951, CVE-2026-116684, CVE-2026-116685,
  CVE-2026-116686).

## [1.0.1] - 2026-08-24

### Fixed

- `update_ad_group` now refuses a `cpc_bid` change unless the ad group's effective
  bid strategy is a manual one (shared `check_manual_bid_allowed` guard, same
  allowlist as `update_keyword_bid`). Previously the strategy guard was a no-op for
  this tool, contradicting README safety rule 4. `target_cpa` is unaffected — tCPA
  is an allowed lever on Smart Bidding.
- `confirm_and_apply` now re-runs the drafting tool's policy rails (spend ceilings,
  bid-strategy allowlist) immediately before mutating, so a ceiling lowered — or an
  account bid strategy changed — after drafting refuses the stale draft instead of
  applying it within the TTL window. A refusal does not consume the draft.
- `list_accounts` now pages through all visible accounts instead of silently
  returning only the first 100.
- CI: bandit false positives in the packaged `reauth.py` suppressed with
  justifications; its selftest asserts became real checks.

### Changed

- `pre-commit` / `pre-push` hooks fail closed when gitleaks, ruff, or the venv is
  missing, instead of silently skipping the check.
- CI tests on Python 3.12 and 3.14; pinned actions bumped (checkout v7.0.1,
  setup-python v7.0.0); Dependabot enabled for pip and GitHub Actions.
- README: links to Microsoft's SOAP-to-REST migration guides; install section
  starts from `git clone`; comparison table carries a "last checked" date.

## [1.0.0] - 2026-08-20

### Added

- Initial public release: 47 Microsoft Ads MCP tools, covering account/campaign/
  keyword/ad reporting reads and two-phase draft/confirm write operations.
- Write access is disabled by default; set `MS_ADS_ENABLE_WRITES=true` to enable
  the write tools.
- Spend-safety rails on write operations (budget/bid guardrails).
- JSONL audit log of every write action taken through the server.
- OAuth bootstrap via `python -m mcp_microsoft_ads.reauth`.
- Offline test suite (no live API calls required to run `make test`).
