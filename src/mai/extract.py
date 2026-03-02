"""Turn a fetched page into clean Markdown text with metadata.

HTML goes through trafilatura, which strips navigation, adverts and other
boilerplate and keeps the main article. PDFs go through pdfium. Plain text
and Markdown pass through as they are.

Extraction is synchronous and CPU-bound, so async callers should run it in a
thread (see ``mai.read``). pdfium must not be used from two threads at once;
a lock here makes that safe.
"""

from __future__ import annotations

import re
import threading
import warnings
from dataclasses import dataclass
from datetime import date

from mai.fetch import FetchedPage
from mai.search.base import parse_date

MIN_CHARS = 200
"""Below this, a page is treated as having no readable content. The usual cause
is a page that builds itself with JavaScript, which the browser fallback handles."""

MAX_PDF_PAGES = 200

_FOOTNOTE_MARK = re.compile(r"<sup>.*?</sup>", re.DOTALL)
_HTML_TAG = re.compile(r"</?(?:sub|sup|span|div|br)\b[^>]*>")
_BLANK_LINES = re.compile(r"\n{3,}")
_HYPHEN_BREAK = re.compile(r"(?<=[a-z])-\n(?=[a-z])")
_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})(\d{2})")
_PDFIUM_LOCK = threading.Lock()


class ExtractError(Exception):
    """No usable text could be extracted from a page."""

    def __init__(self, url: str, message: str, *, needs_browser: bool = False) -> None:
        super().__init__(f"{message}: {url}")
        self.url = url
        self.message = message
        self.needs_browser = needs_browser
        """True when rendering the page in a browser might succeed."""


@dataclass(frozen=True)
class Document:
    url: str
    """The page's final URL, used for citations."""
    title: str
    text: str
    """Main content as Markdown."""
    content_type: str
    author: str | None = None
    published: date | None = None
    site: str | None = None

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def extract(page: FetchedPage) -> Document:
    """Extract the main content of ``page``. Raises ExtractError if there is none."""
    if page.content_type == "application/pdf":
        return _extract_pdf(page)
    if page.content_type in ("text/plain", "text/markdown"):
        return _extract_text(page)
    return _extract_html(page)


def _tidy(text: str) -> str:
    return _BLANK_LINES.sub("\n\n", text).strip()


def _extract_html(page: FetchedPage) -> Document:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # optional-dependency notices from trafilatura's imports
        import trafilatura

    # Bytes are parsed here so the encoding is detected from the document itself.
    tree = trafilatura.load_html(page.body)
    if tree is None:
        raise ExtractError(page.final_url, "Not parseable as HTML")
    # Metadata first: extract() prunes the tree it is given.
    metadata = trafilatura.extract_metadata(tree, default_url=page.final_url)
    text = trafilatura.extract(
        tree,
        url=page.final_url,
        output_format="markdown",
        include_links=False,
        include_tables=True,
        include_comments=False,
        with_metadata=False,
    )
    text = _tidy(_HTML_TAG.sub("", _FOOTNOTE_MARK.sub("", text or "")))
    if len(text) < MIN_CHARS:
        raise ExtractError(page.final_url, "No readable article text found", needs_browser=True)

    return Document(
        url=page.final_url,
        title=(metadata.title if metadata else None) or _first_line(text),
        text=text,
        content_type=page.content_type,
        author=(metadata.author if metadata else None) or None,
        published=parse_date(metadata.date) if metadata else None,
        site=(metadata.sitename if metadata else None) or None,
    )


def _extract_pdf(page: FetchedPage) -> Document:
    with _PDFIUM_LOCK:
        return _extract_pdf_locked(page)


def _extract_pdf_locked(page: FetchedPage) -> Document:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(page.body)
    except pdfium.PdfiumError as error:
        raise ExtractError(page.final_url, "Not a readable PDF") from error
    try:
        pages = []
        for index in range(min(len(pdf), MAX_PDF_PAGES)):
            textpage = pdf[index].get_textpage()
            pages.append(_unwrap_pdf_text(textpage.get_text_range()))
        metadata = pdf.get_metadata_dict()
    finally:
        pdf.close()

    text = _tidy("\n\n".join(part for part in pages if part))
    if len(text) < MIN_CHARS:
        # Typically a scanned document: images of pages with no text layer.
        raise ExtractError(page.final_url, "PDF has no extractable text")
    created = _PDF_DATE.match(metadata.get("CreationDate") or "")
    return Document(
        url=page.final_url,
        title=(metadata.get("Title") or "").strip() or _first_line(text),
        text=text,
        content_type=page.content_type,
        author=(metadata.get("Author") or "").strip() or None,
        published=parse_date("-".join(created.groups())) if created else None,
    )


def _unwrap_pdf_text(raw: str) -> str:
    """Rejoin the hard-wrapped lines of one PDF page into flowing text.

    PDFs store lines, not paragraphs, so paragraph breaks inside a page cannot
    be recovered reliably; each page becomes one block of text.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    # pdfium marks a word hyphenated across a line break with U+FFFE; other PDFs
    # leave a literal hyphen. Either way the word is rejoined.
    text = text.replace("\ufffe\n", "").replace("\ufffe", "")
    text = _HYPHEN_BREAK.sub("", text)
    return " ".join(line.strip() for line in text.split("\n") if line.strip())


def _extract_text(page: FetchedPage) -> Document:
    text = _tidy(page.body.decode(page.charset or "utf-8", errors="replace"))
    if len(text) < MIN_CHARS:
        raise ExtractError(page.final_url, "Document is nearly empty")
    return Document(
        url=page.final_url, title=_first_line(text), text=text, content_type=page.content_type
    )


def _first_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned[:120]
    return "(untitled)"
