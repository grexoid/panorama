#!/usr/bin/env python3
"""Build the static PANORAMA gallery.

All cards live below one root folder, ``cards/``, with one sub-folder per card type:

    cards/projects/*.yaml     running or finished projects  -> link to their own sites
    cards/use-cases/*.yaml    ideas and aspiring work       -> get an exposé page of their own

The script validates every card, adds automatic tags to use cases (see site/taxonomy.yaml)
and renders ``dist/`` from the Jinja2 templates in ``site/``.

    python scripts/build.py                          # cards/ -> dist/
    python scripts/build.py --cards resources/examples --out preview
    python scripts/build.py --strict                 # treat warnings as errors

    GALLERY_PASSWORD=... python scripts/build.py     # encrypt all pages and images (see README, "Password protection")

Exit status is non-zero when any card is invalid, so it doubles as the PR check.
"""
from __future__ import annotations

import argparse
import base64
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
from markupsafe import Markup, escape

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- #
# Card types and vocabularies
# --------------------------------------------------------------------------- #
# folder name below cards/  ->  card type
CARD_FOLDERS = {"projects": "project", "use-cases": "use-case"}
TYPE_LABELS = {"project": "Project", "use-case": "Use case"}
TYPE_PLURALS = {"project": "Projects", "use-case": "Use cases"}

STATUSES = {  # project lifecycle; use cases are always "idea"
    "active": "Active",
    "beta": "Beta",
    "maintenance": "Maintenance",
    "archived": "Archived",
}

# The four "main areas" of the use-case template: (key, full label, short label)
AREAS = (
    ("strategy", "Strategy, Governance and Organisation", "Strategy & governance"),
    ("people", "People, Skills and Ways of Working", "People & skills"),
    ("data", "Data, Technologies and Infrastructure", "Data & infrastructure"),
    ("applications", "AI Applications and Value Creation", "AI applications"),
)

# Text sections of a use case, in reading order: (field, heading)
SECTIONS = (
    ("challenge", "Challenge or opportunity"),
    ("proposed_ai", "Proposed use of AI"),
    ("scope", "Scope"),
    ("expected_outcome", "Expected outcome"),
    ("environmental_contribution", "Contribution to the environment"),
    ("digital_contribution", "Contribution to the digital agenda"),
    ("contribution", "Contribution to the environment and digital agenda"),
    ("conditions", "Key conditions and considerations"),
)
SECTION_FIELDS = tuple(f for f, _ in SECTIONS)

LINK_FIELDS = ("website", "repo_url", "demo_url", "docs_url")
IMAGE_FIELDS = ("screenshot_url", "thumbnail_url")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg"}
MAX_IMAGE_BYTES = 1_500_000

COMMON_FIELDS = {"name", "quip", "tags", "stack", "affiliation", "country", "contact", "featured", *IMAGE_FIELDS}
PROJECT_FIELDS = COMMON_FIELDS | {
    "description", "category", "status", "version", "license", "released_on", "updated_on", *LINK_FIELDS,
}
USE_CASE_FIELDS = COMMON_FIELDS | {
    "summary", "main_area", "country", "images", "submitted_on", "auto_tag", *SECTION_FIELDS,
}

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
            print(f"::{level} file={where},title=Card::{msg}")
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


def facet_item(label: str, with_hue: bool = False) -> dict:
    item = {"label": label, "key": facet_key(label)}
    if with_hue:
        item["hue"] = TAG_HUES[stable_int(label) % len(TAG_HUES)]
    return item


def check_url(value: str, where: str, field: str, problems: Problems) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        problems.error(where, f"'{field}' must be an absolute http(s) URL, got {value!r}")
        return None
    return value


