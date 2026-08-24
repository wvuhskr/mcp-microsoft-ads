# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
