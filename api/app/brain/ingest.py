"""Read uploaded files into text.

TXT, Markdown, and PDF are supported. DOCX is rejected with a clear message so
a later extractor can be added without changing the upload route. Files are
read locally. A model sees text only if extraction is configured to call one.
"""

from __future__ import annotations

from pathlib import Path

MAX_BYTES = 2_000_000
MAX_PDF_PAGES = 30


class UnsupportedFormat(ValueError):
    pass


class PayloadTooLarge(ValueError):
    pass


def detect_format(filename: str, content_type: str | None) -> str:
    extension = Path(filename or "").suffix.lower()
    if extension in {".txt", ".text"}:
        return "txt"
    if extension in {".md", ".markdown"}:
        return "markdown"
    if extension == ".pdf":
        return "pdf"
    if extension in {".docx", ".doc"}:
        raise UnsupportedFormat("DOCX is not supported yet. The ingestion layer can add it later.")
    if extension:
        raise UnsupportedFormat("Upload a .txt, .md, or .pdf file.")
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime == "text/plain":
        return "txt"
    if mime in {"text/markdown", "text/x-markdown"}:
        return "markdown"
    if mime == "application/pdf":
        return "pdf"
    raise UnsupportedFormat("Upload a .txt, .md, or .pdf file.")


def _read_text(data: bytes) -> str:
    if b"\x00" in data[:1024]:
        raise ValueError("That file does not look like text.")
    text = data.decode("utf-8", errors="replace")
    if text.count("\ufffd") > max(8, len(text) // 5):
        raise ValueError("That file could not be read as UTF-8 text.")
    return text


def _read_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF support needs the pypdf package on the server.") from exc
    from io import BytesIO

    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise ValueError("That PDF could not be opened.") from exc
    if getattr(reader, "is_encrypted", False):
        raise ValueError("This PDF is encrypted. Export an unlocked PDF and try again.")
    pages = reader.pages[:MAX_PDF_PAGES]
    parts: list[str] = []
    for page in pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:
            raise ValueError("Text could not be extracted from that PDF.") from exc
    text = "\n".join(parts)
    if len(reader.pages) > MAX_PDF_PAGES:
        text += f"\n\n[Stopped after {MAX_PDF_PAGES} pages.]"
    return text


def read_upload(filename: str, content_type: str | None, data: bytes) -> tuple[str, str]:
    if len(data) > MAX_BYTES:
        raise PayloadTooLarge("Files must be 2 MB or smaller.")
    if not data:
        raise ValueError("That file is empty.")
    kind = detect_format(filename, content_type)
    if kind == "pdf":
        return _read_pdf(data), kind
    return _read_text(data), kind