def parse_rich(value, where: str, field: str, problems: Problems) -> list[dict]:
    """Turn a text section into display blocks.

    Accepts a YAML text (paragraphs separated by blank lines, lines starting with "- " are
    bullet points) or a YAML list (every entry is a bullet point). Returns blocks like
    ``{"kind": "p", "text": ...}`` and ``{"kind": "ul", "items": [...]}``.
    """
    if value in (None, ""):
        return []
    if isinstance(value, list):
        items = [" ".join(as_text(v).split()) for v in value if as_text(v)]
        return [{"kind": "ul", "items": items}] if items else []
    if not isinstance(value, str):
        problems.error(where, f"'{field}' must be text or a list of bullet points")
        return []

    blocks: list[dict] = []
    para: list[str] = []
    items: list[str] = []

    def end_para() -> None:
        if para:
            blocks.append({"kind": "p", "text": " ".join(" ".join(para).split())})
            para.clear()

    def end_list() -> None:
        if items:
            blocks.append({"kind": "ul", "items": list(items)})
            items.clear()

    for line in value.splitlines():
        s = line.strip()
        if not s:
            end_para()
            end_list()
        elif re.match(r"[-*•]\s+\S", s):
            end_para()
            items.append(s[1:].strip())
        elif items:  # continuation line of the previous bullet
            items[-1] += " " + s
        else:
            para.append(s)
    end_para()
    end_list()
    return blocks


def blocks_text(blocks: list[dict]) -> str:
    return " ".join(b["text"] if b["kind"] == "p" else " ".join(b["items"]) for b in blocks)


def excerpt(text: str, limit: int = 240) -> str:
    """First sentences of `text`, at most `limit` characters, cut at a sentence/word boundary."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    if end > limit * 0.5:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"


def linkify(text: str) -> Markup:
    """HTML-escape `text` and turn bare http(s) addresses into links."""
    out, last = [], 0
    for m in re.finditer(r"https?://[^\s<>\"']*[^\s<>\"'.,;:!?)\]]", text):
        out.append(escape(text[last:m.start()]))
        out.append(Markup('<a href="{0}" target="_blank" rel="noopener">{0}</a>').format(m.group(0)))
        last = m.end()
    out.append(escape(text[last:]))
    return Markup("").join(out)


def asset_url(url: str | None, root: str = "") -> str:
    """Prefix site-relative asset paths with `root` (pages in sub-folders); leave https:// URLs alone."""
    if not url:
        return ""
    return url if urlparse(url).scheme else f"{root}{url}"


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #
def resolve_image(value, where, field, folder: str, folder_dir: Path, media: dict[str, Path], problems):
    """Return the URL to use for an image; local files (relative to the card folder) are queued for copying."""
    value = as_text(value)
    if not value:
        return None
    if urlparse(value).scheme in ("http", "https"):
        return check_url(value, where, field, problems)
    if urlparse(value).scheme or value.startswith(("/", "\\")):
        problems.error(where, f"'{field}' must be an http(s) URL or a path relative to the {folder} folder")
        return None
    source = (folder_dir / value).resolve()
    if folder_dir.resolve() not in source.parents:
        problems.error(where, f"'{field}' points outside the {folder} folder: {value}")
        return None
    if not source.is_file():
        problems.error(where, f"'{field}' file not found: {value}")
        return None
    if source.suffix.lower() not in IMAGE_SUFFIXES:
        problems.error(where, f"'{field}' must be one of {', '.join(sorted(IMAGE_SUFFIXES))}")
        return None
    if source.stat().st_size > MAX_IMAGE_BYTES:
        problems.warn(where, f"'{field}' is {source.stat().st_size // 1024} kB - please keep images under {MAX_IMAGE_BYTES // 1000} kB")
    rel = f"{folder}/{source.relative_to(folder_dir.resolve()).as_posix()}"
    media[rel] = source
    return f"media/{rel}"


# --------------------------------------------------------------------------- #
# Loading cards
# --------------------------------------------------------------------------- #
def common_part(raw: dict, where: str, folder: str, folder_dir: Path, media, problems) -> dict:
    """Fields shared by all card types."""
    name = as_text(raw.get("name"))
    if not name:
        problems.error(where, "missing required field 'name'")

    featured = raw.get("featured", False)
    if not isinstance(featured, bool):
        problems.error(where, "'featured' must be true or false")
        featured = False

    contact_raw = as_text(raw.get("contact"))
    contact_name, contact_email = contact_raw, ""
    if m := re.fullmatch(r"(.*?)\s*<([^<>@\s]+@[^<>@\s]+)>", contact_raw):
        contact_name, contact_email = m.group(1) or m.group(2), m.group(2)

    return {
        "name": name,
        "quip": as_text(raw.get("quip")),
        "featured": featured,
        "affiliation": as_text(raw.get("affiliation")),
        "contact_name": contact_name,
        "contact_email": contact_email,
        "tag_labels": as_list(raw.get("tags"), where, "tags", problems),
        "stack_labels": as_list(raw.get("stack"), where, "stack", problems),
        "country_labels": as_list(raw.get("country"), where, "country", problems),   # one, a comma list or a YAML list
        "screenshot": resolve_image(raw.get("screenshot_url"), where, "screenshot_url", folder, folder_dir, media, problems),
        "thumbnail": resolve_image(raw.get("thumbnail_url"), where, "thumbnail_url", folder, folder_dir, media, problems),
        "hue": stable_int(name) % 360,
        "initials": initials(name),
    }


