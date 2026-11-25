#!/usr/bin/env python3
"""Self-test for the Update / Promote buttons: the address a button opens, and applying the edited text that comes back.

The tests that can FAIL a build work on fixture cards that this script writes itself, so they never depend on what
people have put into the real cards: a pull request that only adds a card cannot turn this test red.
Every step starts from the REAL button address (title and text are read out of it), and edits the text the way a
person does in GitHub's text box:

  1. the definitions match the build's vocabularies;
  2. for every fixture card, the pre-filled text submitted unchanged changes nothing (also with Windows line ends, and for a
     very long use case whose longest texts had to be left out of the address);
  3. editing sections changes exactly those fields; a dash removes a value; typed choices are checked; `###` inside a text is safe;
  4. a Promote text with edited description, country, tags and technologies produces a project with exactly those edits;
  5. every address stays within GitHub's length limit.

The same unchanged-text check is then run on the REAL cards, but only as a report: a card whose own pre-filled text
would change it is listed as a warning (in GitHub Actions: an annotation) and does not fail the run, unless --strict-real.

    python scripts/check_requests.py
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import card_forms  # noqa: E402
import card_request  # noqa: E402

REPO = "https://github.com/example/gallery"

LONG = " ".join(["Hydrological discharge and flood data are hard to use for non-specialists, which is why a plain-language assistant helps."] * 24)

PROJECT = """\
# A comment.
name: Demo project
quip: A short teaser
description: |
  First paragraph.

  Second paragraph - with a dash.
website: https://example.org/demo
status: beta
version: 1.0.0
country: EU,Austria
affiliation: Example Agency
contact: "Jane Doe <jane@example.org>"
tags:
  - one
  - two
stack:
  - Python
updated_on: {today}
"""

IDEA = """\
name: Demo idea
quip: A short teaser
summary: |
  Two sentences. About an idea.
main_area: AI Applications and Value Creation
country: Italy
affiliation: Example Agency
contact: Jane Doe <jane@example.org>
tags: [custom tag]
challenge: |
  A problem.

  - with a bullet
proposed_ai: |
  An idea for hydrological discharge, flood and rainfall data.

  A second paragraph.
scope: |
  - One.
  - Two.
conditions: |
  - First condition.
"""

OTHER_IDEA = IDEA.replace("Demo idea", "Other idea").replace("country: Italy", "country: [Italy, Greece]")

LONG_IDEA = f"""\
name: Long idea
main_area: People, Skills and Ways of Working
challenge: |
  {LONG}
proposed_ai: |
  {LONG}
scope: |
  {LONG}
expected_outcome: |
  {LONG}
conditions: |
  {LONG}
"""


def write_fixtures(root: Path) -> None:
    (root / "projects").mkdir(parents=True)
    (root / "use-cases").mkdir(parents=True)
    # updated_on is TODAY on purpose: a request must also cope with a card that was changed today already
    (root / "projects" / "demo-project.yaml").write_text(PROJECT.format(today=dt.date.today().isoformat()), encoding="utf-8")
    (root / "use-cases" / "demo-idea.yaml").write_text(IDEA, encoding="utf-8")
    (root / "use-cases" / "other-idea.yaml").write_text(OTHER_IDEA, encoding="utf-8")
    (root / "use-cases" / "long-idea.yaml").write_text(LONG_IDEA, encoding="utf-8")


def load_all(cards_root: Path) -> list[dict]:
    media: dict = {}
    problems = build.Problems()
    cards = []
    for folder in build.CARD_FOLDERS:
        directory = cards_root / folder
        cards += [build.load_card(f, folder, directory, media, problems) for f in sorted(directory.glob("*.yaml"))]
    cards = [c for c in cards if c]
    build.apply_auto_tags(cards, build.load_taxonomy(build.ROOT / "site" / "taxonomy.yaml"))
    build.finish_cards(cards)
    return cards


def opened(url: str) -> tuple[str, str]:
    """Title and text that GitHub shows when the button's address is opened."""
    query = parse_qs(urlparse(url).query)
    return query["title"][0], query["body"][0]


def button(card: dict, action: str = "update") -> tuple[card_forms.Form, str, str, str]:
    if action == "promote":
        form, values = card_forms.FORMS["promote-use-case"], build.promote_values(card)
    else:
        form = card_forms.FORMS["update-project" if card["type"] == "project" else "update-use-case"]
        values = build.update_values(card)
    url = card_forms.prefill_url(REPO, form, card["slug"], values)
    title, body = opened(url)
    return form, url, title, body


