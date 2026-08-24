# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities using GitHub's **private vulnerability
reporting**, not a public issue: go to this repo's **Security** tab and click
**"Report a vulnerability"**. This opens a private draft advisory that only
the maintainer can see until it's resolved.

Do not open a public issue for a security report.

## What NOT to include in a report (or any public issue)

Never paste any of the following, anywhere public:

- Credentials or refresh tokens
- The contents of your `credentials.yaml` (or equivalent creds file)
- Audit-log lines (from `MS_ADS_AUDIT_PATH` JSONL) — these can contain
  account IDs, campaign names, and other payload details

If you've already pasted one of these into a public issue, rotate the
credential immediately and ask a maintainer to delete/redact the comment.

## Supported versions

Only the latest released version is supported. This is a best-effort,
community-maintained project — there is no formal SLA for fixes or patches.

## Response expectations

There's no guaranteed response time. Reports are triaged as time allows, with
priority given to anything that could lead to credential exposure or
unintended writes to a live Microsoft Ads account.