def project_part(raw: dict, where: str, problems: Problems) -> dict:
    description = as_text(raw.get("description"))
    if not description:
        problems.error(where, "missing required field 'description'")

    links = {f: check_url(as_text(raw.get(f)), where, f, problems) for f in LINK_FIELDS}
    if not (links["website"] or links["repo_url"]):
        problems.error(where, "provide at least one of 'website' or 'repo_url' (a full address starting with https://)")

    status = as_text(raw.get("status")).lower() or "active"
    if status not in STATUSES:
        problems.error(where, f"'status' must be one of {', '.join(STATUSES)}, got {status!r}")
        status = "active"

    released = as_date(raw.get("released_on"), where, "released_on", problems)
    updated = as_date(raw.get("updated_on"), where, "updated_on", problems) or released
    if released and updated and updated < released:
        problems.warn(where, "'updated_on' is earlier than 'released_on'")

    return {
        "paragraphs": [" ".join(p.split()) for p in re.split(r"\n\s*\n", description) if p.strip()],
        "category": as_text(raw.get("category")),
        "status": status,
        "status_label": STATUSES[status],
        "version": as_text(raw.get("version")),
        "license": as_text(raw.get("license")),
        "released": released,
        "updated": updated,
        "links": links,
        "search_extra": " ".join([description, as_text(raw.get("category"))]),
    }


def area_for(value: str):
    """Match a main-area entry given as full label, short label or key."""
    v = " ".join(value.split()).casefold()
    for area in AREAS:
        if v in (area[0], area[1].casefold(), area[2].casefold()):
            return area
    return None


def use_case_part(raw: dict, where: str, folder: str, folder_dir: Path, media, problems: Problems) -> dict:
    area = area_for(as_text(raw.get("main_area")))
    if area is None:
        shown = as_text(raw.get("main_area"))
        problems.error(
            where,
            ("'main_area' is missing" if not shown else f"'main_area' {shown!r} is not recognised")
            + " - choose exactly one of: " + "; ".join(a[1] for a in AREAS),
        )

    sections = {f: parse_rich(raw.get(f), where, f, problems) for f in SECTION_FIELDS}
    for field in ("challenge", "proposed_ai"):
        if not sections[field]:
            problems.error(where, f"missing required field '{field}'")

    # Optional figures taken from the submission: a list of paths/URLs, optionally with captions.
    images = []
    entries = raw.get("images") or []
    if not isinstance(entries, list):
        problems.error(where, "'images' must be a list")
        entries = []
    for i, entry in enumerate(entries, 1):
        source, caption = (entry.get("file") or entry.get("url"), as_text(entry.get("caption"))) if isinstance(entry, dict) else (entry, "")
        url = resolve_image(source, where, f"images[{i}]", folder, folder_dir, media, problems)
        if url:
            images.append({"url": url, "caption": caption})

    submitted = as_date(raw.get("submitted_on"), where, "submitted_on", problems)
    auto_tag = raw.get("auto_tag", True)
    if not isinstance(auto_tag, bool):
        problems.error(where, "'auto_tag' must be true or false")
        auto_tag = True

    ordered = [(title, field, sections[field]) for field, title in SECTIONS if sections[field]]
    summary = as_text(raw.get("summary")) or (excerpt(blocks_text(sections["proposed_ai"])) if sections["proposed_ai"] else "")
    body_text = " ".join(blocks_text(blocks) for _, _, blocks in ordered)

    # Environment + digital contribution are shown side by side; everything else gets a row of its own.
    groups: list[dict] = []
    for title, field, blocks in ordered:
        entry = {"title": title, "field": field, "blocks": blocks}
        if field == "digital_contribution" and groups and groups[-1]["sections"][-1]["field"] == "environmental_contribution":
            groups[-1]["sections"].append(entry)
        else:
            groups.append({"sections": [entry]})

    return {
        "initials": "💡",
        "groups": groups,
        "area": area,
        "area_key": area[0] if area else "",
        "area_label": area[1] if area else "",
        "area_short": area[2] if area else "",
        "summary": summary,
        "sections": [{"title": t, "field": f, "blocks": b} for t, f, b in ordered],
        "images": images,
        "paragraphs": [],
        "auto_tag": auto_tag,
        "status": "idea",
        "status_label": "Idea",
        "released": submitted,
        "updated": submitted,
        "body_text": body_text,
        "search_extra": body_text,
    }


