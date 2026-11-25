#!/usr/bin/env python3
"""Self-test for the Update / Promote buttons: forms, addresses, and applying a submitted form.

Works on a temporary copy of the repository's real cards, so nothing is changed.

  1. the generated issue forms match their definitions, and the definitions match the build's vocabularies;
  2. for EVERY real card, the Update form as the button pre-fills it, submitted unchanged, changes nothing;
  3. editing one box changes exactly that field;
  4. a Promote form as pre-filled (plus a website) turns a use case into a valid project;
  5. the button addresses stay within GitHub's length limit.

    python scripts/check_requests.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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


def main() -> int:
    failures: list[str] = []

    def expect(ok: bool, message: str) -> None:
        if not ok:
            failures.append(message)

    # 1. forms ------------------------------------------------------------------------------------------------------
    expect(not card_forms.stale_forms(), f"issue forms out of date: {card_forms.stale_forms()} (run: python scripts/card_forms.py --write)")
    expect(card_forms.MAIN_AREAS == tuple(a[1] for a in build.AREAS), "card_forms.MAIN_AREAS differs from build.AREAS")
    expect(set(card_forms.STATUSES) == set(build.STATUSES), "card_forms.STATUSES differs from build.STATUSES")
    for form in card_forms.FORMS.values():
        labels = [f.label for f in form.fields]
        expect(len(labels) == len(set(labels)), f"{form.key}: labels are not unique")

    with tempfile.TemporaryDirectory() as tmp:
        cards_root = Path(tmp) / "cards"
        shutil.copytree(build.ROOT / "cards", cards_root)
        cards = load_all(cards_root)
        expect(len(cards) > 2, "no real cards found to test with")

        # 2. every real card, pre-filled and submitted unchanged -> "nothing changed" -------------------------------
        for card in cards:
            form = card_forms.FORMS["update-project" if card["type"] == "project" else "update-use-case"]
            values = build.update_values(card)
            body = card_forms.render_issue_body(form, {**values, "card": card["slug"]})
            try:
                report = card_request.apply_request(form.title_prefix + card["slug"], body, cards_root)
                failures.append(f"{card['type']} {card['slug']}: the unchanged form changed the card:\n{report}")
            except card_request.RequestError as exc:
                expect("nothing changed" in str(exc), f"{card['type']} {card['slug']}: unexpected refusal: {exc}")
            url = card_forms.prefill_url(REPO, form, card["slug"], values)
            expect(len(url) <= card_forms.MAX_URL + 400, f"{card['slug']}: address is {len(url)} characters long")
            expect("template=" + form.key + ".yml" in url and "&card=" + card["slug"] in url, f"{card['slug']}: address lacks template/card")

        # 3. editing one box changes exactly that field -------------------------------------------------------------
        project = next(c for c in cards if c["type"] == "project")
        path = cards_root / "projects" / f"{project['slug']}.yaml"
        before = yaml.safe_load(path.read_text(encoding="utf-8"))
        form = card_forms.FORMS["update-project"]
        values = {**build.update_values(project), "card": project["slug"], "version": "9.9.9", "tags": "alpha, beta"}
        report = card_request.apply_request(form.title_prefix + project["slug"], card_forms.render_issue_body(form, values), cards_root)
        after = yaml.safe_load(path.read_text(encoding="utf-8"))
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        expect(changed == {"version", "tags", "updated_on"}, f"changed fields: {sorted(changed)} (expected version, tags, updated_on)\n{report}")
        expect(after["tags"] == ["alpha", "beta"] and after["version"] == "9.9.9", f"new values: {after.get('tags')!r}, {after.get('version')!r}")
        # a single dash removes a value
        values = {**build.update_values(project), "card": project["slug"], "docs_url": "-", "version": "9.9.9"}
        card_request.apply_request(form.title_prefix + project["slug"], card_forms.render_issue_body(form, values), cards_root)
        expect("docs_url" not in yaml.safe_load(path.read_text(encoding="utf-8")), "a dash did not remove the field")

        # 4. promote --------------------------------------------------------------------------------------------------
        use_case = next(c for c in cards if c["type"] == "use-case" and not c["images"])
        form = card_forms.FORMS["promote-use-case"]
        values = {**build.promote_values(use_case), "card": use_case["slug"], "website": "https://example.org/promoted"}
        report = card_request.apply_request(form.title_prefix + use_case["slug"], card_forms.render_issue_body(form, values), cards_root)
        new = cards_root / "projects" / f"{use_case['slug']}.yaml"
        expect(new.is_file() and not (cards_root / "use-cases" / f"{use_case['slug']}.yaml").exists(), f"promotion did not move the card:\n{report}")
        promoted = yaml.safe_load(new.read_text(encoding="utf-8")) if new.is_file() else {}
        expect(promoted.get("website") == "https://example.org/promoted" and promoted.get("status") == "beta", f"promoted card: {promoted}")
        expect(promoted.get("name") == use_case["name"], "the name was not taken over")
        expect(sorted(t.casefold() for t in promoted.get("tags", [])) == sorted(t["label"].casefold() for t in use_case["tags"]),
               "the use case's tags (including the automatic ones) were not written into the project")

        # a title that is not a request, and a missing website, are refused with an explanation
        for title, why in (("Something else", "unknown title"), (card_forms.FORMS["promote-use-case"].title_prefix + "no-such-card", "unknown card")):
            try:
                card_request.apply_request(title, card_forms.render_issue_body(card_forms.FORMS["promote-use-case"], {"card": "no-such-card"}), cards_root)
                failures.append(f"accepted: {why}")
            except card_request.RequestError:
                pass

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
