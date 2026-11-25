#!/usr/bin/env python3
"""Change fields of an existing card - a project or a use case - without opening its file.

    python scripts/update_card.py project my-project status=active version=1.2.0
    python scripts/update_card.py project my-project demo_url=https://example.org/demo "tags=land use, modelling"
    python scripts/update_card.py use-case my-idea country=Italy "affiliation=Agency X"
    python scripts/update_card.py project my-project demo_url=          # an empty value removes the field

"my-project" is the card's file name without ".yaml". Everything else in the file - comments, other fields - stays as it is.
Values that are the same as in the card now are skipped; if nothing is left, nothing is changed.
A project's `updated_on` is set to today, unless you set it yourself or pass --no-bump.

Three kinds of fields can be changed:
  one-line fields   name, status, version, website, country ...
  long texts        description (projects); summary and the sections of a use case (a text with blank lines and "- " bullets)
  lists             tags, stack (give them separated by commas)
If the result would not be a valid card, nothing is changed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402  (the same loader and rules the site build uses)

KINDS = {"project": "projects", "use-case": "use-cases"}
TEXT = {"description", "summary", "challenge", "proposed_ai", "scope", "expected_outcome",
        "environmental_contribution", "digital_contribution", "contribution", "conditions"}
LIST = {"tags", "stack"}
BOOLEANS = {"featured", "auto_tag"}
DEFAULTS = {"status": "active", "featured": False, "auto_tag": True}    # what a card means when it leaves the field out
DATES = {"released_on", "updated_on", "submitted_on"}
EDITABLE = {
    "project": ("name", "quip", "description", "website", "repo_url", "demo_url", "docs_url", "status", "version", "license",
                "category", "affiliation", "country", "contact", "tags", "stack", "featured", "screenshot_url", "thumbnail_url",
                "released_on", "updated_on"),
    "use-case": ("name", "quip", "summary", "main_area", "challenge", "proposed_ai", "scope", "expected_outcome",
                 "environmental_contribution", "digital_contribution", "contribution", "conditions", "affiliation", "country",
                 "contact", "tags", "stack", "featured", "auto_tag", "submitted_on"),
}
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")


class UpdateError(Exception):
    """Something the person running the script can fix; the message says what."""


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #
def clean_text(value: str) -> str:
    """A long text with Windows line ends, trailing blanks and surrounding empty lines removed."""
    return "\n".join(line.rstrip() for line in str(value).replace("\r\n", "\n").replace("\r", "\n").strip().split("\n"))


def split_list(value) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[,\n]", str(value))
    seen: dict[str, str] = {}
    for item in items:
        label = " ".join(str(item).split())
        if label:
            seen.setdefault(label.casefold(), label)
    return list(seen.values())


def normal(key: str, value) -> object:
    """A value in a form that is equal when two values mean the same (used to spot changes that are none)."""
    if value is None:
        return ""
    if isinstance(value, list) and key not in LIST:      # e.g. a country written as a YAML list
        return " ".join(", ".join(str(v) for v in value).split())
    if key in LIST:
        return tuple(i.casefold() for i in split_list(value))
    if key in TEXT:
        return clean_text(value)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()[:10]
    return " ".join(str(value).split())


def scalar(key: str, value: str) -> str:
    """A one-line value as it is written into YAML."""
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


def render(key: str, value: str) -> list[str]:
    """The lines that write `key: value` into a card file."""
    if key in LIST:
        return [f"{key}:"] + [f"  - {scalar(key, item)}" for item in split_list(value)]
    if key in TEXT:
        lines = clean_text(value).split("\n")
        lines[0] = lines[0].lstrip()                     # a block must not start with a space
        return [f"{key}: |"] + [("  " + line) if line else "" for line in lines]
    return [f"{key}: {scalar(key, ' '.join(value.split()))}"]


# --------------------------------------------------------------------------- #
# The file
# --------------------------------------------------------------------------- #
def find_span(lines: list[str], key: str) -> tuple[int, int] | None:
    """Line range of the top-level field `key`: its line plus the indented lines (text block, list) that belong to it."""
    start = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(key)}:(\s|$)", line)), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t" or lines[end].startswith("- ")):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():     # blank lines after the field are not part of it
        end -= 1
    return start, end


def set_field(text: str, key: str, value: str) -> str:
    """Set (or, for an empty value, remove) the top-level field `key`, keeping everything else in the file."""
    lines = text.split("\n")
    span = find_span(lines, key)
    if not value.strip():                                # removal
        if span:
            del lines[span[0]:span[1]]
        return "\n".join(lines)
    new = render(key, value)
    if span:
        lines[span[0]:span[1]] = new
        return "\n".join(lines)
    if key not in TEXT and key not in LIST:             # the template's switched-off one-line field: switch it on
        commented = next((i for i, line in enumerate(lines) if re.match(rf"#\s*{re.escape(key)}:(\s|$)", line)), None)
        if commented is not None:
            lines[commented] = new[0]
            return "\n".join(lines)
    separator = "\n\n" if len(new) > 1 else "\n"
    return text.rstrip("\n") + separator + "\n".join(new) + "\n"


def current_values(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def update(kind: str, slug: str, changes: dict[str, str], cards_root: Path, today: dt.date | None = None, bump: bool = True) -> list[str]:
    """Apply `changes` (field -> new value; '' removes the field) to cards/<kind>/<slug>.yaml.

    Returns a line per change. Raises UpdateError, with the file untouched, on any problem."""
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
        raise UpdateError(f"cannot change {', '.join(unknown)} on a {kind}. Fields that can be changed: {', '.join(EDITABLE[kind])}")

    current = current_values(path)
    changes = {k: v for k, v in changes.items() if normal(k, v) != normal(k, current.get(k, DEFAULTS.get(k)))}   # skip what is the same already
    if not changes:
        raise UpdateError("nothing changed: all values are the same as in the card")
    if kind == "project" and bump and "updated_on" not in changes:
        changes["updated_on"] = (today or dt.date.today()).isoformat()

    original = path.read_text(encoding="utf-8")
    text = original
    for key, value in changes.items():
        text = set_field(text, key, value)
    path.write_text(text, encoding="utf-8")
    try:                                                 # the real rules; undo if the card would no longer be valid
        if build.load_card(path, KINDS[kind], folder, {}, build.Problems()) is None:
            raise UpdateError("the card would not be valid after this change (see above) - nothing was changed")
    except BaseException:
        path.write_text(original, encoding="utf-8")
        raise
    return [f"`{key}`: " + ("removed" if not value.strip() else "changed") for key, value in changes.items()]


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