def load_card(path: Path, folder: str, folder_dir: Path, media, problems: Problems) -> dict | None:
    where = path.relative_to(ROOT).as_posix() if ROOT in path.parents else path.as_posix()
    n_errors = len(problems.errors)
    ctype = CARD_FOLDERS[folder]

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
            f"or a missing closing quote. See the notes at the top of resources/templates/{ctype}-card.yaml.",
        )
        return None
    if not isinstance(raw, dict):
        problems.error(where, f"the file must consist of 'field: value' lines - compare it with resources/templates/{ctype}-card.yaml")
        return None

    known = PROJECT_FIELDS if ctype == "project" else USE_CASE_FIELDS
    for key in raw.keys() - known:
        problems.warn(where, f"unknown field '{key}' is ignored - check the spelling against the template")

    card = common_part(raw, where, folder, folder_dir, media, problems)
    card.update(project_part(raw, where, problems) if ctype == "project" else use_case_part(raw, where, folder, folder_dir, media, problems))

    if len(problems.errors) > n_errors:
        return None

    card.update({
        "slug": path.stem,
        "type": ctype,
        "type_label": TYPE_LABELS[ctype],
        "page": f"use-cases/{path.stem}/" if ctype == "use-case" else None,
    })
    card["cover"] = card["screenshot"] or (card["images"][0]["url"] if card.get("images") else None)
    return card


# --------------------------------------------------------------------------- #
# Automatic tagging (use cases)
# --------------------------------------------------------------------------- #
def compile_keyword(keyword: str) -> re.Pattern:
    """'hydrolog*' matches word beginnings, ALL-CAPS acronyms match case-sensitively, the rest ignores case."""
    stem = keyword.rstrip("*")
    body = r"[\s\-]+".join(re.escape(w) for w in stem.split())
    if keyword.endswith("*"):
        body += r"\w*"
    flags = 0 if (stem.isupper() and len(stem) >= 2) else re.IGNORECASE
    return re.compile(rf"(?<!\w){body}(?!\w)", flags)


def load_taxonomy(path: Path) -> dict:
    """Read site/taxonomy.yaml into compiled matchers. Missing file = no automatic tagging."""
    empty = {"tags": [], "technologies": [], "settings": {"min_score": 3, "min_score_technologies": 2, "max_tags": 6, "max_technologies": 5}}
    if not path.is_file():
        return empty
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = {**empty["settings"], **(data.get("settings") or {})}

    def compile_group(group: dict | None) -> list[dict]:
        out = []
        for label, spec in (group or {}).items():
            keywords = spec if isinstance(spec, list) else spec.get("keywords", [])
            minimum = None if isinstance(spec, list) else spec.get("min")
            out.append({"label": str(label), "patterns": [compile_keyword(str(k)) for k in keywords], "min": minimum})
        return out

    return {"tags": compile_group(data.get("tags")), "technologies": compile_group(data.get("technologies")), "settings": settings}


def score_group(group: list[dict], priority_text: str, body_text: str, default_min: int, limit: int) -> list[str]:
    """Rank taxonomy entries by how strongly their keywords occur; mentions in name/quip count extra."""
    scored = []
    for entry in group:
        score = 0
        for pattern in entry["patterns"]:
            score += min(len(pattern.findall(body_text)), 3)
            if pattern.search(priority_text):
                score += 3
        if score >= (entry["min"] or default_min):
            scored.append((-score, entry["label"].casefold(), entry["label"]))
    return [label for _, _, label in sorted(scored)[:limit]]


