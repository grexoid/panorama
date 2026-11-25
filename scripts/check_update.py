#!/usr/bin/env python3
"""Self-test for scripts/update_card.py (edits of existing project and use-case cards).

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
conditions: |
  - First condition.
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

        def data(path: Path) -> dict:
            return yaml.safe_load(path.read_text(encoding="utf-8"))

        # 1. one-line fields: replace, switch on a commented template line, add, bump updated_on ------------------
        update_card.update("project", "demo", {"status": "active", "version": "1.2.0", "demo_url": "https://example.org/demo", "country": "Italy"},
                           cards, today=today)
        text = project.read_text(encoding="utf-8")
        d = data(project)
        expect(d["status"] == "active" and d["version"] == "1.2.0", f"values: {d}")
        expect(d["demo_url"] == "https://example.org/demo" and "# demo_url" not in text, "commented line was not switched on")
        expect("# version" not in text and text.count("version:") == 1, "commented version line should have been replaced")
        expect(d["country"] == "Italy", "new field was not added")
        expect(str(d["updated_on"]) == "2026-11-25", f"updated_on not bumped: {d.get('updated_on')!r}")
        expect(text.startswith("# A comment that must survive."), "leading comment lost")
        expect(d["tags"] == ["one", "two"] and "Second paragraph." in d["description"], "text or list was damaged")

        # 2. long text and lists --------------------------------------------------------------------------------
        new_description = "New first paragraph.\r\n\r\n- a bullet\r\n- another bullet\r\n\r\nLast paragraph."
        update_card.update("project", "demo", {"description": new_description, "tags": "land use, Modelling, land use"}, cards, today=today)
        text = project.read_text(encoding="utf-8")
        d = data(project)
        expect(d["description"].strip() == "New first paragraph.\n\n- a bullet\n- another bullet\n\nLast paragraph.", f"description: {d['description']!r}")
        expect(d["tags"] == ["land use", "Modelling"], f"tags (deduplicated, in order): {d['tags']!r}")
        expect(d["website"] == "https://example.org/demo" and d["status"] == "active", "fields after the replaced blocks were damaged")
        expect(text.startswith("# A comment that must survive.") and "# inline comment" in text, "comments on untouched lines must survive")
        update_card.update("project", "demo", {"stack": "Python, R"}, cards, today=today)
        expect(data(project)["stack"] == ["Python", "R"], "a list that did not exist yet was not added")

        # 3. values that are the same as now change nothing --------------------------------------------------------
        before = project.read_text(encoding="utf-8")
        for same in ({"status": "active"}, {"tags": "land use,  modelling"}, {"description": new_description + "\n\n"}, {"country": "Italy"}):
            try:
                update_card.update("project", "demo", same, cards, today=dt.date(2030, 1, 1))
                failures.append(f"accepted an unchanged value: {same}")
            except update_card.UpdateError:
                pass
        expect(project.read_text(encoding="utf-8") == before, "an unchanged request modified the file (or bumped updated_on)")

        # 3b. defaults and list-valued country count as "the same" -------------------------------------------------
        (cards / "projects" / "plain.yaml").write_text(
            "name: Plain\ndescription: text\nwebsite: https://example.org\ncountry:\n  - Italy\n  - Greece\n", encoding="utf-8")
        for same in ({"status": "active"}, {"country": "Italy, Greece"}, {"country": "italy,greece"}, {"featured": "false"}):
            try:
                update_card.update("project", "plain", same, cards, today=today)
                failures.append(f"a field that already has this value (or default) was changed: {same}")
            except update_card.UpdateError:
                pass
        update_card.update("project", "plain", {"status": "beta"}, cards, today=today)
        expect(data(cards / "projects" / "plain.yaml")["status"] == "beta", "status was not added to a card that left it out")

        # 4. removal: a one-line field and a whole list ---------------------------------------------------------
        update_card.update("project", "demo", {"demo_url": "", "stack": "", "updated_on": "2026-01-02"}, cards, today=today)
        d = data(project)
        expect("demo_url" not in d and "stack" not in d, f"fields were not removed: {sorted(d)}")
        expect(str(d["updated_on"]) == "2026-01-02" and d["tags"] == ["land use", "Modelling"], "explicit updated_on overwritten, or another list removed")

        # 5. use case: texts with bullets, areas ---------------------------------------------------------------------
        update_card.update("use-case", "idea", {"country": "Italy", "affiliation": "Agency: North",
                                                "conditions": "- First condition.\n- Second condition.", "challenge": "A better problem statement."}, cards)
        d = data(use_case)
        expect(d["country"] == "Italy" and d["affiliation"] == "Agency: North", f"use case values: {d}")
        expect(d["conditions"].strip() == "- First condition.\n- Second condition." and d["challenge"].strip() == "A better problem statement.", "use-case texts")
        expect(d["proposed_ai"].strip() == "An idea." and "updated_on" not in d, "other sections must stay; use cases have no updated_on")
        update_card.update("use-case", "idea", {"main_area": "People, Skills and Ways of Working"}, cards)
        expect(data(use_case)["main_area"] == "People, Skills and Ways of Working", "main area")

        # 6. refusals leave the file exactly as it was ---------------------------------------------------------------
        def refused(kind: str, slug: str, changes: dict, why: str) -> None:
            path = cards / update_card.KINDS[kind] / f"{slug}.yaml"
            before = path.read_text(encoding="utf-8") if path.exists() else None
            try:
                update_card.update(kind, slug, changes, cards, today=today)
                failures.append(f"accepted: {why}")
            except update_card.UpdateError:
                pass
            expect((path.read_text(encoding="utf-8") if path.exists() else None) == before, f"file changed although {why}")

        refused("project", "demo", {"status": "finished"}, "an invalid status")
        refused("project", "demo", {"colour": "red"}, "an unknown field")
        refused("project", "demo", {"featured": "maybe"}, "a bad boolean")
        refused("project", "demo", {"released_on": "yesterday"}, "a bad date")
        refused("project", "demo", {"website": "", "repo_url": ""}, "removing every link (card would be invalid)")
        refused("project", "demo", {"description": ""}, "removing the description (card would be invalid)")
        refused("project", "demo", {"description": "word " * 120}, "a description beyond the length limit")
        refused("project", "../x", {"status": "active"}, "a path-like name")
        refused("project", "missing", {"status": "active"}, "an unknown card")
        refused("use-case", "idea", {"main_area": "Everything"}, "an invalid main area")
        refused("use-case", "idea", {"challenge": ""}, "removing a required section")
        refused("use-case", "idea", {"status": "active"}, "a project-only field on a use case")

        # 7. the CLI and the real build accept the result ---------------------------------------------------------
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
