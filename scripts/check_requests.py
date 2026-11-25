#!/usr/bin/env python3
"""Self-test for the Update / Promote buttons: the address a button opens, and applying the edited text that comes back.

Works on a temporary copy of the repository's real cards, so nothing is changed. Every step starts from the REAL button
address (title and text are read out of it), and edits the text the way a person does in GitHub's text box.

  1. the definitions match the build's vocabularies;
  2. for EVERY real card, the pre-filled text submitted unchanged changes nothing (also when long texts had to be left out);
  3. editing boxes changes exactly those fields; a dash removes a value; typed choices are checked; `###` inside a text is safe;
  4. a Promote text with edited description, country, tags and technologies produces a project with exactly those edits;
  5. every address stays within GitHub's length limit.

    python scripts/check_requests.py
"""
from __future__ import annotations

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


def with_section(body: str, form: card_forms.Form, field_id: str, value: str) -> str:
    """The text with the section of one field replaced - what a person does by typing under a heading."""
    label = next(f.label for f in form.fields if f.id == field_id)
    pattern = re.compile(rf"(?m)(^### {re.escape(label)}[ \t]*\n(?:<!--.*?-->\n)?)(.*?)(?=^### |\Z)", re.S)
    assert pattern.search(body), f"no section {label!r} in the text"
    return pattern.sub(lambda m: m.group(1) + value + "\n\n", body, count=1)