def apply_auto_tags(cards: list[dict], taxonomy: dict) -> None:
    s = taxonomy["settings"]
    for card in cards:
        card["auto_tags"], card["auto_stack"] = [], []
        if card["type"] != "use-case" or not card["auto_tag"]:
            continue
        priority = f"{card['name']} {card['quip']} {card['summary']}"
        body = f"{priority} {card['body_text']}"
        explicit_tags = {t.casefold() for t in card["tag_labels"]}
        explicit_stack = {t.casefold() for t in card["stack_labels"]}
        card["auto_tags"] = [t for t in score_group(taxonomy["tags"], priority, body, s["min_score"], s["max_tags"]) if t.casefold() not in explicit_tags]
        card["auto_stack"] = [t for t in score_group(taxonomy["technologies"], priority, body, s["min_score_technologies"], s["max_technologies"]) if t.casefold() not in explicit_stack]


# --------------------------------------------------------------------------- #
# Site assembly
# --------------------------------------------------------------------------- #
def finish_cards(cards: list[dict]) -> None:
    """Combine explicit and automatic tags, build the search text, and sort."""
    for card in cards:
        card["updated_text"] = fmt_date(card["updated"]) if card["updated"] else ""
        card["released_text"] = fmt_date(card["released"]) if card["released"] else ""
        card["tags"] = [facet_item(t, True) for t in [*card["tag_labels"], *card["auto_tags"]]]
        card["stack"] = [facet_item(t) for t in [*card["stack_labels"], *card["auto_stack"]]]
        card["countries"] = [facet_item(c) for c in card["country_labels"]]
        card["country"] = ", ".join(card["country_labels"])
        card["search"] = " ".join(
            [card["name"], card["quip"], card["type_label"], card["affiliation"], card["country"], card.get("area_label", ""),
             card["status_label"], card["search_extra"], *[t["label"] for t in card["tags"]], *[t["label"] for t in card["stack"]]]
        ).casefold()
    cards.sort(key=lambda c: (not c["featured"], -(c["updated"] or dt.date.min).toordinal(), c["type"] != "project", c["name"].casefold()))


def build_facets(cards: list[dict], field: str) -> list[dict]:
    counts: dict[str, dict] = {}
    for card in cards:
        for item in card[field]:
            entry = counts.setdefault(item["key"], {**item, "count": 0})
            entry["count"] += 1
    return sorted(counts.values(), key=lambda e: (-e["count"], e["label"].casefold()))


def build_area_facet(cards: list[dict]) -> list[dict]:
    return [
        {"key": key, "label": short, "title": label, "count": n}
        for key, label, short in AREAS
        if (n := sum(1 for c in cards if c.get("area_key") == key))
    ]


def related_use_cases(card: dict, use_cases: list[dict], limit: int = 3) -> list[dict]:
    mine = {t["key"] for t in card["tags"]}
    scored = []
    for other in use_cases:
        if other is card:
            continue
        shared = len(mine & {t["key"] for t in other["tags"]}) + (1 if other["area_key"] == card["area_key"] else 0)
        if shared:
            scored.append((-shared, other["name"].casefold(), other))
    return [c for _, _, c in sorted(scored, key=lambda t: t[:2])[:limit]]


def load_site_config(path: Path) -> dict:
    config = {
        "title": "Project Gallery",
        "title_expansion": "",
        "heading": "Projects",
        "tagline": "",
        "icon": "🗂️",
        "search_placeholder": "Search projects, tags, technologies…",
        "repository": "",
        "branch": "main",
        "contribute_url": "",
        "footer": "",
    }
    if path.is_file():
        config.update({k: v for k, v in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items() if v is not None})
    # The repository ("owner/name") drives the "Edit on GitHub" links; GitHub Actions provides it automatically.
    config["repository"] = str(config["repository"] or os.environ.get("GITHUB_REPOSITORY", "")).strip().strip("/")
    config["repo_url"] = f"https://github.com/{config['repository']}" if config["repository"] else ""
    if config["repo_url"] and not config["contribute_url"]:
        config["contribute_url"] = f"{config['repo_url']}#adding-a-card"
    return config


