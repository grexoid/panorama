#!/usr/bin/env python3
"""Apply a card request: an issue that was opened by the Update or Promote button of a card (see scripts/card_forms.py).

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
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402
import card_forms  # noqa: E402
import card_images  # noqa: E402
import promote_card  # noqa: E402
import update_card  # noqa: E402


class RequestError(Exception):
    """The request cannot be applied; the message is shown to the person who made it."""


PICTURE_STEMS = {"screenshot_url": "screenshot", "thumbnail_url": "logo"}    # project fields that take one picture


def take_pictures(form: card_forms.Form, values: dict[str, str], slug: str, cards_root: Path,
                  fetch: Callable[[str], bytes], saved: list[Path]) -> list[str]:
    """Pictures dragged into the request text are stored next to the card and replaced by their path in `values`.

    Returns report lines. Every stored file is added to `saved`, so the caller can remove them if the request fails."""
    folder = update_card.KINDS[form.kind]
    notes: list[str] = []

    def store(stem: str, url: str) -> str:
        rel = card_images.save(cards_root, folder, slug, stem, fetch(url))
        saved.append(cards_root / folder / rel)
        notes.append(f"added picture `cards/{folder}/{rel}`")
        return rel

    try:
        for field in form.fields:
            text = values.get(field.id, "")
            found = card_images.find_attachments(text) if text.strip() and text.strip() != card_forms.REMOVE else []
            if not found:
                continue
            if field.id in PICTURE_STEMS:                                  # one picture replaces the current one
                values[field.id] = store(PICTURE_STEMS[field.id], found[0][1])
            elif field.id == update_card.IMAGES:                           # the pictures are added to the figures
                path = cards_root / folder / f"{slug}.yaml"
                lines = update_card.image_lines(update_card.current_values(path).get("images"))
                for alt, url in found:
                    caption = card_images.caption_from(alt)
                    rel = store("figure", url)
                    lines.append(f"{rel} | {caption}" if caption else rel)
                lines += update_card.image_lines(card_images.without_attachments(text))
                values[field.id] = "\n".join(lines)
    except card_images.ImageError as exc:
        raise RequestError(str(exc)) from exc
    return notes


def apply_request(title: str, body: str, cards_root: Path, fetch: Callable[[str], bytes] = card_images.download) -> str:
    """Do what the request asks. Returns the report (Markdown); raises RequestError.

    `fetch` gets the bytes of a picture that was attached to the issue (replaced by a fake in tests)."""
    form = card_forms.form_for_title(title)
    if form is None:
        raise RequestError("The title of this issue does not start with one of: " + ", ".join(f"`{f.title_prefix.strip()}`" for f in card_forms.FORMS.values()))
    values = card_forms.parse_issue_body(form, body)
    for field in form.fields:                         # a typed choice must be one of the options
        if field.kind == "choice":
            try:
                values[field.id] = card_forms.parse_choice(field, values[field.id])
            except ValueError as exc:
                raise RequestError(str(exc)) from exc
    slug = values["card"].strip().strip("`") or title[len(form.title_prefix):].strip()
    if not slug:
        raise RequestError("There is no card name: neither under the heading *Card (file name)* nor in the title.")

    saved: list[Path] = []
    try:
        if form.action == "update":
            if not update_card.SLUG.fullmatch(slug) or not (cards_root / update_card.KINDS[form.kind] / f"{slug}.yaml").is_file():
                raise update_card.UpdateError(f"there is no {form.kind} named {slug!r}")     # before any picture is stored
            pictures = take_pictures(form, values, slug, cards_root, fetch, saved)
            changes = {}
            for field in form.fields:
                value = values[field.id].strip()
                if field.id == "card" or not value or value == card_forms.KEEP:
                    continue
                changes[field.id] = "" if value == card_forms.REMOVE else values[field.id]
            done = update_card.update(form.kind, slug, changes, cards_root)
            return f"Updated the {form.kind} `{slug}`:\n\n" + "\n".join(f"- {line}" for line in [*done, *pictures])

        given = {f.id: ("" if values[f.id].strip() == card_forms.REMOVE else values[f.id]) for f in form.fields}   # a dash = not given
        links = {field: given[field].strip() for field in ("website", "repo_url", "demo_url", "docs_url")}
        overrides = {field: given[field] for field in ("name", "quip", "description", "tags", "stack", "country", "affiliation", "contact")}
        target, notes = promote_card.promote(slug, cards_root, links, given["status"].strip() or "beta", overrides=overrides)
        return f"Promoted the use case `{slug}` to a project (`{target.relative_to(cards_root.parent).as_posix()}`):\n\n" + "\n".join(f"- {n}" for n in notes)
    except (update_card.UpdateError, promote_card.PromoteError, RequestError) as exc:
        for path in saved:                                 # a refused request leaves no stray picture behind
            path.unlink(missing_ok=True)
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
