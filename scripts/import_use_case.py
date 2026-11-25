#!/usr/bin/env python3
"""Turn a submitted use-case Word document (.docx) into a draft use-case card.

Colleagues submit use cases as Word documents (the use-case template). This script
reads the seven numbered sections, works out the ticked "main area", extracts the embedded
figures and writes a YAML card to cards/use-cases/ that the gallery build understands.

    python scripts/import_use_case.py path/to/some-idea.docx
    python scripts/import_use_case.py path/to/*.docx --force
    python scripts/import_use_case.py my.docx --slug forest-watch --out cards/use-cases

The result is a DRAFT. Please read it through before committing: add a one-line `quip` and,
if you like, a `summary`, and check the text for left-over template hints. Tags are NOT needed:
the build assigns them automatically from site/taxonomy.yaml.

Only the standard library and PyYAML (already required by the build) are used.
"""
from __future__ import annotations

import argparse
import re
import sys
import textwrap
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
V = "{urn:schemas-microsoft-com:vml}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "cards" / "use-cases"

AREAS = (  # label as printed in the Word template
    "Strategy, Governance and Organisation",
    "People, Skills and Ways of Working",
    "Data, Technologies and Infrastructure",
    "AI Applications and Value Creation",
)
# section number -> (heading starts with, card field)
SECTION_HEADINGS = {
    1: ("main area", None),
    2: ("challenge", "challenge"),
    3: ("proposed use", "proposed_ai"),
    4: ("scope", "scope"),
    5: ("expected outcome", "expected_outcome"),
    6: ("contribution", None),   # split into environmental / digital below
    7: ("key conditions", "conditions"),
}
# Instructions printed in the Word template that people sometimes leave behind.
GUIDANCE = tuple(re.compile(p, re.I) for p in (
    r"^describe the problem, need or opportunity",
    r"^explain how ai could help",
    r"^define what is included",
    r"^describe the concrete result",
    r"^explain how the use case supports both",
    r"^identify the most important requirements",
    r"^select the area to which",
    r"^focus on what ai would do",
    r"^focus on the change or benefit",
    r"^consider( whether the use case)?\s*:?$",
    r"^maximum:",
    r"^suggested formulation:?$",
    r"^typical technical infrastructure and integrations:?$",
))
SELECTED = set("■☒☑✔✓✗⊠▣þ")          # ticked-box glyphs (þ is Wingdings "checked")
LIST_STYLE = re.compile(r"list|lijst|odsek|liststycke|paragrafo|listen|lista|bullet", re.I)
CAPTION = re.compile(r"^\s*(?:pic|fig|figure|image|picture)\.?\s*\d+\s*[:.\-–]?\s*(.*)$", re.I)
STOP = re.compile(r"^example of the intended level of detail", re.I)
INLINE_KEYS = {"name", "quip", "caption", "affiliation", "country"}   # always written on one line


# --------------------------------------------------------------------------- #
# Reading the .docx
# --------------------------------------------------------------------------- #
def clean(text: str) -> str:
    for bad in ("​", "‎", "‏", "­", "﻿"):
        text = text.replace(bad, "")
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def paragraph_text(p) -> str:
    """Text of one paragraph, without descending into nested text boxes (those are paragraphs of their own)."""
    out: list[str] = []

    def walk(el):
        for child in el:
            if child.tag == W + "txbxContent":
                continue
            if child.tag == W + "t" and child.text:
                out.append(child.text)
            elif child.tag in (W + "tab", W + "br", W + "cr"):
                out.append(" ")
            elif child.tag == W + "sym":                 # symbol font glyphs, e.g. Wingdings check boxes
                code = (child.get(W + "char") or "").upper()[-2:]
                out.append("☒" if code in ("FE", "78", "FD") else "☐" if code in ("A8", "6F") else "")
            else:
                walk(child)

    walk(p)
    return clean("".join(out))


