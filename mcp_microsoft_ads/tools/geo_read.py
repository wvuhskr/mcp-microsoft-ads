import csv
import io
import os
import time

import requests

from .. import client
from ..app import mcp

CACHE = os.path.expanduser("~/.mcp-microsoft-ads/geolocations.csv")
MAX_AGE_S = 30 * 86400


def _search_rows(raw: str, query: str) -> list[dict]:
    # live-probed: real Bing Display Name uses "|" as the component separator
    # (e.g. "Springfield|Illinois|United States"), not ", " as the brief's fixture assumed.
    # Normalize both sides so natural "City, State" queries still match.
    q = query.lower().replace("|", ", ")
    out = []
    for row in csv.DictReader(io.StringIO(raw)):
        name = row.get("Bing Display Name", "").replace("|", ", ")
        if q in name.lower():
            out.append({"id": int(row["Location Id"]), "name": name,
                        "type": row.get("Location Type"), "status": row.get("Status")})
    return out[:50]


def _ensure_cache() -> str:
    if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < MAX_AGE_S:
        with open(CACHE) as f:
            return f.read()
    svc = client.svc("CampaignManagementService")
    r = svc.GetGeoLocationsFileUrl(Version="2.0", LanguageLocale="en")
    raw = requests.get(r.FileUrl, timeout=120).text
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        f.write(raw)
    return raw


@mcp.tool()
def search_geo_targets(query: str) -> dict:
    """Search targetable/excludable locations by name (MS ships a file, not a query API;
    cached locally 30 days). Returns location ids for geo write tools."""
    return {"matches": _search_rows(_ensure_cache(), query)}