def with_section(body: str, form: card_forms.Form, field_id: str, value: str) -> str:
    """The text with the section of one field replaced - what a person does by typing under a heading."""
    label = next(f.label for f in form.fields if f.id == field_id)
    pattern = re.compile(rf"(?m)(^### {re.escape(label)}[ \t]*\n(?:<!--.*?-->\n)?)(.*?)(?=^### |\Z)", re.S)
    assert pattern.search(body), f"no section {label!r} in the text"
    return pattern.sub(lambda m: m.group(1) + value + "\n\n", body, count=1)


def unchanged_problems(cards: list[dict], cards_root: Path) -> list[str]:
    """For each card: submitting its own pre-filled text must change nothing. Returns descriptions of the cards that break this."""
    found = []
    for card in cards:
        form, url, title, body = button(card)
        if len(url) > card_forms.MAX_URL:
            found.append(f"{card['type']} {card['slug']}: the button address is {len(url)} characters long (limit {card_forms.MAX_URL})")
        for label, text in (("", body), (" (Windows line ends)", body.replace("\n", "\r\n"))):
            try:
                report = card_request.apply_request(title, text, cards_root)
                found.append(f"{card['type']} {card['slug']}{label}: its own pre-filled text would change the card:\n{report}")
            except card_request.RequestError as exc:
                if "nothing changed" not in str(exc):
                    found.append(f"{card['type']} {card['slug']}{label}: unexpected refusal: {exc}")
    return found