def read_docx(path: Path) -> tuple[list[dict], dict[str, bytes]]:
    """Paragraphs in reading order: text, bullet flag, style and the media files they contain."""
    with zipfile.ZipFile(path) as z:
        body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("word/_rels/document.xml.rels")).iter(REL + "Relationship")}
        media = {n: z.read(n) for n in z.namelist() if n.startswith("word/media/")}

    paragraphs = []
    for p in body.iter(W + "p"):
        ppr = p.find(W + "pPr")
        style = ""
        bullet = False
        if ppr is not None:
            st = ppr.find(W + "pStyle")
            style = st.get(W + "val") if st is not None else ""
            bullet = ppr.find(W + "numPr") is not None
        bullet = bullet or bool(LIST_STYLE.search(style or ""))

        images = []
        for blip in p.iter(A + "blip"):
            target = rels.get(blip.get(R + "embed"))
            if target:
                images.append("word/" + target.lstrip("/").removeprefix("word/"))
        for img in p.iter(V + "imagedata"):
            target = rels.get(img.get(R + "id"))
            if target:
                images.append("word/" + target.lstrip("/").removeprefix("word/"))
        paragraphs.append({"text": paragraph_text(p), "bullet": bullet, "style": style or "", "images": images})
    return paragraphs, media


# --------------------------------------------------------------------------- #
# Interpreting the template structure
# --------------------------------------------------------------------------- #
def is_guidance(text: str) -> bool:
    return any(g.search(text) for g in GUIDANCE)


def detect_area(lines: list[str]) -> str | None:
    """Which of the four areas is ticked? Handles ☒ ■ þ X markers or a single un-ticked line."""
    found = []   # (label, marker)
    for line in lines:
        low = line.casefold()
        for label in AREAS:
            at = low.find(label.casefold())
            if at >= 0:
                found.append((label, line[:at].strip()))
    ticked = [label for label, marker in found if marker and (marker[0] in SELECTED or marker.lower().strip("[]() ") in ("x", "✓"))]
    if ticked:
        return ticked[0]
    bare = [label for label, marker in found if not marker]
    if len(bare) == 1:                      # the author just typed the area, without check boxes
        return bare[0]
    return None


def split_contribution(paras: list[dict]) -> dict[str, list[dict]]:
    """Section 6 usually has an 'Environmental' and a 'Digital' part; sometimes just one text."""
    head = re.compile(r"^(environmental|environment|digital)(\s+(contribution|agenda))?\s*:?$", re.I)
    inline = re.compile(r"^(environmental|environment|digital)(\s+(contribution|agenda))?\s*:\s*(.+)$", re.I)
    parts: dict[str, list[dict]] = {"general": [], "environmental": [], "digital": []}
    current = "general"
    for p in paras:
        text = p["text"]
        if m := head.match(text):
            current = "digital" if m.group(1).lower() == "digital" else "environmental"
        elif m := inline.match(text):
            current = "digital" if m.group(1).lower() == "digital" else "environmental"
            parts[current].append({**p, "text": m.group(4)})
        else:
            parts[current].append(p)
    return parts


def to_text(paras: list[dict]) -> str:
    """Paragraphs -> the card's rich text: blank-line separated paragraphs and '- ' bullets."""
    lines: list[str] = []
    previous_bullet = None
    for p in paras:
        if lines and not (p["bullet"] and previous_bullet):
            lines.append("")
        lines.append(("- " if p["bullet"] else "") + p["text"])
        previous_bullet = p["bullet"]
    return "\n".join(lines)


def parse_submission(paragraphs: list[dict]) -> dict:
    """Split the paragraph stream into title and the numbered sections."""
    title_lines: list[str] = []
    sections: dict[int, list[dict]] = {}
    current: int | None = None
    notes: list[str] = []

    for i, p in enumerate(paragraphs):
        text = p["text"]
        if not text and not p["images"]:
            continue
        if STOP.match(text):
            break
        if m := re.match(r"^\s*([1-7])\s*[.)]\s*(.+)$", text):
            number, title = int(m.group(1)), m.group(2).casefold()
            if title.startswith(SECTION_HEADINGS[number][0]):
                if number in sections:
                    notes.append(f"section {number} appears twice - ignoring everything from the repeated heading on")
                    break
                sections[number] = []
                current = number
                continue
        if current is None:
            if text and not re.match(r"^template for drafting", text, re.I):
                title_lines.append(re.sub(r"^ai use case\s*:\s*", "", text, flags=re.I).strip(" \"“”"))
            continue
        sections[current].append({**p, "index": i})

    return {"title_lines": title_lines, "sections": sections, "notes": notes}