def emphasis(text: str) -> Markup:
    """HTML-escape `text` and turn **double-asterisk** parts into bold (used for the title's backronym)."""
    return Markup(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", str(escape(text))))


def unbold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", str(text))


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
    record = {
        "type": card["type"], "slug": card["slug"], "name": card["name"], "quip": card["quip"],
        "tags": [t["label"] for t in card["tags"]], "technologies": [t["label"] for t in card["stack"]],
        "status": card["status"], "featured": card["featured"], "affiliation": card["affiliation"],
        "countries": [c["label"] for c in card["countries"]],
        "released_on": card["released"].isoformat() if card["released"] else None,
        "updated_on": card["updated"].isoformat() if card["updated"] else None,
        "screenshot": card["screenshot"], "thumbnail": card["thumbnail"],
    }
    if card["type"] == "project":
        record.update({
            "description": "\n\n".join(card["paragraphs"]), "category": card["category"], "version": card["version"],
            "license": card["license"], "links": {k: v for k, v in card["links"].items() if v},
        })
    else:
        record.update({
            "page": card["page"], "main_area": card["area_label"], "summary": card["summary"],
            "images": card["images"], "auto_tags": card["auto_tags"], "auto_technologies": card["auto_stack"],
            "sections": {s["field"]: blocks_text(s["blocks"]) for s in card["sections"]},
        })
    return record


# --------------------------------------------------------------------------- #
# Password protection
# --------------------------------------------------------------------------- #
PASSWORD_ENV = "GALLERY_PASSWORD"
PBKDF2_ITERATIONS = 600_000            # OWASP recommendation for PBKDF2-HMAC-SHA256
MEDIA_REF = re.compile(r'\b(src|href)="((?:\.\./)*media/[^"]+)"')


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class Vault:
    """AES-256-GCM, with the key stretched from the site password by PBKDF2-HMAC-SHA256.

    The salt is fixed per build, so a browser derives the key once per session and then opens every page and
    image. Each encrypted blob carries its own random 96-bit IV in front of the ciphertext."""

    def __init__(self, password: str) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("Password protection needs the 'cryptography' package: pip install -r requirements.txt") from exc
        self.salt = os.urandom(16)
        self._aes = AESGCM(hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), self.salt, PBKDF2_ITERATIONS, 32))

    def seal(self, data: bytes) -> bytes:
        iv = os.urandom(12)
        return iv + self._aes.encrypt(iv, data, None)

    def payload(self, page: str) -> str:
        """JSON for the gate page: everything a browser needs, apart from the password."""
        return json.dumps({"v": 1, "iter": PBKDF2_ITERATIONS, "salt": b64(self.salt), "data": b64(self.seal(page.encode("utf-8")))})


