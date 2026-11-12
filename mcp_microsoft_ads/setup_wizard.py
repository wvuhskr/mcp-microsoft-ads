"""Guided first-time setup: `mcp-microsoft-ads setup`.

Asks for the six values Microsoft requires, saves them to the credentials file
(owner-only permissions), runs the browser sign-in (reauth), then prints the block to
paste into the MCP client's config. Everything here is also doable by hand —
docs/setup.md explains where each value comes from.
"""
import getpass
import json
import os
import sys

import yaml

from . import reauth
from .auth import (
    CredsError,
    _atomic_private_write,
    _validated_tenant,
    creds_path,
    load_static_creds,
)

_FIELDS = [  # (field, question, secret?)
    ("developer_token", "Developer token (from https://developers.ads.microsoft.com/Account)", True),
    ("client_id", "Application (client) ID from your Azure app registration", False),
    ("client_secret", "Client secret VALUE from your Azure app registration", True),
    ("customer_id", "Customer ID (the cid= number in the Microsoft Ads web address)", False),
    ("account_id", "Account ID (the aid= number in the Microsoft Ads web address)", False),
]

_CLIENT_CONFIG = {"mcpServers": {"microsoft-ads": {"command": "uvx", "args": [
    "--from", "git+https://github.com/wvuhskr/mcp-microsoft-ads", "mcp-microsoft-ads"]}}}


def _ask(question: str, secret: bool) -> str:
    while True:
        value = (getpass.getpass(f"{question}: ") if secret else input(f"{question}: ")).strip()
        if value:
            return value
        print("  (required)")


def collect() -> dict:
    creds = {field: _ask(q, secret) for field, q, secret in _FIELDS}
    for field in ("customer_id", "account_id"):
        if not creds[field].isdigit():
            raise CredsError(f"{field} must be digits only, got {creds[field]!r}")
    tenant = input("Tenant [press Enter for 'common', right for most people]: ").strip()
    creds["tenant"] = _validated_tenant(tenant or "common")
    return creds


def main() -> int:
    path = creds_path()
    print("mcp-microsoft-ads setup\n")
    try:
        load_static_creds(path)
        print(f"Found existing settings at {path} (or in MS_ADS_* environment variables); "
              "keeping them and going straight to sign-in.\n")
    except CredsError as e:
        if os.path.lexists(path):
            # never overwrite a file that may hold real values -- a person fixes it
            print(f"{e}\n\nA settings file already exists and was left untouched. Add the "
                  "missing values to it (docs/setup.md step 5), then run setup again.",
                  file=sys.stderr)
            return 2
        print("You need a developer token and an Azure app registration first — "
              "docs/setup.md steps 2 and 3 walk through both.\n"
              "Secrets are hidden as you type or paste them.\n")
        try:
            creds = collect()
        except CredsError as e:
            print(str(e), file=sys.stderr)
            return 2
        os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
        _atomic_private_write(path, yaml.safe_dump(creds))
        print(f"\nSaved to {path} (readable only by you).\n")

    rc = reauth.main()
    if rc != 0:
        return rc
    print("\nLast step — add this to your MCP client's config, then restart it:\n")
    print(json.dumps(_CLIENT_CONFIG, indent=2))
    print("\nThe server starts read-only. Ask your assistant to run health_check, then "
          "list_accounts, and confirm it is the account you expect before turning writes on "
          "(README: 'Turning on changes').")
    return 0
