<p align="center">
  <img src="resources/images/panorama-logo.jpg" alt="PANORAMA Logo" width="400"/>
</p>

# PANORAMA - an AI Project gallery

A static, template-driven gallery of **projects** and **use cases**, published on GitHub Pages.

- **Two kinds of cards**, one YAML file each, no code changes needed to add one:
  - a **project** is running or finished work and links to its own website, repository, demo and docs;
  - a **use case** is an idea - aspiring work that has not become a project yet. It gets a page of its own on the gallery site, an exposé with the full text, optional figures and the facts at a glance.
- Hover (or tab to) a card and it pops out of the page from its centre: bigger picture, summary, technologies, metadata and links.
- Top bar with search plus **Tags**, **Technologies** and **Area** filters. A switch shows all cards, only projects or only use cases; projects can also be filtered by status. Filters are reflected in the URL, so a filtered view can be shared.
- Use cases are **tagged automatically** from their text.
- Works on phones (tap a card to expand it), in dark and light themes, and without any external requests (no CDN, no web fonts, no tracking).

```
cards/                    <- every card lives below this root
  projects/               <- one .yaml per project (+ images/)
  use-cases/              <- one .yaml per use case (+ images/<card-name>/)
submissions/              <- the source Word documents of submitted use cases (not published)
resources/templates/      <- project-card.yaml and use-case-card.yaml: the commented templates to copy
resources/examples/       <- filled-in and fictional cards for previewing the layout
site/                     <- site.yaml (title, logo), taxonomy.yaml (automatic tags), Jinja2 templates, CSS, JS
scripts/build.py          <- validates the cards and renders dist/
scripts/import_use_case.py<- turns a submitted Word document into a draft use-case card
.github/workflows/        <- validate.yml (on PRs) and deploy.yml (on merge)
```

## Adding a card

Both kinds follow the same four steps; use the template that matches your card:

| I want to add...                                   | Template                                                                        | Folder             |
| -------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------ |
| a **project** (running or finished work)           | [`resources/templates/project-card.yaml`](resources/templates/project-card.yaml)   | `cards/projects/`  |
| a **use case** (an idea that is not a project yet) | [`resources/templates/use-case-card.yaml`](resources/templates/use-case-card.yaml) | `cards/use-cases/` |

1. Copy the template to `cards/<folder>/<short-name>.yaml` and fill it in. The templates explain every field in plain language; [`resources/examples/projects/eagle-eye.yaml`](resources/examples/projects/eagle-eye.yaml) and [`resources/examples/use-cases/quality-control-example.yaml`](resources/examples/use-cases/quality-control-example.yaml) show completed cards.
2. *(Optional)* add images next to the card (see below).
3. Open a pull request. The **Validate cards** check builds the site and annotates any problem directly on your file, in plain words.
4. When the PR is merged into `main`, the **Deploy gallery** workflow rebuilds the site and publishes it to GitHub Pages.

### Images

Add images to the `images` folder of the card type and reference them relatively. Absolute `https://` URLs work too. Keep each file under 1.5 MB. Without images a card shows a coloured placeholder.

- **Project:** `screenshot_url: images/my-project/screenshot.png` (landscape, about 1200 x 600 px) and `thumbnail_url: images/my-project/logo.png` (square, about 128 x 128 px), files in `cards/projects/images/my-project/`.
- **Use case:** a list of figures with optional captions, files in `cards/use-cases/images/<card-name>/`. They appear on the use case's page; the first one is also shown on its gallery card.

```yaml
images:
  - file: images/my-idea/architecture.png
    caption: How the pieces fit together
```

Only publish pictures you are allowed to: no personal data (names, e-mail addresses, screenshots of internal tools showing real people) and nothing copyrighted by others.

### No Git experience? Use the browser

You can do all of this on github.com without installing anything:

1. Open the template that matches your card and copy its contents (the copy button is at the top right of the file view).
2. Go to the `cards/projects` or `cards/use-cases` folder, choose **Add file -> Create new file**, and name it e.g. `forest-watch.yaml`.
3. Paste the template, replace the example text with your own, and follow the notes in the file.
4. Click **Commit changes...**, choose **Create a new branch and start a pull request**, then **Propose changes** and **Create pull request**.
5. Wait a minute for the **Validate cards** check. If it shows a red cross, open the details: it names the line to fix. Edit the file in the same pull request and the check runs again.

To add a picture the same way, open the card type's `images` folder, choose **Add file -> Upload files**, and select **Commit directly to the branch** of your pull request.

### Use cases submitted as Word documents

Use cases are collected with the Word template [`submissions/use-cases_template.docx`](submissions/use-cases_template.docx). Completed documents go to `submissions/use-cases/`. The use-case card has exactly the sections of that template, so a document can be converted almost mechanically:

```bash
python scripts/import_use_case.py "submissions/use-cases/My idea.docx"
```

The script reads the seven numbered sections, detects the ticked main area (whichever kind of check box was used), converts bullet lists, removes left-over template hints and writes `cards/use-cases/my-idea.yaml`. Figures embedded in the document are extracted next to it, with their "Pic. 1: ..." captions.

