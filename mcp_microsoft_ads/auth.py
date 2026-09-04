"""Creds + OAuth. MS rotates the refresh token on every refresh; the SDK keeps the
rotated token in memory only. persist_rotated_token is the ONLY writer of the creds
file and must never truncate it (read fully before any write handle).

Cred resolution (owner decision 2026-07-30): the six STATIC creds come from MS_ADS_*
env vars; refresh_token comes from the file and ONLY the file. That split is forced,
not stylistic — a rotated refresh token has to be written somewhere for the next
process, and a process cannot write its own environment. Env-only refresh_token
authenticates exactly once and then dies with an invalidated token.

Env is preferred, the file is a fallback that warns on stderr per field, so an
existing install keeps working until all six are exported. Once they are, the
file needs nothing but refresh_token.

`scope` is not part of the schema: the SDK's fixed Microsoft Ads scope is used, and
a leftover `scope` key in an existing creds file is ignored with a calm stderr
warning (kept for install compatibility -- never required, never read).
"""
import os
import re
import sys
import tempfile

import yaml
from bingads import AuthorizationData, OAuthWebAuthCodeGrant

CREDS_PATH = os.path.expanduser("~/.mcp-microsoft-ads/credentials.yaml")
REQUIRED = {"developer_token", "client_id", "client_secret", "refresh_token",
            "customer_id", "account_id", "tenant"}
_TENANT_RE = re.compile(r"^[A-Za-z0-9.-]+$")
# refresh_token is deliberately absent: it rotates, so it is file-only.
ENV_CREDS = REQUIRED - {"refresh_token"}
ENV_PREFIX = "MS_ADS_"


class CredsError(RuntimeError):
    pass


def env_var_for(field: str) -> str:
    return ENV_PREFIX + field.upper()


def creds_path() -> str:
    return os.environ.get("MS_ADS_CREDENTIALS_PATH") or CREDS_PATH


def _parse_creds(raw: str, path: str) -> dict:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        # Parser messages and chained tracebacks can quote the secret-bearing line.
        raise CredsError(f"{path}: invalid credentials YAML; check the file locally") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise CredsError(f"{path}: credentials must be a mapping")
    return data


def _read_creds_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        raise CredsError(f"{path} not found — create the credentials file or export the "
                         "MS_ADS_* env vars (see README)") from None
    except (UnicodeError, OSError):
        raise CredsError(f"{path}: unable to read credentials file") from None
    return _parse_creds(raw, path)


def _read_creds_file_optional(path: str) -> dict:
    """Missing is fine for bootstrap; malformed or unreadable files still fail."""
    if not os.path.lexists(path):
        return {}
    return _read_creds_file(path)


