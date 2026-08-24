"""Media library writes: image upload for image extensions / PMax asset groups."""
import base64
import os

from .. import client, rails
from ..app import mcp

# Media.MediaType is an aspect-ratio label, NOT a media kind (Media.Type covers that,
# always "Image" here) — live-observed values in this account's media library
# (GetMediaMetaDataByAccountId, 124 items). The WSDL declares this field as a bare
# xs:string with no enum, so nothing beyond what's actually been seen is accepted.
MEDIA_TYPES = {"Image1x1", "Image191x100", "GenericImage", "Image4x1"}


def _media_ids(resp) -> list[int]:
    """AddMediaResponse: live-observed (Task 25 smoke) the response IS the ArrayOflong
    directly — resp.long carries the id(s), MediaIds is absent. AddMediaResponse
    declares exactly ONE child (MediaIds), so per the suds ONE-CHILD RULE
    (client.partial_errors docstring) that wrapper can never survive — no fallback
    branch to keep. client.long_ids also skips NIL entries marking failed batch items."""
    return client.long_ids(resp)


@mcp.tool()
def upload_image_asset(file_path: str, media_type: str) -> dict:
    """Draft an image upload to the account media library (for image extensions /
    PMax asset groups). Returns media id on apply. PNG/JPEG.

    media_type is the image's aspect ratio, one of:
    - Image1x1: square (e.g. 1200x1200)
    - Image191x100: 1.91:1 landscape (e.g. 1200x628)
    - GenericImage
    - Image4x1

    Live-verified 2026-07-30, after fixing two live faults (MediaType is an
    aspect-ratio label; the AddMedia response comes back unwrapped — both noted
    above). AddMedia also dedupes byte-identical images: re-uploading returns the
    existing media id.
    """
    path = os.path.expanduser(file_path)
    if not os.path.exists(path):
        raise ValueError(f"{path} not found")
    if media_type not in MEDIA_TYPES:
        raise ValueError(f"media_type must be one of {sorted(MEDIA_TYPES)}, got '{media_type}'")
    size_kb = os.path.getsize(path) // 1024

    def apply():
        svc = client.svc("CampaignManagementService")
        img = client.blank(svc, "Image")  # Image extends Media
        img.Type = "Image"  # discriminator (Media.Type, inherited)
        img.MediaType = media_type
        with open(path, "rb") as f:
            img.Data = base64.b64encode(f.read()).decode()
        arr = svc.factory.create("ArrayOfMedia")
        arr.Media = [img]
        resp = svc.AddMedia(AccountId=client.account_id(), Media=arr)
        return {"media_ids": _media_ids(resp), "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("upload_image_asset",
                              {"file": path, "size_kb": size_kb, "media_type": media_type}, apply)