The result is a **draft**. Before committing, read it through and:

- add a one-line `quip` (and a `summary` if the first sentences of `proposed_ai` do not make a good teaser);
- fill in `affiliation` and `country`;
- **check every extracted figure** for personal data (e.g. screenshots showing real names) and for third-party copyright (e.g. charts from other organisations), and delete the ones that cannot be published.

Tags need no attention; they are added automatically (next section). The original document stays in `submissions/` as the record of what was submitted.

### Automatic tags

When the site is built, each use case is scanned for the keywords listed in [`site/taxonomy.yaml`](site/taxonomy.yaml). A topic that shows up often enough becomes a **tag** (for example "Water and hydrology"), a tool or method becomes a **technology** (for example "MCP"). The file is plain YAML with its own explanation at the top; adjust the vocabulary there, without touching any code. Tags written into a card by hand are kept and shown first; `auto_tag: false` switches the automation off for one card. Projects are not tagged automatically - their authors choose their tags.

### Card fields

**Project** (`cards/projects/`)

| Field                               | Required     | Notes                                                                     |
| ----------------------------------- | ------------ | ------------------------------------------------------------------------- |
| `name`                              | yes          | Card title                                                                |
| `description`                       | yes          | Shown when the card is expanded. Blank lines start a new paragraph.       |
| `quip`                              |              | One-line teaser shown on the collapsed card                               |
| `website`, `repo_url`               | at least one | Shown as **Website** and **Repository** buttons                           |
| `demo_url`, `docs_url`              |              | Optional **Demo** and **Docs** buttons                                    |
| `screenshot_url`, `thumbnail_url`   |              | `https://...` URL or a path relative to `cards/projects/`                 |
| `category`                          |              | Free text, shown in the details and searchable                            |
| `tags`                              |              | List; feeds the **Tags** filter. Matching is case-insensitive.            |
| `stack`                             |              | List; feeds the **Technologies** filter                                   |
| `status`                            |              | `active` (default), `beta`, `maintenance` or `archived`                   |
| `featured`                          |              | `true` shows a star and sorts the card first                              |
| `version`, `license`, `affiliation` |              | Shown in the details                                                      |
| `contact`                           |              | `Name <email>`; the name links to the email address                       |
| `released_on`, `updated_on`         |              | ISO dates (`YYYY-MM-DD`); drive the "Recently updated" / "Newest" sorting |

**Use case** (`cards/use-cases/`) - the text sections correspond to the numbered sections of the Word template. Every text field takes plain text (blank line = new paragraph, lines starting with `- ` = bullet points) or a YAML list of bullet points.

| Field                                                | Required | Word template section                  |
| ---------------------------------------------------- | -------- | -------------------------------------- |
| `name`                                               | yes      | title                                  |
| `main_area`                                          | yes      | 1. Main area (one of the four areas)   |
| `challenge`                                          | yes      | 2. Challenge or opportunity            |
| `proposed_ai`                                        | yes      | 3. Proposed use of AI                  |
| `scope`                                              |          | 4. Scope                               |
| `expected_outcome`                                   |          | 5. Expected outcome                    |
| `environmental_contribution`, `digital_contribution` |          | 6. Contribution (or one `contribution`) |
| `conditions`                                         |          | 7. Key conditions and considerations   |
| `quip`, `summary`                                    |          | one line and two sentences for the card |
| `affiliation`, `country`, `contact`                  |          | who and where                          |
| `images`                                             |          | figures, each with a `file` and an optional `caption` |
| `tags`, `stack`, `auto_tag`                          |          | additions to / switch for the automatic tags |
| `submitted_on`                                       |          | ISO date; used for sorting             |

Files whose name starts with `_` are ignored, which is handy for drafts.

## Running locally

```bash
pip install -r requirements.txt
python scripts/build.py                  # cards/ -> dist/
python -m http.server -d dist 8000       # open http://localhost:8000
```

To try the layout with the example cards:

```bash
python scripts/build.py --cards resources/examples --out preview
python -m http.server -d preview 8000
```

`python scripts/build.py --strict` is what CI runs: warnings (unknown fields, oversized images) fail the build too.

## Site settings

Title, logo emoji (also used as favicon), heading, tagline and search placeholder are in [`site/site.yaml`](site/site.yaml). The look is plain CSS in `site/static/style.css`; the page structure is in `site/templates/` (`index.html.j2` for the gallery, `use-case.html.j2` for a use case's page).

## One-time repository setup

1. Push this repository to GitHub.
2. **Settings -> Pages -> Build and deployment -> Source: GitHub Actions**.
3. Push to `main` (or run **Deploy gallery** manually). The site appears at `https://<owner>.github.io/<repo>/`.

All asset links are relative, so the site works under the `/<repo>/` sub-path as well as on a custom domain.

The build also writes `projects.json` next to `index.html`, a machine-readable copy of all cards (projects and use cases, including the automatic tags).
