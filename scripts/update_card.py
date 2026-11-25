#!/usr/bin/env python3
"""Change single-line fields of an existing card - a project or a use case - without opening its file.

    python scripts/update_card.py project my-project status=active version=1.2.0
    python scripts/update_card.py project my-project demo_url=https://example.org/demo
    python scripts/update_card.py use-case my-idea country=Italy "affiliation=Agency X"
    python scripts/update_card.py project my-project demo_url=          # an empty value removes the field

"my-project" is the card's file name without ".yaml". Everything else in the file - comments, text, lists - stays as it is.
A project's `updated_on` is set to today, unless you set it yourself or pass --no-bump.

Only one-line fields can be changed here (see below). Descriptions, the sections of a use case, tags and other longer
texts are edited in the file itself (on GitHub: the pencil button on the card's file).
If the result would not be a valid card, nothing is changed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402  (the same loader and rules the site build uses)

KINDS = {"project": "projects", "use-case": "use-cases"}
EDITABLE = {
    "project": ("name", "quip", "status", "version", "license", "website", "repo_url", "demo_url", "docs_url", "category",
                "affiliation", "country", "contact", "featured", "screenshot_url", "thumbnail_url", "released_on", "updated_on"),
    "use-case": ("name", "quip", "main_area", "affiliation", "country", "contact", "featured", "auto_tag", "submitted_on"),
}
BOOLEANS = {"featured", "auto_tag"}
DATES = {"released_on", "updated_on", "submitted_on"}
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")


class UpdateError(Exception):
    """Something the person running the script can fix; the message says what."""


def render(key: str, value: str) -> str:
    """The value as it is written into YAML."""
    if key in BOOLEANS:
        if value.lower() not in ("true", "false"):
            raise UpdateError(f"'{key}' must be true or false, not {value!r}")
        return value.lower()
    if key in DATES:
        try:
            dt.date.fromisoformat(value)
        except ValueError:
            raise UpdateError(f"'{key}' must be a date written year-month-day (2026-11-04), not {value!r}") from None
        return value
    safe = re.fullmatch(r"[^\W\d_][\w ,.()/&+–-]*", value) and not re.fullmatch(r"(?i)true|false|yes|no|null|on|off|y|n", value)
    return value if safe else '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_field(text: str, key: str, value: str) -> str:
    """Set (or, for an empty value, remove) the top-level one-line field `key` in the YAML text, keeping everything else."""
    lines = text.split("\n")
    active = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(key)}:(\s|$)", line)), None)
    if active is not None:
        rest = lines[active].split(":", 1)[1].strip()
        following = lines[active + 1] if active + 1 < len(lines) else ""
        if re.match(r"[|>]", rest) or (not rest and re.match(r"\s+\S|-\s", following)):
            raise UpdateError(f"'{key}' is a longer text or a list; edit it in the card's file instead")
    if not value:                                        # removal
        if active is not None:
            del lines[active]
        return "\n".join(lines)
    new = f"{key}: {render(key, value)}"
    if active is not None:
        lines[active] = new
        return "\n".join(lines)
    commented = next((i for i, line in enumerate(lines) if re.match(rf"#\s*{re.escape(key)}:(\s|$)", line)), None)
    if commented is not None:                            # the template's switched-off line: switch it on
        lines[commented] = new
        return "\n".join(lines)
    return text.rstrip("\n") + "\n" + new + "\n"


def update(kind: str, slug: str, changes: dict[str, str], cards_root: Path, today: dt.date | None = None, bump: bool = True) -> list[str]:
    """Apply `changes` to cards/<kind>/<slug>.yaml. Returns a line per change; raises UpdateError (file untouched) on any problem."""
    if kind not in KINDS:
        raise UpdateError(f"kind must be 'project' or 'use-case', not {kind!r}")
    if not SLUG.fullmatch(slug):
        raise UpdateError(f"{slug!r} is not a card name: use the file name without .yaml")
    folder = cards_root / KINDS[kind]
    path = folder / f"{slug}.yaml"
    if not path.is_file():
        names = ", ".join(sorted(p.stem for p in folder.glob("*.yaml"))) or "(none)"
        raise UpdateError(f"there is no {kind} named {slug!r}. Existing ones: {names}")
    if not changes:
        raise UpdateError("nothing to change: give at least one field, for example status=active")
    unknown = [k for k in changes if k not in EDITABLE[kind]]
    if unknown:
        raise UpdateError(f"cannot change {', '.join(unknown)} here. Fields that can be changed on a {kind}: {', '.join(EDITABLE[kind])}")

    original = path.read_text(encoding="utf-8")
    changes = dict(changes)
    if kind == "project" and bump and "updated_on" not in changes:
        changes["updated_on"] = (today or dt.date.today()).isoformat()

    text = original
    for key, value in changes.items():
        text = set_field(text, key, value.strip())
    path.write_text(text, encoding="utf-8")
    try:                                                 # the real rules; undo if the card would no longer be valid
        if build.load_card(path, KINDS[kind], folder, {}, build.Problems()) is None:
            raise UpdateError("the card would not be valid after this change (see above) - nothing was changed")
    except BaseException:
        path.write_text(original, encoding="utf-8")
        raise
    return [f"{key}: " + (f"set to {value!r}" if value else "removed") for key, value in changes.items()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=list(KINDS), help="project or use-case")
    parser.add_argument("card", help='the card\'s file name without ".yaml"')
    parser.add_argument("changes", nargs="*", metavar="field=value", help="e.g. status=active (an empty value removes the field)")
    parser.add_argument("--cards", type=Path, default=build.ROOT / "cards", help="the cards folder (default: cards)")
    parser.add_argument("--no-bump", action="store_true", help="do not set a project's updated_on to today")
    args = parser.parse_args()

    try:
        changes = {}
        for item in args.changes:
            key, sep, value = item.partition("=")
            if not sep:
                raise UpdateError(f"{item!r} is not field=value")
            changes[key.strip()] = value
        done = update(args.kind, args.card, changes, args.cards.resolve(), bump=not args.no_bump)
    except UpdateError as exc:
        print(f"Cannot update: {exc}")
        return 1
    print(f"Updated {args.kind} {args.card}:")
    for line in done:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
