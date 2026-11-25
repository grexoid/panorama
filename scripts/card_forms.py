#!/usr/bin/env python3
"""The requests behind the "Update" and "Promote" buttons on the cards - defined once.

A button on a card opens a new GitHub issue whose text is already filled in with the card's content: one heading per
field, the current value below it. The person edits the text and presses Create; the "Card requests" workflow reads the
text, applies the change to the card and replies with a link that opens the pull request. (GitHub's structured issue
*forms* are not used on purpose: they lock pre-filled boxes, so nothing could be edited. A plain issue text can.)

This file defines, for each kind of request, the headings (fields), builds the pre-filled text and the button address
(scripts/build.py), and parses the text that comes back (scripts/card_request.py).

    python scripts/card_forms.py        # prints an example request text
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from urllib.parse import quote

KEEP = "(keep)"                      # in a choice field: leave the card as it is
REMOVE = "-"                         # typing a single dash in a field removes that field from the card
STATUSES = ("beta", "active", "maintenance", "archived")
MAIN_AREAS = (                       # the four areas of the use-case template (a test keeps this equal to build.AREAS)
    "Strategy, Governance and Organisation",
    "People, Skills and Ways of Working",
    "Data, Technologies and Infrastructure",
    "AI Applications and Value Creation",
)
MAX_URL = 7400                       # GitHub refuses addresses much beyond 8000 characters
TOO_LONG = "too long to pre-fill: leave empty to keep the current text, or write the new text here"
COMMENT = re.compile(r"<!--.*?-->", re.S)


@dataclass(frozen=True)
class Field:
    id: str                          # the card's field name
    label: str                       # the heading in the request text
    kind: str = "input"              # input | textarea | choice (one of `options`)
    hint: str = ""                   # short note shown under the heading (as a comment: not part of the issue)
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Form:
    key: str
    title_prefix: str                # every request starts with this; the workflow recognises the kind of request by it
    kind: str                        # "project" or "use-case": the kind of card the request is about
    action: str                      # "update" or "promote"
    intro: str                       # shown at the top of the request text
    fields: tuple[Field, ...]


CARD = Field("card", "Card (file name)", hint="do not change")
PICTURE_HINT = "drag a picture file into this box to replace it (png, jpg, gif, webp up to 1.5 MB), or give an address; a dash removes it"
HOW_TO_EDIT = (
    "Change the text under the headings, then press **Create**. A section you leave as it is keeps that part of the card; "
    "type a single dash (`-`) to remove a value; empty sections are ignored. "
    "A workflow then prepares the change and replies here with a link to open the pull request "
    "(only repository collaborators can run it). Please keep the `###` headings as they are."
)

UPDATE_PROJECT = Form(
    key="update-project", title_prefix="Update project: ", kind="project", action="update",
    intro="**Update a project card.** " + HOW_TO_EDIT,
    fields=(
        CARD,
        Field("name", "Name"),
        Field("quip", "Teaser"),
        Field("description", "Description", "textarea", "one short paragraph, at most 500 characters"),
        Field("website", "Website"),
        Field("repo_url", "Repository"),
        Field("demo_url", "Demo"),
        Field("docs_url", "Documentation"),
        Field("screenshot_url", "Screenshot", hint=PICTURE_HINT + "; landscape, about 1200 x 600"),
        Field("thumbnail_url", "Logo", hint=PICTURE_HINT + "; square, about 128 x 128"),
        Field("status", "Status", "choice", options=(KEEP, *STATUSES)),
        Field("version", "Version"),
        Field("license", "License"),
        Field("category", "Category"),
        Field("country", "Country", hint="several: separate with commas"),
        Field("affiliation", "Organisation"),
        Field("contact", "Contact", hint="Name <email>"),
        Field("tags", "Tags", hint="separate with commas"),
        Field("stack", "Technologies", hint="separate with commas"),
    ),
)

UPDATE_USE_CASE = Form(
    key="update-use-case", title_prefix="Update use case: ", kind="use-case", action="update",
    intro="**Update a use case.** " + HOW_TO_EDIT,
    fields=(
        CARD,
        Field("name", "Name"),
        Field("quip", "Teaser"),
        Field("summary", "Summary", "textarea"),
        Field("main_area", "Main area", "choice", options=(KEEP, *MAIN_AREAS)),
        Field("country", "Country", hint="several: separate with commas"),
        Field("affiliation", "Organisation"),
        Field("contact", "Contact", hint="Name <email>"),
        Field("tags", "Tags", hint="your own tags, separate with commas; automatic ones are added"),
        Field("stack", "Technologies", hint="your own, separate with commas; automatic ones are added"),
        Field("challenge", "Challenge or opportunity", "textarea"),
        Field("proposed_ai", "Proposed use of AI", "textarea"),
        Field("scope", "Scope", "textarea", "lines starting with '- ' are bullet points"),
        Field("expected_outcome", "Expected outcome", "textarea"),
        Field("environmental_contribution", "Contribution to the environment", "textarea"),
        Field("digital_contribution", "Contribution to the digital agenda", "textarea"),
        Field("conditions", "Key conditions and considerations", "textarea", "lines starting with '- ' are bullet points"),
        Field("images", "Images", "textarea",
              "drag picture files into this box to ADD them to the figures (existing ones stay; a dash removes all); "
              "text under a picture becomes its caption. Only png, jpg, gif, webp up to 1.5 MB"),
    ),
)

PROMOTE_USE_CASE = Form(
    key="promote-use-case", title_prefix="Promote use case: ", kind="use-case", action="promote",
    intro=("**Promote a use case to a project** - the idea has become real work. "
           "Check the pre-filled text, add the project's **website and/or repository**, choose its status, then press **Create**. "
           "Everything can be changed; a dash (`-`) or an empty section means \"not given\". "
           "The use case's long sections (challenge, scope, conditions ...) have no place on a project card; they stay in the repository history. "
           "A workflow prepares the change and replies here with a link to open the pull request (only repository collaborators can run it). "
           "Please keep the `###` headings as they are."),
    fields=(
        CARD,
        Field("name", "Name"),
        Field("quip", "Teaser"),
        Field("description", "Description", "textarea", "pre-filled from the use case; one short paragraph, at most 500 characters"),
        Field("website", "Website", hint="give this and/or the repository"),
        Field("repo_url", "Repository"),
        Field("demo_url", "Demo", hint="optional"),
        Field("docs_url", "Documentation", hint="optional"),
        Field("status", "Status", "choice", options=STATUSES),
        Field("country", "Country", hint="several: separate with commas"),
        Field("affiliation", "Organisation"),
        Field("contact", "Contact", hint="Name <email>"),
        Field("tags", "Tags", hint="projects are not tagged automatically: the use case's tags as they are now, separate with commas"),
        Field("stack", "Technologies", hint="separate with commas"),
    ),
)

FORMS: dict[str, Form] = {f.key: f for f in (UPDATE_PROJECT, UPDATE_USE_CASE, PROMOTE_USE_CASE)}


# --------------------------------------------------------------------------- #
# The request text (what a button pre-fills)
# --------------------------------------------------------------------------- #
def request_body(form: Form, values: dict[str, str] | None = None, omitted: frozenset[str] | set[str] = frozenset()) -> str:
    """The text of a request: the intro, then a heading per field with its value below.

    `omitted` fields get no value (they were too long for the button's address); the note under their heading says an
    empty section keeps the current text."""
    values = values or {}
    parts = [form.intro, ""]
    for f in form.fields:
        parts.append(f"### {f.label}")
        if f.id in omitted:
            parts.append(f"<!-- {TOO_LONG} -->")
        elif f.kind == "choice":
            parts.append(f"<!-- one of: {' | '.join(f.options)} -->")
        elif f.hint:
            parts.append(f"<!-- {f.hint} -->")
        value = "" if f.id in omitted else (values.get(f.id) or "").strip()
        if f.kind == "choice" and not value:
            value = f.options[0]
        parts += [value, ""]
    return "\n".join(parts).rstrip() + "\n"


def prefill_url(repo_url: str, form: Form, slug: str, values: dict[str, str]) -> str:
    """Address of a new issue on GitHub whose title and text are filled in for this card.

    Addresses have a length limit. Long texts are added shortest first, and the ones that no longer fit are left out
    of the text (marked, so that an empty section means "keep the current text")."""
    def build(included: set[str], omitted: set[str]) -> str:
        shown = {k: v for k, v in values.items() if k in included}
        body = request_body(form, {"card": slug, **shown}, omitted)
        return f"{repo_url}/issues/new?title={quote(form.title_prefix + slug, safe='')}&body={quote(body, safe='')}"

    candidates = sorted((f.id for f in form.fields if f.id != "card" and (values.get(f.id) or "").strip()),
                        key=lambda k: len(values[k]))
    included: set[str] = set()
    for field_id in candidates:                    # shortest first; the ones not added yet count as "left out"
        trial = included | {field_id}
        if len(build(trial, set(candidates) - trial)) <= MAX_URL:
            included.add(field_id)
    return build(included, set(candidates) - included)


# --------------------------------------------------------------------------- #
# Submitted requests
# --------------------------------------------------------------------------- #
def form_for_title(title: str) -> Form | None:
    return next((f for f in FORMS.values() if title.startswith(f.title_prefix)), None)


def parse_issue_body(form: Form, body: str) -> dict[str, str]:
    """Field id -> the text under its heading, from the text of an issue created from a request. Missing/empty give ''.

    Only the form's own headings separate sections, so a `###` inside a long text does not cut it short."""
    text = COMMENT.sub("", body.replace("\r\n", "\n").replace("\r", "\n"))
    by_label = {f.label.casefold(): f.id for f in form.fields}
    heading = re.compile(r"(?m)^#{3}[ \t]+(" + "|".join(re.escape(f.label) for f in form.fields) + r")[ \t]*$", re.I)
    values = {f.id: "" for f in form.fields}
    marks = list(heading.finditer(text))
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        values[by_label[mark.group(1).casefold()]] = text[mark.end():end].strip()
    return values


def parse_choice(field: Field, value: str) -> str:
    """The option the person typed (case does not matter); raises ValueError, listing the options, if it is none."""
    value = value.strip()
    if not value:
        return ""
    for option in field.options:
        if option.casefold() == value.casefold():
            return option
    raise ValueError(f"'{value}' is not a choice for *{field.label}*. Use one of: {', '.join(field.options)}")


def main() -> int:
    form = FORMS["update-project"]
    print(request_body(form, {"card": "my-project", "name": "My project", "status": "active"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
