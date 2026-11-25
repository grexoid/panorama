#!/usr/bin/env python3
"""Check that a password-protected build leaks nothing readable.

    GALLERY_PASSWORD=test python scripts/build.py --out /tmp/protected
    python scripts/check_protected.py /tmp/protected

Fails (exit status 1) when
  - a page or JSON file contains the name of a card, or text from one,
  - an image or other media file is published unencrypted,
  - projects.json is present (it holds the full text of every card).
Run by the "Validate cards" workflow, so a change that would publish readable content is caught in the pull request.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1])

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
