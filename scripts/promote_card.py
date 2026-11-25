#!/usr/bin/env python3
"""Turn a use case into a project: the idea has become real work.

    python scripts/promote_card.py my-idea --website https://example.org/my-idea
    python scripts/promote_card.py my-idea --repo-url https://github.com/org/my-idea --status active

"my-idea" is the file name of the use case without ".yaml" (it is also the last part of the use case's web address).

What happens
  - cards/use-cases/my-idea.yaml becomes cards/projects/my-idea.yaml; the card's images move with it.
  - The project description is built from the use case's summary and the start of "proposed_ai".
  - Projects are not tagged automatically, so the tags and technologies the use case had (hand-written and
    automatic ones) are written into the project card as they are today.
  - The use case's long text (challenge, scope, expected outcome, conditions ...) has no place on a project card.
    It is not lost: it stays in the repository history.
  - The new card is checked with the same rules as the site build. If it is not valid (for example because no
    website or repository address was given), nothing is changed.

Going the other way (project -> use case) is not automated: use-case cards need sections that projects do not have.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402  (the same loader and rules the site build uses)

SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")


class PromoteError(Exception):
    """Something the person running the script can fix; the message says what."""


def scalar(value: str) -> str:
    """A one-line YAML value: plain when that is safe, otherwise double-quoted."""
    safe = re.fullmatch(r"[^\W\d_][\w ,.()/&+–-]*", value) and not re.fullmatch(r"(?i)true|false|yes|no|null|on|off|y|n", value)
    return value if safe else json.dumps(value, ensure_ascii=False)


def block(key: str, text: str) -> str:
    return f"{key}: |\n" + "\n".join(("  " + line) if line else "" for line in text.split("\n"))


def is_local(path: str) -> bool:
    return not re.match(r"[a-z][a-z0-9+.-]*:", path, re.I)


def promote(slug: str, cards_root: Path, links: dict[str, str], status: str, today: dt.date | None = None) -> tuple[Path, list[str]]:
    """Move cards/use-cases/<slug>.yaml to cards/projects/ as a project card. Returns the new file and notes for the user."""
    today = today or dt.date.today()
    if not SLUG.fullmatch(slug):
        raise PromoteError(f"{slug!r} is not a card name: use the file name without .yaml, lowercase letters, digits and dashes only")
    src_dir, dst_dir = cards_root / "use-cases", cards_root / "projects"
    src, dst = src_dir / f"{slug}.yaml", dst_dir / f"{slug}.yaml"
    if not src.is_file():
        names = ", ".join(sorted(p.stem for p in src_dir.glob("*.yaml"))) or "(none)"
        raise PromoteError(f"there is no use case named {slug!r}. Existing use cases: {names}")
    if dst.exists():
        raise PromoteError(f"cards/projects/{slug}.yaml already exists - nothing was changed")

    problems = build.Problems()
    card = build.load_card(src, "use-cases", src_dir, {}, problems)
    if card is None:
        raise PromoteError("the use case itself is not valid; fix the problems listed above first")
    build.apply_auto_tags([card], build.load_taxonomy(build.ROOT / "site" / "taxonomy.yaml"))
    raw = yaml.safe_load(src.read_text(encoding="utf-8"))

    # --- description: summary + start of "proposed_ai" ---------------------------------------------------
    proposed = next((s["blocks"] for s in card["sections"] if s["field"] == "proposed_ai"), [])
    paragraphs = [b["text"] for b in proposed if b["kind"] == "p"]
    has_summary = bool(build.as_text(raw.get("summary")))
    description = "\n\n".join(([card["summary"]] if has_summary else []) + paragraphs[: 1 if has_summary else 2]) or card["summary"]

    # --- images: the first one becomes the project's screenshot; files move along -----------------------
    entries = [e.get("file") or e.get("url") if isinstance(e, dict) else e for e in (raw.get("images") or [])]
    entries = [build.as_text(e) for e in entries if build.as_text(e)]
    local = [e for e in entries if is_local(e)]
    copied: list[Path] = []
    try:
        for rel in local:
            target = dst_dir / rel
            if target.exists():
                raise PromoteError(f"cards/projects/{rel} already exists - nothing was changed")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_dir / rel, target)
            copied.append(target)

        # --- the project card ------------------------------------------------------------------------
        lines = [
            f'# Promoted from the use case "{slug}" on {today.isoformat()}. The use case\'s full text (challenge, scope,',
            "# expected outcome, conditions) is not shown on a project card; it stays in the repository history.",
            "# Please review the description, the links and the status.",
            "",
            f"name: {scalar(card['name'])}",
        ]
        if card["quip"]:
            lines.append(f"quip: {scalar(card['quip'])}")
        lines += ["", block("description", description), ""]
        for field, key in (("website", "website"), ("repo_url", "repo_url"), ("demo_url", "demo_url"), ("docs_url", "docs_url")):
            if links.get(field):
                lines.append(f"{key}: {links[field]}")
        if entries:
            lines.append(f"screenshot_url: {scalar(entries[0])}")
        lines += ["", f"category: {scalar(card['area_short'])}"]
        for key, labels in (("tags", [*card["tag_labels"], *card["auto_tags"]]), ("stack", [*card["stack_labels"], *card["auto_stack"]])):
            if labels:
                lines += [f"{key}:"] + [f"  - {scalar(label)}" for label in labels]
        lines += ["", f"status: {status}"]
        if card["featured"]:
            lines.append("featured: true")
        if card["affiliation"]:
            lines.append(f"affiliation: {scalar(card['affiliation'])}")
        if card["country_labels"]:
            lines.append(f"country: {scalar(', '.join(card['country_labels']))}")
        if build.as_text(raw.get("contact")):
            lines.append(f"contact: {scalar(build.as_text(raw.get('contact')))}")
        lines += ["", f"updated_on: {today.isoformat()}"]

        dst_dir.mkdir(parents=True, exist_ok=True)
        dst.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # --- validate with the real rules; undo everything if the result is not a valid project --------
        check = build.Problems()
        if build.load_card(dst, "projects", dst_dir, {}, check) is None:
            raise PromoteError("the new project card would not be valid (see above) - nothing was changed")
    except BaseException:
        dst.unlink(missing_ok=True)
        for path in copied:
            path.unlink(missing_ok=True)
        raise

    # --- success: remove the use case and its moved images --------------------------------------------
    src.unlink()
    for rel in local:
        (src_dir / rel).unlink(missing_ok=True)
        folder = (src_dir / rel).parent
        while folder != src_dir and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent

    notes = [
        f"cards/use-cases/{slug}.yaml  ->  cards/projects/{slug}.yaml",
        f"status set to '{status}' - change it if the project is further along or finished",
    ]
    if local:
        notes.append(f"moved {len(local)} image file(s); only the first one is used (screenshot_url), the others stay in cards/projects/ - delete them if not needed")
    notes.append("the use case's long text is no longer shown (it remains in the git history)")
    notes.append("projects are not tagged automatically: the current tags and technologies were written into the card")
    return dst, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("card", help='name of the use case: its file name without ".yaml"')
    parser.add_argument("--website", default="", help="the project's website (https://...)")
    parser.add_argument("--repo-url", default="", help="the project's code repository (https://...)")
    parser.add_argument("--demo-url", default="", help="optional: a place to try it out")
    parser.add_argument("--docs-url", default="", help="optional: documentation")
    parser.add_argument("--status", default="beta", choices=list(build.STATUSES), help="project status (default: beta)")
    parser.add_argument("--cards", type=Path, default=build.ROOT / "cards", help="the cards folder (default: cards)")
    args = parser.parse_args()

    links = {"website": args.website.strip(), "repo_url": args.repo_url.strip(), "demo_url": args.demo_url.strip(), "docs_url": args.docs_url.strip()}
    try:
        target, notes = promote(args.card, args.cards.resolve(), links, args.status)
    except PromoteError as exc:
        print(f"Cannot promote: {exc}")
        return 1
    print(f"Promoted {args.card} to a project.")
    for note in notes:
        print(f"  - {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