def attach_captions(sections: dict[int, list[dict]]) -> list[dict]:
    """Collect figures in reading order; a paragraph like 'Pic.3: ...' becomes the caption of the nearest figure."""
    figures = [{"media": m, "index": p["index"], "caption": ""} for paras in sections.values() for p in paras for m in p["images"]]
    for paras in sections.values():
        for p in paras:
            m = CAPTION.match(p["text"]) if p["text"] else None
            if m and figures:
                near = min(figures, key=lambda f: (abs(f["index"] - p["index"]) if f["index"] <= p["index"] else abs(f["index"] - p["index"]) + 0.5))
                if m.group(1) and not near["caption"]:
                    near["caption"] = m.group(1).strip()
    return figures


# --------------------------------------------------------------------------- #
# Writing the YAML card
# --------------------------------------------------------------------------- #
def y_line(key: str, value: str) -> str:
    """`key: value` on one line (quoted when plain style would be risky) or as a wrapped literal block."""
    value = " ".join(value.split()) if key in INLINE_KEYS else value.strip()
    if "\n" in value or (len(value) > 90 and key not in INLINE_KEYS):
        body: list[str] = []
        for line in value.split("\n"):
            if not line.strip():
                body.append("")
            elif line.startswith("- "):
                body += textwrap.wrap(line, 88, subsequent_indent="  ", break_long_words=False, break_on_hyphens=False)
            else:
                body += textwrap.wrap(line, 90, break_long_words=False, break_on_hyphens=False)
        return f"{key}: |\n" + "\n".join(("  " + b) if b else "" for b in body)
    safe = re.fullmatch(r"[^\W\d_][\w ,.()/&+–-]*", value) and not re.search(r"(?i)^(true|false|yes|no|null|on|off|y|n)$", value)
    if safe:
        return f"{key}: {value}"
    return f"{key}: " + '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_fields(parsed: dict) -> tuple[dict[str, str], list[str]]:
    """Card fields (as rich text) plus warnings for the person converting."""
    s, warnings = parsed["sections"], list(parsed["notes"])
    keep = lambda paras: [p for p in paras if p["text"] and not is_guidance(p["text"]) and not CAPTION.match(p["text"])]
    fields: dict[str, str] = {}

    area = detect_area([p["text"] for p in s.get(1, [])])
    if area is None:
        warnings.append("could not tell which main area is ticked - set `main_area` by hand")
    fields["main_area"] = area or ""

    for number, (_, field) in SECTION_HEADINGS.items():
        if field and number in s:
            fields[field] = to_text(keep(s[number]))
        elif field:
            warnings.append(f"section {number} ({SECTION_HEADINGS[number][0]}) is missing")
    if 6 in s:
        parts = split_contribution(keep(s[6]))
        if parts["environmental"] or parts["digital"]:
            fields["environmental_contribution"] = to_text(parts["environmental"])
            fields["digital_contribution"] = to_text(parts["digital"])
            if parts["general"]:
                warnings.append("section 6 has text before the Environmental/Digital parts - check where it belongs")
                fields["contribution"] = to_text(parts["general"])
        else:
            fields["contribution"] = to_text(parts["general"])
    return fields, warnings