def lock_media(page: str) -> str:
    """Point a page at the encrypted copies of its images; static/secure.js decrypts them in the browser."""
    return MEDIA_REF.sub(lambda m: f'data-enc-{m.group(1)}="{m.group(2)}.enc"', page)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cards", type=Path, default=ROOT / "cards", help="root folder with the card folders (default: cards)")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output folder (default: dist)")
    parser.add_argument("--site", type=Path, default=ROOT / "site", help="folder with templates, static files, site.yaml and taxonomy.yaml")
    parser.add_argument("--strict", action="store_true", help="fail on warnings as well as errors")
    parser.add_argument("--require-password", action="store_true", help=f"fail unless {PASSWORD_ENV} is set (used when deploying)")
    args = parser.parse_args()

    cards_root = args.cards.resolve()
    out_dir = args.out.resolve()
    site_dir = args.site.resolve()
    problems = Problems()

    # Password protection is switched on by an environment variable, so the password never lands in the repository.
    password = os.environ.get(PASSWORD_ENV, "")
    if args.require_password and not password:
        print(f"Refusing to build an unprotected site: the environment variable {PASSWORD_ENV} is empty or not set.\n"
              f"On GitHub: Settings -> Secrets and variables -> Actions -> New repository secret named {PASSWORD_ENV}.")
        return 1
    vault = Vault(password) if password else None
    print(f"Password protection: {'ON - pages and images are encrypted' if vault else 'OFF (set ' + PASSWORD_ENV + ' to switch it on)'}")

    # --- read cards ---------------------------------------------------------
    media: dict[str, Path] = {}
    cards: list[dict] = []
    for folder in CARD_FOLDERS:
        folder_dir = cards_root / folder
        files = sorted(p for p in folder_dir.glob("*.y*ml") if p.suffix in (".yaml", ".yml") and not p.name.startswith("_")) if folder_dir.is_dir() else []
        print(f"Reading {len(files):3d} {folder:9} card(s) from {folder_dir}")
        cards += [c for f in files if (c := load_card(f, folder, folder_dir, media, problems))]
    if cards_root.is_dir():
        for extra in sorted(cards_root.iterdir()):
            if extra.name not in CARD_FOLDERS and not extra.name.startswith(("_", ".")):
                problems.warn(extra.relative_to(ROOT).as_posix() if ROOT in extra.parents else extra.as_posix(),
                              f"ignored - cards belong in {' or '.join(f'cards/{f}/' for f in CARD_FOLDERS)}")
    else:
        problems.error(str(cards_root), "the cards folder does not exist")

    if problems.errors or (args.strict and problems.warnings):
        print(f"\nBuild failed: {len(problems.errors)} error(s), {len(problems.warnings)} warning(s).")
        return 1
    if not cards:
        # Not an error: a fresh repository has no cards yet and the site shows a friendly empty state.
        print("Note: no cards found - the gallery will be empty.")

    taxonomy = load_taxonomy(site_dir / "taxonomy.yaml")
    apply_auto_tags(cards, taxonomy)
    finish_cards(cards)
    use_cases = [c for c in cards if c["type"] == "use-case"]
    for card in use_cases:
        card["related"] = related_use_cases(card, use_cases)

    # --- output folder ------------------------------------------------------
    if out_dir.exists():
        if out_dir in (ROOT, site_dir, cards_root) or ROOT.parent == out_dir or out_dir == Path(out_dir.anchor):
            print(f"Refusing to clear {out_dir}")
            return 1
        clean_dir(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    shutil.copytree(site_dir / "static", out_dir / "static", dirs_exist_ok=True)
    for rel, source in media.items():
        target = out_dir / "media" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if vault:                      # only the encrypted copy is published
            target.with_name(target.name + ".enc").write_bytes(vault.seal(source.read_bytes()))
        else:
            shutil.copy2(source, target)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    site = load_site_config(site_dir / "site.yaml")
    folder_of = {ctype: folder for folder, ctype in CARD_FOLDERS.items()}
    for card in cards:   # link to GitHub's editor for the card's file (empty when the repository is unknown)
        card["edit_url"] = (f"{site['repo_url']}/edit/{site['branch']}/cards/{folder_of[card['type']]}/{card['slug']}.yaml"
                            if site["repo_url"] else "")
    favicon = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<text x="50" y="52" font-size="82" text-anchor="middle" dominant-baseline="central">{html.escape(str(site["icon"]))}</text></svg>'
    )
    (out_dir / "favicon.svg").write_text(favicon, encoding="utf-8")

    # --- render -------------------------------------------------------------
    env = Environment(
        loader=FileSystemLoader(site_dir / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["linkify"] = linkify
    env.filters["asset"] = asset_url
    env.filters["emphasis"] = emphasis
    env.filters["unbold"] = unbold

    projects = [c for c in cards if c["type"] == "project"]
    statuses_present = [s for s in STATUSES if any(c["status"] == s for c in projects)]
    common = {
        "site": site,
        "assets": {key: file_hash(site_dir / "static" / name) for key, name in
                   (("css", "style.css"), ("js", "app.js"), ("gate", "gate.js"), ("secure", "secure.js"))},
        "built_on": dt.date.today(),
        "counts": {"all": len(cards), "project": len(projects), "use-case": len(use_cases)},
        "encrypted": vault is not None,
    }

    def publish(path: Path, page: str, root: str) -> None:
        """Write a page - or, when protected, a small gate page that carries it encrypted."""
        if vault:
            page = env.get_template("gate.html.j2").render(**common, root=root, payload=vault.payload(lock_media(page)))
        path.write_text(page, encoding="utf-8")

    publish(out_dir / "index.html", env.get_template("index.html.j2").render(
        **common, root="", cards=cards,
        facets={"tags": build_facets(cards, "tags"), "stack": build_facets(cards, "stack"),
                "countries": build_facets(cards, "countries"), "areas": build_area_facet(cards)},
        statuses=[(s, STATUSES[s]) for s in statuses_present],
    ), "")

    expose = env.get_template("use-case.html.j2")
    for card in use_cases:
        target = out_dir / card["page"]
        target.mkdir(parents=True, exist_ok=True)
        publish(target / "index.html", expose.render(**common, root="../../", card=card, areas=AREAS), "../../")

    if not vault:   # the JSON holds every card's full text, so it is not published for a protected site
        (out_dir / "projects.json").write_text(
            json.dumps([public_record(c) for c in cards], indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )

    print(f"\nBuilt {len(projects)} project(s) and {len(use_cases)} use case(s) -> {out_dir}  ({len(problems.warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
