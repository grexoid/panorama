#!/usr/bin/env python3
"""Build the static project gallery.

Reads every project card (``*.yaml``) from the projects directory, validates it,
and renders ``dist/`` (index.html + assets) from the Jinja2 templates in ``site/``.

    python scripts/build.py                      # projects/ -> dist/
    python scripts/build.py --projects resources/examples --out preview
    python scripts/build.py --strict             # treat warnings as errors

Exit status is non-zero when any card is invalid, so it doubles as the PR check.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent

STATUSES = {
    "active": "Active",
    "beta": "Beta",
    "maintenance": "Maintenance",
    "archived": "Archived",
}
LINK_FIELDS = ("website", "repo_url", "demo_url", "docs_url")
IMAGE_FIELDS = ("screenshot_url", "thumbnail_url")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg"}
MAX_IMAGE_BYTES = 1_500_000
KNOWN_FIELDS = {
    "name", "quip", "description", "category", "tags", "stack", "status", "featured",
    "version", "license", "affiliation", "contact", "released_on", "updated_on",
    *LINK_FIELDS, *IMAGE_FIELDS,
}
REQUIRED_FIELDS = ("name", "description")
# Hues used for tag chips (blue, green, amber, violet, pink, teal) - mirrors the mockup.
TAG_HUES = (205, 150, 35, 270, 335, 178)


# --------------------------------------------------------------------------- #
# Problem reporting
# --------------------------------------------------------------------------- #
class Problems:
    """Collects errors/warnings; emits GitHub Actions annotations when in CI."""

    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []
        self.ci = os.environ.get("GITHUB_ACTIONS") == "true"

    def _emit(self, level: str, where: str, msg: str) -> None:
        if self.ci:
            print(f"::{level} file={where},title=Project card::{msg}")
        else:
            print(f"  {level.upper():7} {where}: {msg}")

    def error(self, where: str, msg: str) -> None:
        self.errors.append((where, msg))
        self._emit("error", where, msg)

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append((where, msg))
        self._emit("warning", where, msg)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def stable_int(text: str) -> int:
    return int(hashlib.sha1(text.casefold().encode("utf-8")).hexdigest()[:8], 16)


def initials(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name)
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].capitalize()
    return (words[0][0] + words[1][0]).upper()


def fmt_date(d: dt.date) -> str:
    return f"{d.day} {d:%b} {d.year}"


def as_text(value) -> str:
    return "" if value is None else str(value).strip()


def as_date(value, where, field, problems) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip())
    except ValueError:
        problems.error(where, f"'{field}' must be an ISO date (YYYY-MM-DD), got {value!r}")
        return None


def as_list(value, where, field, problems) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [v for v in value.split(",")]
    if not isinstance(value, list):
        problems.error(where, f"'{field}' must be a list")
        return []
    seen: dict[str, str] = {}
    for item in value:
        label = re.sub(r"\s+", " ", as_text(item).replace("|", "/"))
        if label:
            seen.setdefault(label.casefold(), label)
    return list(seen.values())


def facet_key(label: str) -> str:
    return label.casefold()


def check_url(value: str, where: str, field: str, problems: Problems) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        problems.error(where, f"'{field}' must be an absolute http(s) URL, got {value!r}")
        return None
    return value


# --------------------------------------------------------------------------- #
# Loading cards
# --------------------------------------------------------------------------- #
def resolve_image(value, where, field, projects_dir: Path, media: dict[str, Path], problems):
    """Return the URL to use for an image field; local files are queued for copying."""
    value = as_text(value)
    if not value:
        return None
    if urlparse(value).scheme in ("http", "https"):
        return check_url(value, where, field, problems)
    if urlparse(value).scheme or value.startswith(("/", "\\")):
        problems.error(where, f"'{field}' must be an http(s) URL or a path relative to the projects folder")
        return None
    source = (projects_dir / value).resolve()
    if projects_dir.resolve() not in source.parents:
        problems.error(where, f"'{field}' points outside the projects folder: {value}")
        return None
    if not source.is_file():
        problems.error(where, f"'{field}' file not found: {value}")
        return None
    if source.suffix.lower() not in IMAGE_SUFFIXES:
        problems.error(where, f"'{field}' must be one of {', '.join(sorted(IMAGE_SUFFIXES))}")
        return None
    if source.stat().st_size > MAX_IMAGE_BYTES:
        problems.warn(where, f"'{field}' is {source.stat().st_size // 1024} kB - please keep images under {MAX_IMAGE_BYTES // 1000} kB")
    rel = source.relative_to(projects_dir.resolve()).as_posix()
    media[rel] = source
    return f"media/{rel}"


def load_card(path: Path, projects_dir: Path, media: dict[str, Path], problems: Problems) -> dict | None:
    where = path.relative_to(ROOT).as_posix() if ROOT in path.parents else path.as_posix()
    n_errors = len(problems.errors)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        # The parser's own message is technical, so add the usual causes in plain words.
        line = getattr(getattr(exc, "problem_mark", None), "line", None)
        near = f" (near line {line + 1})" if line is not None else ""
        problems.error(
            where,
            f"the file is not valid YAML{near}. Usual causes: text containing a colon followed by a space "
            "(put the whole text in double quotes), a Tab instead of spaces, a missing space after a colon, "
            "or a missing closing quote. See the notes at the top of resources/templates/project-card.yaml.",
        )
        return None
    if not isinstance(raw, dict):
        problems.error(where, "the file must consist of 'field: value' lines - compare it with resources/templates/project-card.yaml")
        return None

    for key in raw.keys() - KNOWN_FIELDS:
        problems.warn(where, f"unknown field '{key}' is ignored - check the spelling against the template")
    for key in REQUIRED_FIELDS:
        if not as_text(raw.get(key)):
            problems.error(where, f"missing required field '{key}'")

    name = as_text(raw.get("name"))
    links = {f: check_url(as_text(raw.get(f)), where, f, problems) for f in LINK_FIELDS}
    if not (links["website"] or links["repo_url"]):
        problems.error(where, "provide at least one of 'website' or 'repo_url' (a full address starting with https://)")

    status = as_text(raw.get("status")).lower() or "active"
    if status not in STATUSES:
        problems.error(where, f"'status' must be one of {', '.join(STATUSES)}, got {status!r}")
        status = "active"

    featured = raw.get("featured", False)
    if not isinstance(featured, bool):
        problems.error(where, "'featured' must be true or false")
        featured = False

    released = as_date(raw.get("released_on"), where, "released_on", problems)
    updated = as_date(raw.get("updated_on"), where, "updated_on", problems) or released
    if released and updated and updated < released:
        problems.warn(where, "'updated_on' is earlier than 'released_on'")

    contact_raw = as_text(raw.get("contact"))
    contact_name, contact_email = contact_raw, ""
    if m := re.fullmatch(r"(.*?)\s*<([^<>@\s]+@[^<>@\s]+)>", contact_raw):
        contact_name, contact_email = m.group(1) or m.group(2), m.group(2)

    tag_labels = as_list(raw.get("tags"), where, "tags", problems)
    stack_labels = as_list(raw.get("stack"), where, "stack", problems)

    screenshot = resolve_image(raw.get("screenshot_url"), where, "screenshot_url", projects_dir, media, problems)
    thumbnail = resolve_image(raw.get("thumbnail_url"), where, "thumbnail_url", projects_dir, media, problems)

    if len(problems.errors) > n_errors:
        return None

    description = as_text(raw.get("description"))
    category = as_text(raw.get("category"))
    return {
        "slug": path.stem,
        "name": name,
        "quip": as_text(raw.get("quip")),
        "paragraphs": [" ".join(p.split()) for p in re.split(r"\n\s*\n", description) if p.strip()],
        "category": category,
        "tags": [{"label": t, "key": facet_key(t), "hue": TAG_HUES[stable_int(t) % len(TAG_HUES)]} for t in tag_labels],
        "stack": [{"label": t, "key": facet_key(t)} for t in stack_labels],
        "status": status,
        "status_label": STATUSES[status],
        "featured": featured,
        "version": as_text(raw.get("version")),
        "license": as_text(raw.get("license")),
        "affiliation": as_text(raw.get("affiliation")),
        "contact_name": contact_name,
        "contact_email": contact_email,
        "released": released,
        "released_text": fmt_date(released) if released else "",
        "updated": updated,
        "updated_text": fmt_date(updated) if updated else "",
        "links": links,
        "screenshot": screenshot,
        "thumbnail": thumbnail,
        "hue": stable_int(name) % 360,
        "initials": initials(name),
        "search": " ".join(
            [name, as_text(raw.get("quip")), description, category, status, as_text(raw.get("affiliation")),
             *tag_labels, *stack_labels]
        ).casefold(),
    }


def default_order(card: dict) -> tuple:
    """Featured first, then most recently updated, then A-Z."""
    updated = card["updated"] or dt.date.min
    return (not card["featured"], -updated.toordinal(), card["name"].casefold())


def build_facets(cards: list[dict], field: str) -> list[dict]:
    counts: dict[str, dict] = {}
    for card in cards:
        for item in card[field]:
            entry = counts.setdefault(item["key"], {**item, "count": 0})
            entry["count"] += 1
    return sorted(counts.values(), key=lambda e: (-e["count"], e["label"].casefold()))


# --------------------------------------------------------------------------- #
# Site assembly
# --------------------------------------------------------------------------- #
def load_site_config(path: Path) -> dict:
    config = {
        "title": "Project Gallery",
        "heading": "Projects",
        "tagline": "",
        "icon": "🗂️",
        "search_placeholder": "Search projects, tags, technologies…",
        "contribute_url": "",
        "footer": "",
    }
    if path.is_file():
        config.update({k: v for k, v in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items() if v is not None})
    if not config["contribute_url"] and os.environ.get("GITHUB_REPOSITORY"):
        config["contribute_url"] = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}#adding-a-project"
    return config


def clean_dir(folder: Path) -> None:
    """Empty a folder. Tolerates folders held open by a dev server / file watcher (common on Windows)."""
    for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            path.unlink() if path.is_file() or path.is_symlink() else path.rmdir()
        except OSError:
            pass  # a locked empty folder is harmless: files are rewritten in place


def file_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:8]


def public_record(card: dict) -> dict:
    return {
        "slug": card["slug"], "name": card["name"], "quip": card["quip"],
        "description": "\n\n".join(card["paragraphs"]), "category": card["category"],
        "tags": [t["label"] for t in card["tags"]], "stack": [t["label"] for t in card["stack"]],
        "status": card["status"], "featured": card["featured"], "version": card["version"],
        "license": card["license"], "affiliation": card["affiliation"],
        "released_on": card["released"].isoformat() if card["released"] else None,
        "updated_on": card["updated"].isoformat() if card["updated"] else None,
        "links": {k: v for k, v in card["links"].items() if v},
        "screenshot": card["screenshot"], "thumbnail": card["thumbnail"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--projects", type=Path, default=ROOT / "projects", help="folder with project cards (default: projects)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder (default: dist)")
    parser.add_argument("--site", type=Path, default=ROOT / "site", help="folder with templates, static files and site.yaml")
    parser.add_argument("--strict", action="store_true", help="fail on warnings as well as errors")
    args = parser.parse_args()

    projects_dir = args.projects.resolve()
    out_dir = args.out.resolve()
    site_dir = args.site.resolve()
    problems = Problems()

    files = sorted(p for p in projects_dir.glob("*.y*ml") if p.suffix in (".yaml", ".yml") and not p.name.startswith("_"))
    print(f"Reading {len(files)} project card(s) from {projects_dir}")
    media: dict[str, Path] = {}
    cards = [c for f in files if (c := load_card(f, projects_dir, media, problems))]
    cards.sort(key=default_order)

    if problems.errors or (args.strict and problems.warnings):
        print(f"\nBuild failed: {len(problems.errors)} error(s), {len(problems.warnings)} warning(s).")
        return 1
    if not cards:
        # Not an error: a fresh repository has no cards yet and the site shows a friendly empty state.
        print("Note: no project cards found - the gallery will be empty.")

    # Fresh output folder (guard against pointing --out at something important).
    if out_dir.exists():
        if out_dir in (ROOT, site_dir, projects_dir) or ROOT.parent == out_dir or out_dir == Path(out_dir.anchor):
            print(f"Refusing to clear {out_dir}")
            return 1
        clean_dir(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    shutil.copytree(site_dir / "static", out_dir / "static", dirs_exist_ok=True)
    for rel, source in media.items():
        target = out_dir / "media" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    site = load_site_config(site_dir / "site.yaml")
    favicon = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<text x="50" y="52" font-size="82" text-anchor="middle" dominant-baseline="central">{html.escape(str(site["icon"]))}</text></svg>'
    )
    (out_dir / "favicon.svg").write_text(favicon, encoding="utf-8")

    env = Environment(
        loader=FileSystemLoader(site_dir / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    statuses_present = [s for s in STATUSES if any(c["status"] == s for c in cards)]
    page = env.get_template("index.html.j2").render(
        site=site,
        cards=cards,
        facets={"tags": build_facets(cards, "tags"), "stack": build_facets(cards, "stack")},
        statuses=[(s, STATUSES[s]) for s in statuses_present],
        assets={"css": file_hash(site_dir / "static" / "style.css"), "js": file_hash(site_dir / "static" / "app.js")},
        built_on=dt.date.today(),
    )
    (out_dir / "index.html").write_text(page, encoding="utf-8")
    (out_dir / "projects.json").write_text(
        json.dumps([public_record(c) for c in cards], indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nBuilt {len(cards)} project(s) -> {out_dir}  ({len(problems.warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