def import_docx(path: Path, slug: str, out_dir: Path, force: bool = False, overrides: dict | None = None) -> tuple[Path, list[str]]:
    """Write cards/use-cases/<slug>.yaml and its figures. `overrides` can replace/extend fields (see curate scripts)."""
    overrides = dict(overrides or {})
    paragraphs, media = read_docx(path)
    parsed = parse_submission(paragraphs)
    fields, warnings = build_fields(parsed)
    figures = attach_captions(parsed["sections"])

    title = parsed["title_lines"][0] if parsed["title_lines"] else path.stem
    for key, value in overrides.items():           # a value of None removes a field
        if key not in ("name", "images", "meta"):
            fields.pop(key, None) if value is None else fields.__setitem__(key, value)
    name = overrides.get("name", title)

    target = out_dir / f"{slug}.yaml"
    if target.exists() and not force:
        raise FileExistsError(f"{target} exists - use --force to overwrite")

    # Figures: keep (and caption) the ones the caller wants; default is "all, in reading order".
    chosen = overrides.get("images")
    if chosen is None:
        chosen = [{"n": i} for i in range(len(figures))]
    image_entries = []
    for k, item in enumerate(chosen, 1):
        fig = figures[item["n"]]
        data = media.get(fig["media"])
        if data is None:
            warnings.append(f"figure {item['n']} points at a missing file in the document")
            continue
        suffix = Path(fig["media"]).suffix.lower() or ".png"
        rel = f"images/{slug}/figure-{k}{suffix}"
        (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (out_dir / rel).write_bytes(data)
        image_entries.append((rel, item.get("caption", fig["caption"])))

    meta = overrides.get("meta", {})     # quip, summary, affiliation, country, ...
    lines = [
        f"# Draft generated from {path.name}",
        "# Please review: add a short `quip`, check the text for left-over template hints.",
        "# Tags and technologies are added automatically at build time (site/taxonomy.yaml).",
        "",
        y_line("name", name),
    ]
    for key in ("quip", "summary"):
        if meta.get(key):
            lines.append(y_line(key, meta[key]))
    lines += ["", y_line("main_area", fields.pop("main_area") or "TODO - choose one of the four areas")]
    for key in ("affiliation", "country"):
        if meta.get(key):
            lines.append(y_line(key, meta[key]))
    lines.append("")
    for field in ("challenge", "proposed_ai", "scope", "expected_outcome",
                  "environmental_contribution", "digital_contribution", "contribution", "conditions"):
        if fields.get(field):
            lines += [y_line(field, fields[field]), ""]
    if image_entries:
        lines.append("images:")
        for rel, caption in image_entries:
            lines.append(f"  - file: {rel}")
            if caption:
                lines.append("    " + y_line("caption", caption))
        lines.append("")
    for key in ("submitted_on", "contact"):
        if meta.get(key):
            lines.append(f"{key}: {meta[key]}")

    out_dir.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    if not meta.get("quip"):
        warnings.append("no `quip` yet - add one short line describing the idea")
    if image_entries:
        warnings.append(f"{len(image_entries)} figure(s) copied to {out_dir.name}/images/{slug}/ - before publishing, check them for "
                        "personal data (names, e-mail addresses, screenshots of internal tools) and for third-party copyright; delete any that are not suitable")
    return target, warnings


def slugify(name: str) -> str:
    stem = re.sub(r"(?i)\b(ai[ _-]*)?use[ _-]*case\b", " ", Path(name).stem)
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "use-case"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("docx", nargs="+", type=Path, help="submitted Word document(s)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="folder for the cards (default: cards/use-cases)")
    parser.add_argument("--slug", help="file name for the card (only with a single document)")
    parser.add_argument("--force", action="store_true", help="overwrite existing cards")
    args = parser.parse_args()
    if args.slug and len(args.docx) > 1:
        parser.error("--slug works with one document at a time")

    status = 0
    for doc in args.docx:
        try:
            target, warnings = import_docx(doc, args.slug or slugify(doc.name), args.out, args.force)
        except (FileExistsError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            print(f"SKIPPED {doc.name}: {exc}")
            status = 1
            continue
        print(f"wrote {target.relative_to(ROOT) if ROOT in target.parents else target}")
        for w in warnings:
            print(f"   - {w}")
    return status


if __name__ == "__main__":
    sys.exit(main())
