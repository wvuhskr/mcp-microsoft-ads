"""Media library writes: image upload for image extensions / PMax asset groups."""
import base64
import hashlib
import io
import os
import stat

from PIL import Image, UnidentifiedImageError

from .. import client, rails
from ..app import mcp

# Media.MediaType is an aspect-ratio label, NOT a media kind (Media.Type covers that,
# always "Image" here) — live-observed values in this account's media library
# (GetMediaMetaDataByAccountId, 124 items). The WSDL declares this field as a bare
# xs:string with no enum, so nothing beyond what's actually been seen is accepted.
MEDIA_TYPES = {"Image1x1", "Image191x100", "GenericImage", "Image4x1"}


# Local resource limits, not claims about Microsoft's account-specific limits.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


def _read_image(path: str) -> bytes:
    try:
        # Nonblocking open avoids hanging on a FIFO; fstat checks the opened file,
        # not a path that can be swapped between a separate stat and open.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise ValueError(f"{path} not found") from None
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("image must be a regular file")
        if info.st_size > MAX_IMAGE_BYTES:
            raise ValueError("image exceeds the local 5 MiB upload limit")
        data = f.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds the local 5 MiB upload limit")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ValueError("image must be PNG or JPEG")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("image exceeds the local 20 million pixel limit")
            if getattr(image, "is_animated", False):
                raise ValueError("animated images are not supported")
            image.verify()
        # verify() checks structure; load() also detects truncated image data.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError("file is not a valid PNG or JPEG image") from None
    return data


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
    PMax asset groups). Returns media id on apply. Valid PNG/JPEG only, at most
    5 MiB and 20 million pixels (local safety limits). The file must remain
    byte-identical between preview and apply; otherwise create a new draft.

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
    rails.check_writes_enabled()  # refuse before reading any local file
    path = os.path.abspath(os.path.expanduser(file_path))
    if media_type not in MEDIA_TYPES:
        raise ValueError(f"media_type must be one of {sorted(MEDIA_TYPES)}, got '{media_type}'")
    data = _read_image(path)
    size_kb = len(data) // 1024
    digest = hashlib.sha256(data).hexdigest()

    def apply():
        data = _read_image(path)
        if hashlib.sha256(data).hexdigest() != digest:
            raise rails.RailViolation("image changed since preview; create a new draft")
        svc = client.svc("CampaignManagementService")
        img = client.blank(svc, "Image")  # Image extends Media
        img.Type = "Image"  # discriminator (Media.Type, inherited)
        img.MediaType = media_type
        img.Data = base64.b64encode(data).decode()
        arr = svc.factory.create("ArrayOfMedia")
        arr.Media = [img]
        resp = svc.AddMedia(AccountId=client.account_id(), Media=arr)
        return {"media_ids": _media_ids(resp), "partial_errors": client.partial_errors(resp)}

    return rails.create_draft("upload_image_asset",
                              {"file": path, "size_kb": size_kb, "media_type": media_type,
                               "sha256": digest}, apply)
