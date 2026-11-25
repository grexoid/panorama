#!/usr/bin/env python3
"""Self-test for scripts/update_card.py (quick edits of existing project and use-case cards).

Runs in a temporary cards folder, so nothing in the repository is touched.

    python scripts/check_update.py
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_card  # noqa: E402

PROJECT = """\
# A comment that must survive.
name: Demo project
description: |
  First line.

  Second paragraph.
website: https://example.org/demo   # inline comment
status: beta
tags:
  - one
  - two

# demo_url: https://example.org/demo-old
# version: 1.0.0
"""

USE_CASE = """\
name: Demo idea
main_area: AI Applications and Value Creation
challenge: |
  A problem.
proposed_ai: |
  An idea.
"""


def main() -> int:
    failures: list[str] = []

    def expect(ok: bool, message: str) -> None:
        if not ok:
            failures.append(message)

    today = dt.date(2026, 11, 25)
    with tempfile.TemporaryDirectory() as tmp:
        cards = Path(tmp) / "cards"
        (cards / "projects").mkdir(parents=True)
        (cards / "use-cases").mkdir(parents=True)
        project = cards / "projects" / "demo.yaml"
        use_case = cards / "use-cases" / "idea.yaml"
        project.write_text(PROJECT, encoding="utf-8")
        use_case.write_text(USE_CASE, encoding="utf-8")

        # 1. project: replace, switch on a commented template line, add a new field, auto-bump updated_on -----
        update_card.update("project", "demo", {"status": "active", "version": "1.2.0", "demo_url": "https://example.org/demo", "country": "Italy"},
                           cards, today=today)
        text = project.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        expect(data["status"] == "active" and data["version"] == "1.2.0", f"values: {data}")
        expect(data["demo_url"] == "https://example.org/demo" and "# demo_url" not in text, "commented line was not switched on")
        expect("# version" not in text and text.count("version:") == 1, "commented version line should have been replaced")
        expect(data["country"] == "Italy", "new field was not added")
        expect(str(data["updated_on"]) == "2026-11-25", f"updated_on not bumped: {data.get('updated_on')!r}")
        expect(text.startswith("# A comment that must survive."), "leading comment lost")
        expect("Second paragraph." in text and data["tags"] == ["one", "two"], "text or list was damaged")

        # 2. empty value removes a field; an explicit updated_on is respected ---------------------------------
        update_card.update("project", "demo", {"demo_url": "", "updated_on": "2026-01-02"}, cards, today=today)
        data = yaml.safe_load(project.read_text(encoding="utf-8"))
        expect("demo_url" not in data, "field was not removed")
        expect(str(data["updated_on"]) == "2026-01-02", f"explicit updated_on overwritten: {data['updated_on']!r}")

        # 3. use case ----------------------------------------------------------------------------------------
        update_card.update("use-case", "idea", {"country": "Italy", "affiliation": "Agency: North"}, cards)
        data = yaml.safe_load(use_case.read_text(encoding="utf-8"))
        expect(data["country"] == "Italy" and data["affiliation"] == "Agency: North", f"use case values: {data}")
        expect("updated_on" not in data, "use cases have no updated_on")

        # 4. refusals leave the file exactly as it was -------------------------------------------------------
        def refused(kind: str, slug: str, changes: dict, why: str) -> None:
            path = cards / update_card.KINDS[kind] / f"{slug}.yaml"
            before = path.read_text(encoding="utf-8") if path.exists() else None
            try:
                update_card.update(kind, slug, changes, cards, today=today)
                failures.append(f"accepted: {why}")
            except update_card.UpdateError:
                pass
            after = path.read_text(encoding="utf-8") if path.exists() else None
            expect(before == after, f"file changed although {why}")

        refused("project", "demo", {"status": "finished"}, "an invalid status")
        refused("project", "demo", {"description": "x"}, "a multi-line field")
        refused("project", "demo", {"tags": "x"}, "a list field")
        refused("project", "demo", {"colour": "red"}, "an unknown field")
        refused("project", "demo", {"featured": "maybe"}, "a bad boolean")
        refused("project", "demo", {"released_on": "yesterday"}, "a bad date")
        refused("project", "demo", {"website": "", "repo_url": ""}, "removing every link (card would be invalid)")
        refused("project", "../x", {"status": "active"}, "a path-like name")
        refused("project", "missing", {"status": "active"}, "an unknown card")
        refused("use-case", "idea", {"main_area": "Everything"}, "an invalid main area")
        refused("use-case", "idea", {"status": "active"}, "a project-only field on a use case")

        # 5. the CLI and the real build accept the result ---------------------------------------------------
        done = subprocess.run([sys.executable, str(HERE / "update_card.py"), "project", "demo", "version=2.0.0", "--cards", str(cards)],
                              capture_output=True, text=True)
        expect(done.returncode == 0, f"CLI failed: {done.stdout}{done.stderr}")
        bad = subprocess.run([sys.executable, str(HERE / "update_card.py"), "project", "demo", "--cards", str(cards)], capture_output=True, text=True)
        expect(bad.returncode != 0, "CLI should fail without any change")
        built = subprocess.run([sys.executable, str(HERE / "build.py"), "--strict", "--cards", str(cards), "--out", str(Path(tmp) / "out")],
                               capture_output=True, text=True)
        expect(built.returncode == 0, f"the build rejects the updated cards:\n{built.stdout}{built.stderr}")

    if failures:
        print("Update self-test FAILED:\n  - " + "\n  - ".join(failures))
        return 1
    print("Update self-test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
