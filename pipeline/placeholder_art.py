"""
TCGplayer's "Image Coming Soon" picture, recognised wherever card art is
downloaded, staged or re-checked.

For a product with no photo, TCGplayer's CDN does not always answer 403: for
some it serves a grey "Image Coming Soon" JPEG with a 200. Runs before
2026-10-23 saved it as card art, and a card carrying it looks like it HAS art,
so the site showed a grey box and the imageless audit (image_path IS NULL)
never looked at the card again.

The bytes are not stable. The bucket census of 2026-11-13 found 37 objects in
four encodings of the one picture: two 1000x573 files of 32,552 B that differ
only in the JFIF density field, a 1000x573 re-encode of 32,321 B, and a
400x229 one of 10,133 B served at the same _in_1000x1000 URL. The first guard
(2026-10-23) knew only the first md5. Known encodings match by md5 (the S3
ETag of our single-part uploads); one not seen yet is caught by its shape:
the placeholder is a grayscale picture about 1.75 times as wide as it is
tall, while card art is colour, and portrait or about 1.4:1 for a landscape
card.
"""

import hashlib
import io

PLACEHOLDER_MD5S = frozenset({
    "6810409b5f6564ddf54aabf123f3819e",   # 1000x573, 32,552 B (One Piece starter decks)
    "cadb07e32b4400ddd93be6206bffc7f6",   # 1000x573, 32,552 B, JFIF density 28 dpcm
    "9695b6aef2482fcb8f5c71e1216f1504",   # 1000x573, 32,321 B
    "65a5bec7e3b6d9e5fcbf67b74dc57523",   # 400x229, 10,133 B
})
PLACEHOLDER_SIZES = frozenset({32552, 32321, 10133})
# The shape rule: grayscale, and this wide for its height (1000x573 = 1.745).
ASPECT_MIN, ASPECT_MAX = 1.70, 1.80


def has_placeholder_shape(body):
    """Grayscale and ~1.75:1. Reads the image header only (PIL opens lazily).
    False when Pillow is missing or the bytes are not an image it can read:
    the md5 list still applies."""
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(io.BytesIO(body)) as im:
            w, h = im.size
            return im.mode == "L" and h > 0 and ASPECT_MIN <= w / h <= ASPECT_MAX
    except Exception:                               # noqa: BLE001
        return False


def is_placeholder(body):
    """True when an image body is the "Image Coming Soon" picture."""
    if not body:
        return False
    return hashlib.md5(body).hexdigest() in PLACEHOLDER_MD5S or has_placeholder_shape(body)


def is_placeholder_file(path):
    """True when the file on disk is the placeholder. A missing or unreadable
    file is not one (callers check existence themselves)."""
    try:
        with open(path, "rb") as f:
            return is_placeholder(f.read())
    except OSError:
        return False
