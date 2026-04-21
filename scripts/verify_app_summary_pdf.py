from __future__ import annotations

from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = ROOT / "output" / "pdf" / "trinity_app_summary.pdf"
PNG_PATH = ROOT / "tmp" / "pdfs" / "trinity_app_summary_page1.png"


def main() -> None:
    reader = PdfReader(str(PDF_PATH))
    print(f"pages={len(reader.pages)}")

    with pdfplumber.open(str(PDF_PATH)) as pdf:
        text = pdf.pages[0].extract_text() or ""
        print("TEXT_START")
        print(text)
        print("TEXT_END")

    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(PDF_PATH))
    page = pdf[0]
    bitmap = page.render(scale=2.0)
    bitmap.to_pil().save(PNG_PATH)
    print(f"png={PNG_PATH}")


if __name__ == "__main__":
    main()
