from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from mai.extract import MIN_CHARS, ExtractError, extract
from mai.fetch import FetchedPage

FIXTURES = Path(__file__).parent / "fixtures"


def fetched(body: bytes, content_type: str = "text/html", charset: str | None = "utf-8"):
    return FetchedPage(
        url="https://example.com/start",
        final_url="https://example.com/final",
        status=200,
        content_type=content_type,
        charset=charset,
        body=body,
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def make_pdf(lines: list[str], *, title: str = "", author: str = "", pages: int = 1) -> bytes:
    """Build a minimal valid PDF with the given text lines on each page."""
    stream = "BT /F1 11 Tf 50 750 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(pages))
    font_id = 3 + 2 * pages
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>",
    ]
    for i in range(pages):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(f"<< /Title ({title}) /Author ({author}) /CreationDate (D:20240314120000Z) >>")

    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {len(objects)} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return out


PDF_LINES = [
    "Retrieval methods for long documents",
    "This paper studies how passage retrie-",
    "val quality changes with document length.",
    "We evaluate four systems on 1,200 queries and find that",
    "hybrid search is the most robust across every length bucket.",
    "Dense retrieval alone degrades on documents over ten pages.",
]


def test_html_keeps_the_article_and_drops_boilerplate():
    document = extract(fetched((FIXTURES / "article.html").read_bytes()))

    assert document.url == "https://example.com/final"  # the URL to cite
    assert document.title.startswith("Tidal power comes of age at Port Hask")
    assert document.author == "Rhea Lindqvist"
    assert document.site == "The Coastal Ledger"
    assert document.published == date(2024, 5, 14)
    assert document.content_type == "text/html"

    text = document.text
    assert "48 seabed turbines" in text and "310 million crowns" in text
    assert "## What it cost" in text  # headings survive as Markdown
    assert "171" in text and "183" in text  # table data survives
    for boilerplate in ("Subscribe now", "Advertisement", "Cookie settings", "tracker", "[1]"):
        assert boilerplate not in text
    assert "<" not in text
    assert 100 < document.word_count < 200


def test_html_without_an_article_asks_for_a_browser():
    shell = b"<html><head><title>App</title></head><body><div id='root'></div></body></html>"
    with pytest.raises(ExtractError) as info:
        extract(fetched(shell))
    assert info.value.needs_browser
    assert info.value.url == "https://example.com/final"


def test_html_encoding_is_detected_from_the_document():
    paragraph = "Le café de la gare a rouvert après des travaux très longs. " * 6
    head = "<html><head><meta charset='iso-8859-1'></head>"
    html = f"{head}<body><article><p>{paragraph}</p></article></body></html>"
    document = extract(fetched(html.encode("iso-8859-1"), charset=None))
    assert "café" in document.text and "très" in document.text


def test_pdf_text_is_unwrapped_with_metadata():
    document = extract(
        fetched(make_pdf(PDF_LINES, title="Long Documents", author="A. Writer"), "application/pdf")
    )

    assert document.title == "Long Documents"
    assert document.author == "A. Writer"
    assert document.published == date(2024, 3, 14)
    assert "passage retrieval quality changes" in document.text  # hyphenated break rejoined
    assert "1,200 queries and find that hybrid search" in document.text  # lines joined
    assert "\n" not in document.text  # one page, one block


def test_pdf_falls_back_to_first_line_for_title_and_separates_pages():
    document = extract(fetched(make_pdf(PDF_LINES, pages=2), "application/pdf"))
    assert document.title.startswith("Retrieval methods for long documents")
    assert document.author is None
    assert document.text.count("\n\n") == 1  # a blank line between the two pages


def test_unreadable_and_empty_pdfs_are_errors():
    with pytest.raises(ExtractError, match="Not a readable PDF"):
        extract(fetched(b"this is not a pdf", "application/pdf"))
    with pytest.raises(ExtractError, match="no extractable text") as info:
        extract(fetched(make_pdf(["Hi"]), "application/pdf"))
    assert not info.value.needs_browser


def test_plain_text_and_markdown_pass_through():
    body = "# Release notes\n\n\n\n" + "Fixed a bug in the parser. " * 12
    document = extract(fetched(body.encode(), "text/markdown"))
    assert document.title == "Release notes"
    assert "\n\n\n" not in document.text
    assert document.text.startswith("# Release notes\n\nFixed a bug")

    with pytest.raises(ExtractError, match="nearly empty"):
        extract(fetched(b"too short", "text/plain"))
    assert MIN_CHARS == 200
