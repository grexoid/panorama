#!/usr/bin/env python3
"""Self-test for the Word importer (scripts/import_use_case.py).

Builds a tiny Word document in a temporary folder - so no sample file has to be stored in the repository -
runs the importer on it and checks what comes out: title, ticked area, template hints removed, bullet points,
the Environmental/Digital split, a figure with its caption, and that the result is a valid card for the build.

    python scripts/check_importer.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import import_use_case as imp  # noqa: E402

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"')


def para(text: str, bullet: bool = False) -> str:
    props = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>' if bullet else ""
    return f'<w:p>{props}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


FIGURE = '<w:p><w:r><w:drawing><a:blip r:embed="rId5"/></w:drawing></w:r></w:p>'

BODY = "".join([
    para("AI use case: Test idea"),
    para("1. Main area"),
    para("Select the area to which the use case primarily belongs:"),                 # template hint
    para("☐ Strategy, Governance and Organisation"),
    para("■ People, Skills and Ways of Working"),                                     # the ticked one
    para("☐ Data, Technologies and Infrastructure"),
    para("☐ AI Applications and Value Creation"),
    para("2. Challenge or opportunity"),
    para("Describe the problem, need or opportunity that the use case addresses."),   # template hint
    para("Experts spend a lot of time."),
    para("3. Proposed use of AI"),
    para("AI would help in two ways:"),
    para("first point", bullet=True),
    para("second point", bullet=True),
    para("4. Scope"),
    para("Maximum: 1 short paragraph or 4 bullets."),                                 # hint only, no real content
    FIGURE,
    para("Pic.1: A diagram"),
    para("6. Contribution to the environment and digital agenda"),
    para("Environmental contribution"),
    para("Greener."),
    para("Digital contribution: Better."),
    para("7. Key conditions and considerations"),
    para("Human review stays.", bullet=True),
    para("Example of the intended level of detail"),                                  # everything below is ignored
    para("1. Main area"),
    para("This must not appear anywhere."),
])


def make_docx(path: Path) -> None:
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId5" Type="image" Target="media/image1.png"/></Relationships>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document {NS}><w:body>{BODY}</w:body></w:document>")
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 32)


def expect(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        docx = tmp / "Test idea.docx"
        make_docx(docx)
        cards = tmp / "cards" / "use-cases"
        target, _ = imp.import_docx(docx, "test-idea", cards)
        text = target.read_text(encoding="utf-8")
        card = yaml.safe_load(text)

        expect(card.get("name") == "Test idea", f"title: {card.get('name')!r}", failures)
        expect(card.get("main_area") == "People, Skills and Ways of Working", f"ticked area: {card.get('main_area')!r}", failures)
        expect(card.get("challenge") == "Experts spend a lot of time.", f"template hint not removed: {card.get('challenge')!r}", failures)
        expect("- first point" in card.get("proposed_ai", "") and "- second point" in card.get("proposed_ai", ""), "bullet points lost", failures)
        expect("scope" not in card, "a section with only a template hint should be left out", failures)
        expect(card.get("environmental_contribution") == "Greener.", f"environmental part: {card.get('environmental_contribution')!r}", failures)
        expect(card.get("digital_contribution") == "Better.", f"digital part (inline label): {card.get('digital_contribution')!r}", failures)
        expect(card.get("conditions") == "- Human review stays.", f"conditions: {card.get('conditions')!r}", failures)
        expect("must not appear" not in text, "text after the template's example section was not ignored", failures)
        images = card.get("images") or []
        expect(len(images) == 1 and images[0].get("caption") == "A diagram", f"figure/caption: {images!r}", failures)
        expect(bool(images) and (cards / images[0]["file"]).is_file(), "figure file was not extracted", failures)

        # The draft must be accepted by the real build (it only lacks the optional quip).
        done = subprocess.run(
            [sys.executable, str(HERE / "build.py"), "--strict", "--cards", str(tmp / "cards"), "--out", str(tmp / "out")],
            capture_output=True, text=True)
        expect(done.returncode == 0, f"the build rejects the imported draft:\n{done.stdout}{done.stderr}", failures)

    if failures:
        print("Importer self-test FAILED:\n  - " + "\n  - ".join(failures))
        return 1
    print("Importer self-test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
