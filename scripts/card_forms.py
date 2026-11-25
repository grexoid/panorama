#!/usr/bin/env python3
"""The GitHub issue forms behind the "Update" and "Promote" buttons on the cards - defined once.

A button on a card opens one of these forms on GitHub with the card's current content already filled in (GitHub
can fill a form from the address). When someone submits it, the "Card requests" workflow reads the submitted values
and prepares the change as a branch and a pull request link. Defining the forms here keeps three things in step:

  - the form files in .github/ISSUE_TEMPLATE/ (generated from this file: `python scripts/card_forms.py --write`),
  - the button addresses built by scripts/build.py (a field's `id` is the address parameter that fills it),
  - the parser that reads a submitted form (scripts/card_request.py).

    python scripts/card_forms.py --write     # (re)generate .github/ISSUE_TEMPLATE/*.yml
    python scripts/card_forms.py --check     # fail if those files are out of date (run by CI)
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / ".github" / "ISSUE_TEMPLATE"

KEEP = "(keep)"                      # first option of a dropdown that means "leave as it is"
NO_RESPONSE = "_No response_"        # what GitHub writes into the issue for an empty field
REMOVE = "-"                         # typing a dash in a field removes that field from the card
STATUSES = ("beta", "active", "maintenance", "archived")
MAIN_AREAS = (                       # the four areas of the use-case template (a test keeps this equal to build.AREAS)
    "Strategy, Governance and Organisation",
    "People, Skills and Ways of Working",
    "Data, Technologies and Infrastructure",
    "AI Applications and Value Creation",
)
MAX_URL = 6500                       # GitHub refuses addresses much beyond 8000 characters


@dataclass(frozen=True)
class Field:
    id: str                          # the card's field name - and the address parameter that pre-fills the form field
    label: str                       # shown on the form; also how the submitted issue names the value
    kind: str = "input"              # input | textarea | dropdown
    hint: str = ""
    options: tuple[str, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class Form:
    key: str                         # file name (without .yml) of the issue form
    title_prefix: str                # every request starts with this; the workflow recognises the kind of request by it
    kind: str                        # "project" or "use-case": the kind of card the request is about
    action: str                      # "update" or "promote"
    name: str
    description: str
    intro: str
    fields: tuple[Field, ...]

    def field(self, field_id: str) -> Field:
        return next(f for f in self.fields if f.id == field_id)


CARD = Field("card", "Card (file name)", hint="The card's file name without .yaml. It is filled in for you - please do not change it.", required=True)
KEEP_HINT = (
    "Leave a box as it is to keep the card as it is. Change the text to update it. "
    "To remove a value from the card altogether, type a single dash (-). "
    "Empty boxes are ignored."
)
LONG_HINT = " (A box that is empty although the card has text there did not fit into the pre-filled address: the card's current text stays as it is.)"

UPDATE_PROJECT = Form(
    key="update-project", title_prefix="Update project: ", kind="project", action="update",
    name="Update a project card",
    description="Change the content of an existing project card. Opened from the card's Update button, already filled in.",
    intro=f"### Update a project card\n\n{KEEP_HINT}{LONG_HINT}\n\nWhen you submit, a workflow prepares the change on a new branch and "
          "posts a link here to open the pull request. Only repository collaborators can run it.",
    fields=(
        CARD,
        Field("name", "Name"),
        Field("quip", "Teaser", hint="One short line shown on the card."),
        Field("description", "Description", "textarea", "Blank lines start a new paragraph."),
        Field("website", "Website", hint="https://..."),
        Field("repo_url", "Repository", hint="https://..."),
        Field("demo_url", "Demo", hint="https://..."),
        Field("docs_url", "Documentation", hint="https://..."),
        Field("status", "Status", "dropdown", options=(KEEP, *STATUSES)),
        Field("version", "Version"),
        Field("license", "License"),
        Field("category", "Category"),
        Field("country", "Country", hint="Several: separate with commas."),
        Field("affiliation", "Organisation"),
        Field("contact", "Contact", hint="Name <email>"),
        Field("tags", "Tags", hint="Separated by commas."),
        Field("stack", "Technologies", hint="Separated by commas."),
    ),
)

UPDATE_USE_CASE = Form(
    key="update-use-case", title_prefix="Update use case: ", kind="use-case", action="update",
    name="Update a use case",
    description="Change the content of an existing use case. Opened from the card's Update button, already filled in.",
    intro=f"### Update a use case\n\n{KEEP_HINT}{LONG_HINT}\n\nWhen you submit, a workflow prepares the change on a new branch and "
          "posts a link here to open the pull request. Only repository collaborators can run it.",
    fields=(
        CARD,
        Field("name", "Name"),
        Field("quip", "Teaser", hint="One short line shown on the card."),
        Field("summary", "Summary", "textarea", "Two sentences for the card and the top of the page."),
        Field("main_area", "Main area", "dropdown", options=(KEEP, *MAIN_AREAS)),
        Field("country", "Country", hint="Several: separate with commas."),
        Field("affiliation", "Organisation"),
        Field("contact", "Contact", hint="Name <email>"),
        Field("tags", "Tags", hint="Your own tags, separated by commas. The automatic tags are added on top."),
        Field("stack", "Technologies", hint="Your own, separated by commas. The automatic ones are added on top."),
        Field("challenge", "Challenge or opportunity", "textarea"),
        Field("proposed_ai", "Proposed use of AI", "textarea"),
        Field("scope", "Scope", "textarea", "Lines starting with '- ' become bullet points."),
        Field("expected_outcome", "Expected outcome", "textarea"),
        Field("environmental_contribution", "Contribution to the environment", "textarea"),
        Field("digital_contribution", "Contribution to the digital agenda", "textarea"),
        Field("conditions", "Key conditions and considerations", "textarea", "Lines starting with '- ' become bullet points."),
    ),
)

PROMOTE_USE_CASE = Form(
    key="promote-use-case", title_prefix="Promote use case: ", kind="use-case", action="promote",
    name="Promote a use case to a project",
    description="Turn a use case into a project: the idea has become real work. Opened from the card's Promote button, already filled in.",
    intro="### Promote a use case to a project\n\n"
          "The use case moves to the projects and its page gives way to the project card. Check the pre-filled text, "
          "add the project's **website and/or repository**, choose its status, and submit. "
          "The long sections of the use case (challenge, scope, conditions ...) have no place on a project card; they stay in the "
          "repository history. A workflow prepares the change on a new branch and posts a link here to open the pull request. "
          "Only repository collaborators can run it.",
    fields=(
        CARD,
        Field("name", "Name"),
        Field("quip", "Teaser", hint="One short line shown on the card."),
        Field("description", "Description", "textarea", "Pre-filled from the use case; adjust it to describe the project. Blank lines start a new paragraph."),
        Field("website", "Website", hint="https://... - give this and/or the repository."),
        Field("repo_url", "Repository", hint="https://..."),
        Field("demo_url", "Demo", hint="Optional: https://..."),
        Field("docs_url", "Documentation", hint="Optional: https://..."),
        Field("status", "Status", "dropdown", options=STATUSES),
        Field("country", "Country", hint="Several: separate with commas."),
        Field("affiliation", "Organisation"),
        Field("contact", "Contact", hint="Name <email>"),
        Field("tags", "Tags", hint="Projects are not tagged automatically: these are the use case's tags as they are now. Separated by commas."),
        Field("stack", "Technologies", hint="Separated by commas."),
    ),
)

FORMS: dict[str, Form] = {f.key: f for f in (UPDATE_PROJECT, UPDATE_USE_CASE, PROMOTE_USE_CASE)}


# --------------------------------------------------------------------------- #
# The issue form files
# --------------------------------------------------------------------------- #
def form_yaml(form: Form) -> str:
    """The text of .github/ISSUE_TEMPLATE/<key>.yml for a form."""
    body: list[dict] = [{"type": "markdown", "attributes": {"value": form.intro}}]
    for f in form.fields:
        attributes: dict = {"label": f.label}
        if f.hint:
            attributes["description"] = f.hint
        if f.kind == "dropdown":
            attributes["options"] = list(f.options)
            attributes["default"] = 0
        item = {"type": f.kind, "id": f.id, "attributes": attributes}
        if f.required:
            item["validations"] = {"required": True}
        body.append(item)
    doc = {"name": form.name, "description": form.description, "title": form.title_prefix, "body": body}
    header = "# Generated by scripts/card_forms.py - do not edit by hand (run: python scripts/card_forms.py --write).\n"
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110)


def write_forms() -> list[Path]:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for form in FORMS.values():
        path = TEMPLATE_DIR / f"{form.key}.yml"
        path.write_text(form_yaml(form), encoding="utf-8", newline="\n")
        written.append(path)
    return written


def stale_forms() -> list[str]:
    """Names of generated form files that differ from what the definitions above produce."""
    return [f"{form.key}.yml" for form in FORMS.values()
            if not (TEMPLATE_DIR / f"{form.key}.yml").is_file()
            or (TEMPLATE_DIR / f"{form.key}.yml").read_text(encoding="utf-8").replace("\r\n", "\n") != form_yaml(form)]


# --------------------------------------------------------------------------- #
# Buttons: addresses that open a form pre-filled
# --------------------------------------------------------------------------- #
def prefill_url(repo_url: str, form: Form, slug: str, values: dict[str, str]) -> str:
    """Address of the form on GitHub, with `values` (field id -> text) filled in.

    GitHub fills a form field from the address parameter with the field's id. The address has a length limit, so
    long texts are added shortest first and the ones that no longer fit are left out (the form tells the person
    that an empty box means "unchanged")."""
    def param(key: str, value: str) -> str:
        return f"&{key}={quote(value, safe='')}"

    url = f"{repo_url}/issues/new?template={form.key}.yml&title={quote(form.title_prefix + slug, safe='')}"
    wanted = {"card": slug, **{k: v for k, v in values.items() if v}}
    for key, value in sorted(wanted.items(), key=lambda kv: len(kv[1])):
        if key not in {f.id for f in form.fields}:
            continue
        if len(url) + len(param(key, value)) <= MAX_URL or key == "card":
            url += param(key, value)
    return url


# --------------------------------------------------------------------------- #
# Submitted forms
# --------------------------------------------------------------------------- #
def form_for_title(title: str) -> Form | None:
    return next((f for f in FORMS.values() if title.startswith(f.title_prefix)), None)


def render_issue_body(form: Form, values: dict[str, str]) -> str:
    """The issue text GitHub produces from a submitted form (used by tests and documentation)."""
    parts = []
    for f in form.fields:
        value = (values.get(f.id) or "").strip()
        if f.kind == "dropdown" and not value:
            value = f.options[0]
        parts.append(f"### {f.label}\n\n{value or NO_RESPONSE}")
    return "\n\n".join(parts) + "\n"


def parse_issue_body(form: Form, body: str) -> dict[str, str]:
    """Field id -> submitted value, from the text of an issue created from the form. Empty fields give ''."""
    by_label = {f.label: f.id for f in form.fields}
    values = {f.id: "" for f in form.fields}
    for chunk in re.split(r"(?m)^### ", body.replace("\r\n", "\n"))[1:]:
        label, _, rest = chunk.partition("\n")
        field_id = by_label.get(label.strip())
        if field_id is not None:
            value = rest.strip()
            values[field_id] = "" if value == NO_RESPONSE else value
    return values


def main() -> int:
    if "--write" in sys.argv:
        for path in write_forms():
            print(f"wrote {path.relative_to(ROOT).as_posix()}")
        return 0
    if "--check" in sys.argv:
        stale = stale_forms()
        if stale:
            print("These issue forms are out of date: " + ", ".join(stale) + "\nRun: python scripts/card_forms.py --write")
            return 1
        print("Issue forms are up to date.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
