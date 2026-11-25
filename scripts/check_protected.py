#!/usr/bin/env python3
"""Check that a password-protected build leaks nothing readable.

    GALLERY_PASSWORD=test python scripts/build.py --out /tmp/protected
    python scripts/check_protected.py /tmp/protected

Fails (exit status 1) when
  - a page or JSON file contains the name of a card, or text from one,
  - an image or other media file is published unencrypted,
  - projects.json is present (it holds the full text of every card).
Run by the "Validate cards" workflow, so a change that would publish readable content is caught in the pull request.

A second build of the same site can be compared with --same-key-as:

    python scripts/check_protected.py /tmp/build-2 --same-key-as /tmp/build-1

With GALLERY_PASSWORD set (the password both builds used), it checks that files of the FIRST build can be decrypted with
the key of the SECOND. That is what keeps a browser working that still holds an older encrypted image or page in its
cache after a new deployment (GitHub Pages lets browsers cache files for 10 minutes).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def gate_payload(site: Path) -> dict:
    page = (site / "index.html").read_text(encoding="utf-8")
    return json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>', page, re.S).group(1))


def cross_build_problems(new: Path, old: Path) -> list[str]:
    """The key of build `new` must open the files of build `old`."""
    a, b = gate_payload(old), gate_payload(new)
    if a["salt"] != b["salt"]:
        return ["the two builds use different keys (salt differs): files cached from the older build would not decrypt"]
    password = os.environ.get("GALLERY_PASSWORD")
    if not password:
        return []
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = AESGCM(hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(b["salt"]), b["iter"], 32))
    blobs = [("index.html", base64.b64decode(a["data"]))]
    blobs += [(p.relative_to(old).as_posix(), p.read_bytes()) for p in sorted((old / "media").rglob("*.enc"))]
    problems = []
    for name, blob in blobs:
        try:
            key.decrypt(blob[:12], blob[12:], None)
        except Exception:
            problems.append(f"{name} of the older build cannot be decrypted with the newer build's key")
    return problems or ([] if blobs else ["nothing to compare"])


def main() -> int:
    args = sys.argv[1:]
    other = None
    if "--same-key-as" in args:
        i = args.index("--same-key-as")
        other = Path(args[i + 1]) if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    if len(args) != 1 or ("--same-key-as" in sys.argv and other is None):
        print(__doc__)
        return 2
    out = Path(args[0])
    if other is not None:
        problems = cross_build_problems(out, other)
        if problems:
            print("Cross-build check FAILED:\n  " + "\n  ".join(problems))
            return 1
        print("Cross-build check passed: files of the older build open with the newer build's key.")

    # Words that must not appear: every card's name and the first sentence of its main text.
    secrets: list[str] = []
    for card_file in sorted((ROOT / "cards").glob("*/*.y*ml")):
        data = yaml.safe_load(card_file.read_text(encoding="utf-8")) or {}
        for field in ("name", "challenge", "description"):
            text = " ".join(str(data.get(field) or "").split())
            if text:
                secrets.append(text if field == "name" else text[:60])

    problems = []
    if not secrets:
        problems.append("no cards found to check against - is this the repository root?")
    for page in out.rglob("*"):
        if page.is_file() and page.suffix in (".html", ".json", ".yaml", ".yml", ".txt"):
            text = " ".join(page.read_text(encoding="utf-8", errors="replace").split())
            problems += [f"{page.relative_to(out)} contains readable text: {s[:50]!r}" for s in secrets if s in text]
    if (out / "projects.json").exists():
        problems.append("projects.json is published (it contains every card in plain text)")
    media = out / "media"
    if media.is_dir():
        problems += [f"{p.relative_to(out)} is published without encryption" for p in media.rglob("*") if p.is_file() and p.suffix != ".enc"]
    if not (out / "index.html").is_file() or "id=\"payload\"" not in (out / "index.html").read_text(encoding="utf-8"):
        problems.append("index.html is not a protected gate page")

    if problems:
        print("Protection check FAILED:\n  " + "\n  ".join(problems))
        return 1
    print(f"Protection check passed: {len(secrets)} card texts searched for, nothing readable in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
