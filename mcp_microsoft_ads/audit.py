import json
import os
import time

AUDIT_PATH = os.path.expanduser("~/.mcp-microsoft-ads/audit.jsonl")


def audit_path() -> str:
    return os.environ.get("MS_ADS_AUDIT_PATH") or AUDIT_PATH


def log_event(tool: str, phase: str, data: dict, path: str | None = None) -> dict:
    if path is None:
        path = audit_path()
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": tool, "phase": phase, **data}
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return entry
