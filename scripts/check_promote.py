#!/usr/bin/env python3
"""Self-test for scripts/promote_card.py (use case -> project).

Works in a temporary cards folder, so nothing in the repository is touched:
  1. a use case with an image is promoted; the project card is valid for the real build, images moved, tags kept;
  2. promoting without any website/repository address fails, exits non-zero and leaves everything as it was.

    python scripts/check_promote.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import promote_card  # noqa: E402

USE_CASE = """\
name: Flood watch
quip: Rivers, explained
summary: A short summary of the idea.
main_area: AI Applications and Value Creation
challenge: |
  Hydrological discharge and flood data are hard to use for non-specialists.
proposed_ai: |
  An assistant answers questions about hydrological discharge and flood levels.

  A second paragraph about the approach.
conditions: |
  - Data must be reliable.
affiliation: Example Agency
country: Italy
contact: Jane Doe <jane@example.org>
tags:
  - custom tag
images:
  - file: images/flood-watch/overview.png
    caption: Overview
"""


def main() -> int:
    failures: list[str] = []

    def expect(ok: bool, message: str) -> None:
        if not ok:
            failures.append(message)

    with tempfile.TemporaryDirectory() as tmp:
        cards = Path(tmp) / "cards"
        use_cases = cards / "use-cases"
        (use_cases / "images" / "flood-watch").mkdir(parents=True)
        (use_cases / "flood-watch.yaml").write_text(USE_CASE, encoding="utf-8")
        (use_cases / "images" / "flood-watch" / "overview.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
        (use_cases / "other-idea.yaml").write_text(USE_CASE.split("images:")[0].replace("Flood watch", "Other idea"), encoding="utf-8")
        (use_cases / "images" / "flood-watch" / "copy.png").write_bytes(b"x")   # not referenced by the card: must stay where it is

        # 1. happy path --------------------------------------------------------------------------------------
        target, notes = promote_card.promote("flood-watch", cards, {"website": "https://example.org/flood"}, "beta")
        card = yaml.safe_load(target.read_text(encoding="utf-8"))
        expect(target == cards / "projects" / "flood-watch.yaml", f"target path: {target}")
        expect(not (use_cases / "flood-watch.yaml").exists(), "the use case file was not removed")
        expect((cards / "projects" / "images" / "flood-watch" / "overview.png").is_file(), "image was not moved to the project")
        expect(not (use_cases / "images" / "flood-watch" / "overview.png").exists(), "image still in the use case folder")
        expect((use_cases / "images" / "flood-watch" / "copy.png").exists(), "an unrelated file was touched")
        expect(card.get("name") == "Flood watch" and card.get("quip") == "Rivers, explained", f"name/quip: {card.get('name')!r}")
        expect(card.get("status") == "beta" and card.get("website") == "https://example.org/flood", f"status/website: {card}")
        expect(card.get("screenshot_url") == "images/flood-watch/overview.png", f"screenshot_url: {card.get('screenshot_url')!r}")
        expect(card.get("description", "").startswith("A short summary of the idea."), f"description: {card.get('description')!r}")
        expect("An assistant answers questions" in card.get("description", ""), "description lacks the start of proposed_ai")
        expect("Data must be reliable" not in card.get("description", ""), "long sections should not leak into the description")
        tags = card.get("tags") or []
        expect(tags[:1] == ["custom tag"] and len(tags) > 1, f"explicit tag first, automatic ones frozen in: {tags!r}")
        expect(card.get("contact") == "Jane Doe <jane@example.org>" and card.get("affiliation") == "Example Agency", "contact/affiliation lost")
        expect(card.get("country") == "Italy", f"country lost: {card.get('country')!r}")
        expect(len(notes) >= 3, "no notes for the user")

        # 2. the result must be a valid card for the real build ---------------------------------------------
        built = subprocess.run([sys.executable, str(HERE / "build.py"), "--strict", "--cards", str(cards), "--out", str(Path(tmp) / "out")],
                               capture_output=True, text=True)
        expect(built.returncode == 0, f"the build rejects the promoted project:\n{built.stdout}{built.stderr}")

        # 3. failure path: no website/repository -> nothing changes ----------------------------------------
        before = sorted(p.relative_to(cards).as_posix() for p in cards.rglob("*") if p.is_file())
        done = subprocess.run([sys.executable, str(HERE / "promote_card.py"), "other-idea", "--cards", str(cards)], capture_output=True, text=True)
        after = sorted(p.relative_to(cards).as_posix() for p in cards.rglob("*") if p.is_file())
        expect(done.returncode != 0, "promoting without any address should fail")
        expect(before == after, f"a failed promotion changed files:\n  before {before}\n  after  {after}")
        expect("other-idea" in sorted(p.stem for p in use_cases.glob("*.yaml")), "the use case disappeared")

        # 4. bad names are refused --------------------------------------------------------------------------
        try:
            promote_card.promote("../etc", cards, {"website": "https://example.org"}, "beta")
            expect(False, "a path-like name was accepted")
        except promote_card.PromoteError:
            pass

    if failures:
        print("Promote self-test FAILED:\n  - " + "\n  - ".join(failures))
        return 1
    print("Promote self-test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