def main() -> int:
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
        cards_root = Path(tmp) / "cards"
        shutil.copytree(build.ROOT / "cards", cards_root)
        cards = load_all(cards_root)
        expect(len(cards) > 2, "no real cards found to test with")
        omitted_somewhere = 0

        # 2. every real card: the button's text, submitted unchanged, changes nothing ------------------------------------
        for card in cards:
            form = card_forms.FORMS["update-project" if card["type"] == "project" else "update-use-case"]
            url = card_forms.prefill_url(REPO, form, card["slug"], build.update_values(card))
            title, body = opened(url)
            expect(len(url) <= card_forms.MAX_URL, f"{card['slug']}: the address is {len(url)} characters long (limit {card_forms.MAX_URL})")
            expect(title == form.title_prefix + card["slug"], f"{card['slug']}: title {title!r}")
            omitted_somewhere += body.count(card_forms.TOO_LONG)
            try:
                report = card_request.apply_request(title, body, cards_root)
                failures.append(f"{card['type']} {card['slug']}: the unchanged text changed the card:\n{report}")
            except card_request.RequestError as exc:
                expect("nothing changed" in str(exc), f"{card['type']} {card['slug']}: unexpected refusal: {exc}")
            # the same with the line ends GitHub's text box uses on Windows
            try:
                card_request.apply_request(title, body.replace("\n", "\r\n"), cards_root)
                failures.append(f"{card['slug']}: CRLF line ends changed the card")
            except card_request.RequestError:
                pass

        # 3. editing ----------------------------------------------------------------------------------------------------
        project = next(c for c in cards if c["type"] == "project")
        path = cards_root / "projects" / f"{project['slug']}.yaml"
        form = card_forms.FORMS["update-project"]
        title, body = opened(card_forms.prefill_url(REPO, form, project["slug"], build.update_values(project)))
        before = yaml.safe_load(path.read_text(encoding="utf-8"))

        edited = with_section(with_section(body, form, "version", "9.9.9"), form, "tags", "alpha, beta")
        edited = with_section(edited, form, "description", "First paragraph.\n\n### Notes\n\n- a bullet\n- another\n\nLast paragraph.")
        report = card_request.apply_request(title, edited, cards_root)
        after = yaml.safe_load(path.read_text(encoding="utf-8"))
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        expect(changed == {"version", "tags", "description", "updated_on"}, f"changed fields: {sorted(changed)}\n{report}")
        expect(after["tags"] == ["alpha", "beta"] and after["version"] == "9.9.9", f"new values: {after.get('tags')!r}, {after.get('version')!r}")
        expect("### Notes" in after["description"] and "- a bullet" in after["description"], "a ### inside a long text cut it short")

        edited = with_section(with_section(body, form, "docs_url", "-"), form, "status", "BETA")
        card_request.apply_request(title, edited, cards_root)
        after = yaml.safe_load(path.read_text(encoding="utf-8"))
        expect("docs_url" not in after, "a dash did not remove the field")
        expect(after.get("status") == "beta", f"a typed choice (any case) was not applied: {after.get('status')!r}")
        try:
            card_request.apply_request(title, with_section(body, form, "status", "finished"), cards_root)
            failures.append("an invalid choice was accepted")
        except card_request.RequestError as exc:
            expect("Use one of" in str(exc) and "maintenance" in str(exc), f"the refusal does not list the options: {exc}")

        # 4. promote: edited description, country, tags and technologies must all arrive --------------------------------
        use_case = next(c for c in cards if c["type"] == "use-case" and not c["images"])
        form = card_forms.FORMS["promote-use-case"]
        title, body = opened(card_forms.prefill_url(REPO, form, use_case["slug"], build.promote_values(use_case)))
        edited = body
        for field_id, value in (("website", "https://example.org/promoted"), ("description", "My own description.\n\nSecond paragraph."),
                                ("country", "Italy, Slovenia"), ("tags", "tag one, tag two"), ("stack", "Python, R"),
                                ("name", "A new name"), ("status", "active")):
            edited = with_section(edited, form, field_id, value)
        report = card_request.apply_request(title, edited, cards_root)
        new = cards_root / "projects" / f"{use_case['slug']}.yaml"
        expect(new.is_file() and not (cards_root / "use-cases" / f"{use_case['slug']}.yaml").exists(), f"promotion did not move the card:\n{report}")
        promoted = yaml.safe_load(new.read_text(encoding="utf-8")) if new.is_file() else {}
        expect(promoted.get("description", "").strip() == "My own description.\n\nSecond paragraph.", f"description: {promoted.get('description')!r}")
        expect(promoted.get("country") == "Italy, Slovenia", f"country: {promoted.get('country')!r}")
        expect(promoted.get("tags") == ["tag one", "tag two"] and promoted.get("stack") == ["Python", "R"], f"tags/stack: {promoted.get('tags')!r} {promoted.get('stack')!r}")
        expect(promoted.get("name") == "A new name" and promoted.get("status") == "active", f"name/status: {promoted.get('name')!r} {promoted.get('status')!r}")
        expect(promoted.get("website") == "https://example.org/promoted", f"website: {promoted.get('website')!r}")

        # promoting the pre-filled text as it is (plus a website) takes the use case's content over, including its tags
        other = next(c for c in cards if c["type"] == "use-case" and not c["images"] and c["slug"] != use_case["slug"])
        title, body = opened(card_forms.prefill_url(REPO, form, other["slug"], build.promote_values(other)))
        card_request.apply_request(title, with_section(body, form, "website", "https://example.org/other"), cards_root)
        promoted = yaml.safe_load((cards_root / "projects" / f"{other['slug']}.yaml").read_text(encoding="utf-8"))
        expect(sorted(t.casefold() for t in promoted.get("tags", [])) == sorted(t["label"].casefold() for t in other["tags"]),
               "the use case's tags (including the automatic ones) were not written into the project")
        expect(promoted.get("status") == "beta" and promoted.get("name") == other["name"], "default status or name")

        # a title that is not a request, an unknown card, and a promotion without any address are refused
        promote_empty = card_forms.request_body(form, {"card": "no-such-card"})
        for title, text, why in (("Something else", promote_empty, "unknown title"),
                                 (form.title_prefix + "no-such-card", promote_empty, "unknown card")):
            try:
                card_request.apply_request(title, text, cards_root)
                failures.append(f"accepted: {why}")
            except card_request.RequestError:
                pass

        expect(omitted_somewhere > 0, "no card needed long texts to be left out of the address - the limit is never exercised; "
                                      "if the cards have become short, this is fine and the check can be relaxed")

        # the whole set of cards is still valid for the real build
        built = subprocess.run([sys.executable, str(HERE / "build.py"), "--strict", "--cards", str(cards_root), "--out", str(Path(tmp) / "out")],
                               capture_output=True, text=True)
        expect(built.returncode == 0, f"the build rejects the cards after the requests:\n{built.stdout[-800:]}{built.stderr[-400:]}")

    if failures:
        print("Card request self-test FAILED:\n  - " + "\n  - ".join(failures))
        return 1
    print("Card request self-test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
