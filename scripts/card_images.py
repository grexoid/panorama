#!/usr/bin/env python3
"""Pictures that come with a card request: files dragged into the text of the issue that an Update button opened.

When a picture is dragged into (or pasted into) a GitHub issue, GitHub uploads it and writes its address into the text:
`![name](https://github.com/user-attachments/assets/...)`. The "Card requests" workflow finds such addresses in the
screenshot, logo and images sections, downloads the pictures and stores them next to the card
(cards/<kind>/images/<card-name>/), so they become part of the pull request like any other file.

Only pictures from GitHub's own attachment addresses are downloaded, only png, jpg, gif and webp files are accepted
(their content is checked, not just their name; svg is left out because it can carry scripts) and each file must stay
within the size limit of the site build.
"""
from __future__ import annotations

import hashlib
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402

MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^\s<>)]+)>?[^)]*\)")
HTML_IMAGE = re.compile(r"<img\b[^>]*>", re.I)
BARE_ATTACHMENT = re.compile(r"https://github\.com/user-attachments/(?:assets|files)/[^\s)\]>\"']+")
TIMEOUT_SECONDS = 30


class ImageError(Exception):
    """A picture cannot be used; the message says why (it is shown to the person who made the request)."""


def allowed_address(url: str) -> bool:
    """GitHub's own attachment addresses - nothing else is ever downloaded."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        return False
    return (host == "github.com" and parts.path.startswith("/user-attachments/")) or host.endswith(".githubusercontent.com")


def find_attachments(text: str) -> list[tuple[str, str]]:
    """(alt text, address) of every picture in `text` - Markdown pictures, <img> tags and bare attachment addresses."""
    found: list[tuple[str, str]] = []
    for alt, url in MARKDOWN_IMAGE.findall(text):
        found.append((alt, url))
    for tag in HTML_IMAGE.findall(text):
        src = re.search(r'src\s*=\s*"([^"]+)"', tag, re.I)
        alt = re.search(r'alt\s*=\s*"([^"]*)"', tag, re.I)
        if src:
            found.append((alt.group(1) if alt else "", src.group(1)))
    remainder = HTML_IMAGE.sub(" ", MARKDOWN_IMAGE.sub(" ", text))
    found += [("", url) for url in BARE_ATTACHMENT.findall(remainder)]
    unique: dict[str, tuple[str, str]] = {}
    for alt, url in found:
        unique.setdefault(url, (alt, url))
    return list(unique.values())


def without_attachments(text: str) -> str:
    """`text` with the pictures taken out - what the person typed besides them."""
    return "\n".join(line.strip() for line in BARE_ATTACHMENT.sub(" ", HTML_IMAGE.sub(" ", MARKDOWN_IMAGE.sub(" ", text))).splitlines() if line.strip())


def caption_from(alt: str) -> str:
    """A caption the person wrote as the picture's text. GitHub fills in the file name, which is not a caption."""
    alt = alt.strip()
    return "" if re.fullmatch(r"[\w.\-]+", alt) or alt.lower() in ("image", "img") else " ".join(alt.split())


def download(url: str) -> bytes:
    """The bytes behind a GitHub attachment address (no credentials are sent; attachments of a public repository are public)."""
    if not allowed_address(url):
        raise ImageError(f"Pictures are only taken from files attached to the issue (drag them into the text), not from {url}. "
                         "To use a picture from elsewhere, type its plain web address instead of a picture.")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "panorama-card-requests"}), timeout=TIMEOUT_SECONDS) as response:
            if not allowed_address(response.geturl()):
                raise ImageError("The picture's address led somewhere that is not a GitHub attachment.")
            data = response.read(build.MAX_IMAGE_BYTES + 1)
    except OSError as exc:
        raise ImageError(f"The picture could not be downloaded ({exc}). Is it still attached to the issue?") from exc
    return data


def suffix_of(data: bytes) -> str:
    """The file ending for the picture's real content: png, jpg, gif or webp - anything else is refused."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise ImageError("Only png, jpg, gif and webp pictures can be added this way (a PDF, svg or other file cannot).")


def save(cards_root: Path, kind_folder: str, slug: str, stem: str, data: bytes) -> str:
    """Store a picture as cards/<kind_folder>/images/<slug>/<stem>-<hash>.<ext>; returns the path relative to cards/<kind_folder>/.

    The name contains a short fingerprint of the content: a new picture never has the name of an old one, so a
    card that points at the old file keeps working until its field is changed."""
    if not data:
        raise ImageError("The picture is empty.")
    if len(data) > build.MAX_IMAGE_BYTES:
        raise ImageError(f"The picture is larger than {build.MAX_IMAGE_BYTES / 1_000_000:.1f} MB. Please make it smaller (for example by saving it as a jpg).")
    suffix = suffix_of(data)
    rel = f"images/{slug}/{stem}-{hashlib.sha256(data).hexdigest()[:8]}{suffix}"
    target = cards_root / kind_folder / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return rel
