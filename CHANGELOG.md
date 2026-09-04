# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## Unreleased

Security and account-safety fixes:

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

## [Unreleased]

### Changed

- Compatible with both mcp 1.x and 2.x (requirement widened to `mcp>=1.0,<3`;
  mcp 2.0 renamed `FastMCP` to `MCPServer` — same tool surface, shimmed in
  `app.py`). Suite passes on mcp 1.29.0 and 2.1.0.
- Dependency floors: bingads 13.0.29, pyyaml>=6.0.3, bandit>=1.9.4,
  setuptools>=84; gitleaks-action v3.0.0 in CI; Dependabot bumps.

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
