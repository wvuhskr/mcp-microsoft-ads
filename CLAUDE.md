# CLAUDE.md — mcp-microsoft-ads

MCP server for Microsoft Advertising SOAP API (`bingads` SDK, suds under the hood).
Two-phase writes: every mutating tool returns a draft; `rails.apply_draft` /
`confirm_and_apply` lands it. Rails + audit live in `rails.py` / `audit.py`.

## Live-verified rules — violate these and the live API breaks, tests won't catch it

1. **suds ONE-CHILD RULE**: a `*Response` element that declares exactly ONE child comes
   back **stripped** (the response IS that child); two+ children keep the wrapper. Not
   recursive — applies to `*Response` elements only. Explains every unwrapped-shape
   surprise. **Test fakes must encode the suds shape, not the WSDL shape.** Full rule:
   `client.partial_errors` docstring.
2. **Never write `getattr(resp, "Thing", None) or []` — always `client.as_list(...)`.**
   suds collapses single-item arrays to a bare object; `as_list` normalizes. Every SOAP
   list read routes through it (swept repo-wide, a5a1dde).
3. **`client.blank()` is mandatory for Update payloads.** Bare SimpleNamespace fails suds
   serialization. Null fields on a blank()-built Update mean "leave unchanged"
   (live-proven for Campaign; inferred for other entities).
4. **Type discriminators**: criterions use the class name (`Type="Location"`); Ads use
   the AdType ENUM (`Type="ResponsiveSearch"`, not the class name). Probe per entity
   family — don't generalize.
5. **Factory ns prefixes are load-bearing**: `ns3:ArrayOflong`, `ns3:ArrayOfstring`
   (campaign mgmt), `ns1:` (reporting). Wrong prefix = suds silently builds the wrong
   type.
6. **Docstring status lines are evidence, keep them honest**: every write tool states
   `Live-verified …` or `NOT live-verified …` (greppable, append-only). Never claim
   live verification for a path that hasn't run against the real account through THAT
   tool.

## Testing

- `.venv/bin/python -m pytest -q` (bare `python3` lacks bingads). 0 warnings expected —
  **a token-refresh warning in pytest output = a test is hitting the LIVE API**
  (conftest autouse patches `account_id`/`customer_id`; new fixtures must not bypass it).
- New regression tests must be verified discriminating: fail against pre-fix code
  (reverse-apply the fix), then pass.
- `ruff check .` must be clean (config in pyproject; pre-commit enforces gitleaks + ruff).

## Live probes

- Live API probes require real credentials. Read-only probes are fine; write probes only
  on clearly-marked throwaway campaigns (e.g. a `z.` prefix), net zero (create → verify →
  delete/restore).
- stdout is the MCP JSON-RPC channel — never print to it; diagnostics go to stderr.

## Deliberate non-fixes (do not "clean up")

- `insight.py` Recommendations fallback: stays. The Recommendations service returns error
  607 on accounts without the pilot feature; the fallback doubles as None-safety.
- Blind `except` in audit hardening (`rails.py`): deliberate — an audit failure must
  never mask a LANDED write.
- Conversion goals can't be API-deleted (only Paused / ExcludeFromBidding) — don't create
  throwaway probe goals casually; they're permanent clutter in the account.