def _atomic_private_write(path: str, raw: str) -> None:
    """Create privately BEFORE writing, then replace the directory entry.

    In particular, never follow a pre-existing .bak symlink or hard link and
    never inherit the original file's potentially public permissions.
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _gather_static(file_creds: dict) -> tuple[dict, list[str]]:
    """Env-wins-over-file for the six static ENV_CREDS fields. Shared by
    load_creds and load_static_creds so the two never drift."""
    creds, from_file = {}, []
    for field in sorted(ENV_CREDS):
        value = os.environ.get(env_var_for(field))
        if value:
            creds[field] = value
        elif field in file_creds:
            creds[field] = file_creds[field]
            from_file.append(field)
    return creds, from_file


def _notice_from_file(from_file: list[str], path: str) -> None:
    if from_file:
        # stderr only: stdout is the MCP JSON-RPC channel.
        print(f"mcp-microsoft-ads: {len(from_file)} cred(s) read from {path} instead of "
              f"env: {', '.join(from_file)} — export "
              f"{', '.join(env_var_for(f) for f in from_file)} to finish the move to env vars.",
              file=sys.stderr)


def _warn_leftover_scope(file_creds: dict, path: str) -> None:
    """`scope` is no longer part of the schema -- the SDK uses its own fixed
    Microsoft Ads scope. A leftover `scope` key in an existing creds file (the
    owner's real file has one) is harmless and ignored; just say so once per load."""
    if "scope" in file_creds:
        print(f"mcp-microsoft-ads: ignoring leftover 'scope' key in {path} — no longer "
              "used (the SDK's fixed Microsoft Ads scope applies); safe to delete it.",
              file=sys.stderr)


def _blank_rt(value) -> bool:
    """Shared blank rule for refresh_token, used by load_creds, load_static_creds,
    persist_rotated_token, and write_initial_refresh_token so all four agree: a
    value counts as ABSENT/blank/invalid if it isn't a string, or is empty/
    whitespace-only after stripping. A blank refresh_token in the file must never
    be treated as "present" -- among other things, persist_rotated_token would
    then be called with old="" and `"" in raw` / `raw.replace("", new)` silently
    interleaves the new token between every character of the file."""
    return not isinstance(value, str) or not value.strip()


def _validated_tenant(value) -> str:
    """AAD tenant: a GUID, a domain name, or one of common/organizations/consumers.
    Strip whitespace; reject anything with characters illegal in a URL path segment
    (the tenant is spliced straight into the authorize/token endpoint URL)."""
    value = (value or "").strip()
    if not value or not _TENANT_RE.match(value):
        shown = value if len(value) <= 40 else value[:40] + "…"
        raise CredsError(f"invalid tenant value: {shown!r} — expected a GUID, a domain "
                          "name, or common/organizations/consumers")
    return value


def load_static_creds(path: str | None = None) -> dict:
    """The six static creds only (no refresh_token requirement) -- env wins, file
    fills gaps. A MISSING file is fine here (nothing written yet, e.g. first-run
    bootstrap): treated as {}. A present-but-unreadable/invalid file still raises.
    refresh_token rides along if the file happens to have one (a refresh-token-less
    static file is the exact shape a bootstrap install starts from), so callers can
    tell "no creds at all" from "statics present, refresh_token still missing"."""
    path = path or creds_path()
    file_creds = _read_creds_file_optional(path)
    creds, from_file = _gather_static(file_creds)
    if "refresh_token" in file_creds and not _blank_rt(file_creds["refresh_token"]):
        creds["refresh_token"] = file_creds["refresh_token"]

    missing = sorted(ENV_CREDS - set(creds))
    if missing:
        wanted = ", ".join(env_var_for(f) for f in missing)
        raise CredsError(f"creds incomplete, missing: {missing} — set {wanted}, or add to {path}")

    _notice_from_file(from_file, path)
    _warn_leftover_scope(file_creds, path)
    return creds


def load_creds(path: str | None = None) -> dict:
    """Env wins for the six static creds; refresh_token always comes from the file."""
    path = path or creds_path()
    # The file is read unconditionally — it is the only possible source of
    # refresh_token, so there is no env-only mode to short-circuit into.
    file_creds = _read_creds_file(path)
    creds, from_file = _gather_static(file_creds)
    if "refresh_token" in file_creds and not _blank_rt(file_creds["refresh_token"]):
        creds["refresh_token"] = file_creds["refresh_token"]

    missing = sorted(REQUIRED - set(creds))
    if missing:
        wanted = ", ".join(env_var_for(f) for f in missing if f != "refresh_token")
        detail = f"set {wanted}, or add to {path}" if wanted else f"add refresh_token to {path}"
        raise CredsError(f"creds incomplete, missing: {missing} — {detail}")

    _notice_from_file(from_file, path)
    _warn_leftover_scope(file_creds, path)
    return creds


def persist_rotated_token(old: str, new: str | None, path: str | None = None) -> bool:
    path = path or creds_path()
    if _blank_rt(old):
        # Defense in depth: load_creds already refuses to surface a blank
        # refresh_token, so this should be unreachable via that path. But
        # `"" in raw` is always True and `raw.replace("", new)` would interleave
        # new between every character of the file -- never let that be reachable.
        raise CredsError("active refresh_token is blank/invalid; refusing string replacement")
    if not new or new == old:
        return False
    if _blank_rt(new):
        raise CredsError("new refresh_token is blank/invalid; refusing to overwrite")
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except (UnicodeError, OSError):
        raise CredsError(f"{path}: unable to read credentials file") from None
    current = _parse_creds(raw, path)
    if current.get("refresh_token") != old:
        raise CredsError("active refresh_token not present in creds file; refusing to overwrite")
    # Replace exactly the YAML value, preserving other fields and comments. A
    # token may also occur in another credential, so raw.replace(old, new) is unsafe.
    document = yaml.compose(raw, Loader=yaml.SafeLoader)
    tokens = [(key, value) for key, value in document.value if key.value == "refresh_token"]
    if len(tokens) != 1:
        raise CredsError("ambiguous refresh_token field; refusing to overwrite")
    key, node = tokens[0]
    if node.start_mark.index < key.end_mark.index:
        # YAML alias pointing at another field: round-trip to avoid changing its target.
        current["refresh_token"] = new
        updated = yaml.safe_dump(current)
    else:
        encoded = yaml.safe_dump(new, default_style='"').strip()
        updated = raw[:node.start_mark.index] + encoded + raw[node.end_mark.index:]
    expected = {**current, "refresh_token": new}
    try:
        matches = yaml.safe_load(updated) == expected
    except yaml.YAMLError:
        matches = False
    if not matches:
        # Block scalars can consume a newline and anchors may be referenced by
        # other fields. Fall back to a safe round-trip for those unusual files.
        updated = yaml.safe_dump(expected)
    _atomic_private_write(path + ".bak", raw)
    _atomic_private_write(path, updated)
    return True


def write_initial_refresh_token(refresh_token: str, path: str | None = None) -> None:
    """First-ever write of the creds file (bootstrap only). persist_rotated_token
    requires an existing token to replace, so it can't do this job -- a fresh
    install has none.

    Creates the file (and its parent dir, mode 0700, if missing) when nothing is
    there yet; the new file is born 0600. If a refresh-token-less static file
    already exists (the ENV_CREDS-in-file fallback), it is read fully, .bak'd
    (0600) like persist_rotated_token does, and rewritten with refresh_token added
    via a yaml round-trip -- comments in a hand-edited file are lost on this first
    write, acceptable since the file is machine-managed from here on. REFUSES
    (CredsError) if the file already carries a refresh_token: overwriting a live
    token is the reauth path's job (persist_rotated_token), never this one's."""
    path = path or creds_path()
    if _blank_rt(refresh_token):
        raise CredsError("new refresh_token is blank/invalid; refusing to write")
    existing: dict = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
        except (UnicodeError, OSError):
            raise CredsError(f"{path}: unable to read credentials file") from None
        existing = _parse_creds(raw, path)
        if not _blank_rt(existing.get("refresh_token")):
            raise CredsError(f"{path} already has a refresh_token — that's the reauth "
                             "flow's job, not bootstrap")
        _atomic_private_write(path + ".bak", raw)
    else:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)

    existing["refresh_token"] = refresh_token
    _atomic_private_write(path, yaml.safe_dump(existing))


def build_authorization(path: str | None = None):
    """One live refresh now; callback persists every future rotation."""
    path = path or creds_path()
    creds = load_creds(path)
    state = {"current": creds["refresh_token"]}

    def _on_refresh(oauth_tokens):
        if persist_rotated_token(state["current"], oauth_tokens.refresh_token, path):
            state["current"] = oauth_tokens.refresh_token

    grant = OAuthWebAuthCodeGrant(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        redirection_uri="http://localhost",
        token_refreshed_callback=_on_refresh,
        tenant=_validated_tenant(creds["tenant"]),
    )
    grant.request_oauth_tokens_by_refresh_token(creds["refresh_token"])
    auth_data = AuthorizationData(
        account_id=int(creds["account_id"]),
        customer_id=int(creds["customer_id"]),
        developer_token=creds["developer_token"],
        authentication=grant,
    )
    return auth_data, creds
