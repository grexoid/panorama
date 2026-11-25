#!/usr/bin/env python3
"""Apply a card request: an issue that was created from one of the card forms (see scripts/card_forms.py).

    python scripts/card_request.py --title "Update project: eagle-eye" --body-file issue.md

The title says what kind of request it is, the body holds the submitted values. For an Update, only values that differ
from the card are applied: a box left as it was changes nothing, a single dash (-) removes the value. For a Promote, the
use case becomes a project (see scripts/promote_card.py). Prints a short report in Markdown; exits with status 1 and an
explanation when the request cannot be applied. Used by the "Card requests" workflow, which posts the report on the issue.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import card_forms  # noqa: E402
import promote_card  # noqa: E402
import update_card  # noqa: E402


class RequestError(Exception):
    """The request cannot be applied; the message is shown to the person who made it."""


def apply_request(title: str, body: str, cards_root: Path) -> str:
    """Do what the request asks. Returns the report (Markdown); raises RequestError."""
    form = card_forms.form_for_title(title)
    if form is None:
        raise RequestError("The title of this issue does not start with one of: " + ", ".join(f"`{f.title_prefix.strip()}`" for f in card_forms.FORMS.values()))
    values = card_forms.parse_issue_body(form, body)
    slug = values["card"].strip().strip("`")
    if not slug:
        raise RequestError("The box *Card (file name)* is empty.")

    try:
        if form.action == "update":
            changes = {}
            for field in form.fields:
                value = values[field.id].strip()
                if field.id == "card" or not value or value == card_forms.KEEP:
                    continue
                changes[field.id] = "" if value == card_forms.REMOVE else values[field.id]
            done = update_card.update(form.kind, slug, changes, cards_root)
            return f"Updated the {form.kind} `{slug}`:\n\n" + "\n".join(f"- {line}" for line in done)

        links = {field: values[field].strip() for field in ("website", "repo_url", "demo_url", "docs_url")}
        overrides = {field: values[field] for field in ("name", "quip", "description", "tags", "stack", "country", "affiliation", "contact")}
        target, notes = promote_card.promote(slug, cards_root, links, values["status"].strip() or "beta", overrides=overrides)
        return f"Promoted the use case `{slug}` to a project (`{target.relative_to(cards_root.parent).as_posix()}`):\n\n" + "\n".join(f"- {n}" for n in notes)
    except (update_card.UpdateError, promote_card.PromoteError) as exc:
        raise RequestError(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", default=os.environ.get("ISSUE_TITLE", ""), help="the issue's title (default: $ISSUE_TITLE)")
    parser.add_argument("--body-file", type=Path, help="a file with the issue's text (default: $ISSUE_BODY)")
    parser.add_argument("--cards", type=Path, default=build.ROOT / "cards", help="the cards folder (default: cards)")
    args = parser.parse_args()
    body = args.body_file.read_text(encoding="utf-8") if args.body_file else os.environ.get("ISSUE_BODY", "")
    try:
        print(apply_request(args.title.strip(), body, args.cards.resolve()))
    except RequestError as exc:
        print(f"**This request could not be applied.**\n\n{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