def main() -> int:
    strict_real = "--strict-real" in sys.argv
    failures: list[str] = []

    def expect(ok: bool, message: str) -> None:
        if not ok:
            failures.append(message)

    # 1. definitions ------------------------------------------------------------------------------------------------
    expect(card_forms.MAIN_AREAS == tuple(a[1] for a in build.AREAS), "card_forms.MAIN_AREAS differs from build.AREAS")
    expect(set(card_forms.STATUSES) == set(build.STATUSES), "card_forms.STATUSES differs from build.STATUSES")
    for form in card_forms.FORMS.values():
        labels = [f.label.casefold() for f in form.fields]
        expect(len(labels) == len(set(labels)), f"{form.key}: headings are not unique")
        expect(not any(line.lstrip().startswith("###") for line in form.intro.splitlines()), f"{form.key}: the intro must not contain a ### heading")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "cards"
        write_fixtures(root)
        cards = load_all(root)
        by_slug = {c["slug"]: c for c in cards}
        expect(len(cards) == 4, f"fixtures did not load: {sorted(by_slug)}")

        # 2. unchanged text changes nothing ----------------------------------------------------------------------------
        failures += unchanged_problems(cards, root)
        long_body = button(by_slug["long-idea"])[3]
        expect(card_forms.TOO_LONG in long_body, "the very long use case should have had texts left out of the address")

        # 3. editing ----------------------------------------------------------------------------------------------------
        project = by_slug["demo-project"]
        path = root / "projects" / "demo-project.yaml"
        form, _, title, body = button(project)
        before = yaml.safe_load(path.read_text(encoding="utf-8"))

        edited = with_section(with_section(body, form, "version", "9.9.9"), form, "tags", "alpha, beta")
        edited = with_section(edited, form, "description", "First paragraph.\n\n### Notes\n\n- a bullet\n- another\n\nLast paragraph.")
        report = card_request.apply_request(title, edited, root)
        after = yaml.safe_load(path.read_text(encoding="utf-8"))
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)} - {"updated_on"}   # (already today's date: may stay)
        expect(changed == {"version", "tags", "description"}, f"changed fields: {sorted(changed)}\n{report}")
        expect(after["tags"] == ["alpha", "beta"] and after["version"] == "9.9.9", f"new values: {after.get('tags')!r}, {after.get('version')!r}")
        expect("### Notes" in after["description"] and "- a bullet" in after["description"], "a ### inside a long text cut it short")
        expect(after["country"] == before["country"], f"an untouched country was rewritten: {after['country']!r}")

        edited = with_section(with_section(body, form, "website", "-"), form, "status", "ACTIVE")
        card_request.apply_request(title, with_section(edited, form, "repo_url", "https://example.org/code"), root)
        after = yaml.safe_load(path.read_text(encoding="utf-8"))
        expect("website" not in after and after.get("repo_url") == "https://example.org/code", "a dash did not remove the field")
        expect(after.get("status") == "active", f"a typed choice (any case) was not applied: {after.get('status')!r}")
        try:
            card_request.apply_request(title, with_section(body, form, "status", "finished"), root)
            failures.append("an invalid choice was accepted")
        except card_request.RequestError as exc:
            expect("Use one of" in str(exc) and "maintenance" in str(exc), f"the refusal does not list the options: {exc}")

        # 4. promote: edited description, country, tags and technologies must all arrive --------------------------------
        idea = by_slug["demo-idea"]
        form, url, title, body = button(idea, "promote")
        expect(len(url) <= card_forms.MAX_URL, f"promote address is {len(url)} characters long")
        edited = body
        for field_id, value in (("website", "https://example.org/promoted"), ("description", "My own description.\n\nSecond paragraph."),
                                ("country", "Italy, Slovenia"), ("tags", "tag one, tag two"), ("stack", "Python, R"),
                                ("name", "A new name"), ("status", "active")):
            edited = with_section(edited, form, field_id, value)
        report = card_request.apply_request(title, edited, root)
        new = root / "projects" / "demo-idea.yaml"
        expect(new.is_file() and not (root / "use-cases" / "demo-idea.yaml").exists(), f"promotion did not move the card:\n{report}")
        promoted = yaml.safe_load(new.read_text(encoding="utf-8")) if new.is_file() else {}
        expect(promoted.get("description", "").strip() == "My own description.\n\nSecond paragraph.", f"description: {promoted.get('description')!r}")
        expect(promoted.get("country") == "Italy, Slovenia", f"country: {promoted.get('country')!r}")
        expect(promoted.get("tags") == ["tag one", "tag two"] and promoted.get("stack") == ["Python", "R"], f"tags/stack: {promoted.get('tags')!r} {promoted.get('stack')!r}")
        expect(promoted.get("name") == "A new name" and promoted.get("status") == "active", f"name/status: {promoted.get('name')!r} {promoted.get('status')!r}")
        expect(promoted.get("website") == "https://example.org/promoted", f"website: {promoted.get('website')!r}")

        # promoting the pre-filled text as it is (plus a website) takes the use case's content over, including its tags
        other = by_slug["other-idea"]
        form, _, title, body = button(other, "promote")
        card_request.apply_request(title, with_section(body, form, "website", "https://example.org/other"), root)
        promoted = yaml.safe_load((root / "projects" / "other-idea.yaml").read_text(encoding="utf-8"))
        expect(sorted(t.casefold() for t in promoted.get("tags", [])) == sorted(t["label"].casefold() for t in other["tags"]),
               "the use case's tags (including the automatic ones) were not written into the project")
        expect(promoted.get("status") == "beta" and promoted.get("name") == other["name"], "default status or name")
        expect(promoted.get("country") == "Italy, Greece", f"a list-valued country was not carried over: {promoted.get('country')!r}")

        # a title that is not a request and an unknown card are refused with an explanation
        promote_empty = card_forms.request_body(card_forms.FORMS["promote-use-case"], {"card": "no-such-card"})
        for title, why in (("Something else", "unknown title"), (card_forms.FORMS["promote-use-case"].title_prefix + "no-such-card", "unknown card")):
            try:
                card_request.apply_request(title, promote_empty, root)
                failures.append(f"accepted: {why}")
            except card_request.RequestError:
                pass

        # the cards are still valid for the real build after all those requests
        built = subprocess.run([sys.executable, str(HERE / "build.py"), "--strict", "--cards", str(root), "--out", str(Path(tmp) / "out")],
                               capture_output=True, text=True)
        expect(built.returncode == 0, f"the build rejects the cards after the requests:\n{built.stdout[-800:]}{built.stderr[-400:]}")

        # the REAL cards: a report, not a gate ---------------------------------------------------------------------------
        real_root = Path(tmp) / "real"
        shutil.copytree(build.ROOT / "cards", real_root)
        warnings = unchanged_problems(load_all(real_root), real_root)

    if warnings:
        print(f"\nNote: {len(warnings)} real card(s) would be changed by their own pre-filled Update text. "
              "The cards are fine; the Update tool should treat these values as unchanged:")
        for w in warnings:
            first = w.splitlines()[0]
            print(f"  WARNING {first}")
            print(f"::warning title=Update text differs from the card::{first}")
    if failures or (strict_real and warnings):
        print("Card request self-test FAILED:\n  - " + "\n  - ".join(failures + (warnings if strict_real else [])))
        return 1
    print("Card request self-test passed." + (f" ({len(warnings)} warning(s) about real cards)" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
