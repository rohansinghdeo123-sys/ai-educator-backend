"""Pluggable paper parsers.

A parser turns raw uploaded bytes into plain text plus an extraction-confidence
signal and warnings. The factory selects a parser from the filename/content-type.

OCR is intentionally a stub: the interface and routing are in place so an image
OCR backend can be added later (``ImageOCRPaperParser.parse``) without touching
routes or services.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("ai_educator.exam.parsers")

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - environments without pypdf
    PdfReader = None  # type: ignore[assignment]


OCR_NOT_CONFIGURED_MESSAGE = (
    "Image OCR is not configured yet. Please upload a text-based PDF or enable the "
    "OCR service to analyze scanned/image papers."
)
PDF_PARSER_UNAVAILABLE_MESSAGE = (
    "PDF parsing is unavailable on the server (pypdf not installed)."
)
PDF_NO_TEXT_MESSAGE = (
    "No selectable text was found in this PDF. It may be a scanned image; OCR is "
    "required to read it."
)


@dataclass
class ParsedPaper:
    text: str = ""
    page_count: int = 0
    char_count: int = 0
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)
    parser_name: str = ""
    requires_ocr: bool = False

    def add_warning(self, message: str) -> None:
        if message and message not in self.warnings:
            self.warnings.append(message)


class BasePaperParser:
    """Common interface for all paper parsers."""

    name = "base"
    requires_ocr = False

    def parse(self, data: bytes, filename: str = "") -> ParsedPaper:  # pragma: no cover - abstract
        raise NotImplementedError


class TextPaperParser(BasePaperParser):
    name = "text"

    def parse(self, data: bytes, filename: str = "") -> ParsedPaper:
        text = (data or b"").decode("utf-8", errors="ignore")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        char_count = len(text)
        # Plain text is read losslessly; confidence reflects whether there is
        # enough content to analyze, not extraction fidelity.
        confidence = 0.0 if char_count == 0 else min(1.0, char_count / 400)
        result = ParsedPaper(
            text=text,
            page_count=1 if text else 0,
            char_count=char_count,
            confidence=round(confidence, 3),
            parser_name=self.name,
        )
        if not text:
            result.add_warning("The uploaded text file was empty.")
        return result


class PDFPaperParser(BasePaperParser):
    name = "pdf"

    def parse(self, data: bytes, filename: str = "") -> ParsedPaper:
        if PdfReader is None:
            return ParsedPaper(parser_name=self.name, warnings=[PDF_PARSER_UNAVAILABLE_MESSAGE])
        try:
            reader = PdfReader(io.BytesIO(data or b""))
        except Exception as exc:
            logger.warning("PDF parse failed for %s: %s", filename, exc)
            return ParsedPaper(
                parser_name=self.name,
                warnings=[f"Could not read the PDF file: {exc}"[:240]],
            )

        page_texts: List[str] = []
        per_page_quality: List[float] = []
        for page in reader.pages:
            try:
                raw_text = page.extract_text() or ""
            except Exception:
                raw_text = ""
            text = re.sub(r"[ \t]+", " ", raw_text).strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            page_texts.append(text)
            per_page_quality.append(0.0 if not text else min(1.0, len(text) / 900))

        combined = "\n\n".join(part for part in page_texts if part).strip()
        char_count = len(combined)
        confidence = round(sum(per_page_quality) / len(per_page_quality), 3) if per_page_quality else 0.0
        result = ParsedPaper(
            text=combined,
            page_count=len(reader.pages),
            char_count=char_count,
            confidence=confidence,
            parser_name=self.name,
        )
        if char_count == 0:
            result.add_warning(PDF_NO_TEXT_MESSAGE)
            result.requires_ocr = True
        return result


class ImageOCRPaperParser(BasePaperParser):
    """Stub parser for image uploads. Returns a clear 'OCR not configured'
    warning until an OCR backend is wired into :meth:`parse`."""

    name = "image_ocr"
    requires_ocr = True

    def parse(self, data: bytes, filename: str = "") -> ParsedPaper:
        return ParsedPaper(
            text="",
            page_count=1,
            char_count=0,
            confidence=0.0,
            warnings=[OCR_NOT_CONFIGURED_MESSAGE],
            parser_name=self.name,
            requires_ocr=True,
        )


_EXTENSION_PARSERS = {
    "pdf": PDFPaperParser,
    "txt": TextPaperParser,
    "text": TextPaperParser,
    "md": TextPaperParser,
    "png": ImageOCRPaperParser,
    "jpg": ImageOCRPaperParser,
    "jpeg": ImageOCRPaperParser,
    "webp": ImageOCRPaperParser,
}

_CONTENT_TYPE_PARSERS = {
    "application/pdf": PDFPaperParser,
    "text/plain": TextPaperParser,
    "text/markdown": TextPaperParser,
    "image/png": ImageOCRPaperParser,
    "image/jpeg": ImageOCRPaperParser,
    "image/jpg": ImageOCRPaperParser,
    "image/webp": ImageOCRPaperParser,
}


def detect_extension(filename: str = "", content_type: str = "") -> str:
    ext = ""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].strip().lower()
    if ext:
        return ext
    content_type = (content_type or "").split(";")[0].strip().lower()
    mapping = {
        "application/pdf": "pdf",
        "text/plain": "txt",
        "text/markdown": "md",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
    }
    return mapping.get(content_type, "")


class ParserFactory:
    @staticmethod
    def for_upload(filename: str = "", content_type: str = "", data: bytes = b"") -> BasePaperParser:
        ext = detect_extension(filename, content_type)
        parser_cls = _EXTENSION_PARSERS.get(ext)
        if parser_cls is None:
            content_key = (content_type or "").split(";")[0].strip().lower()
            parser_cls = _CONTENT_TYPE_PARSERS.get(content_key)
        # Content sniff: a PDF magic header wins regardless of a wrong extension.
        if (data or b"")[:5].startswith(b"%PDF") and parser_cls is not ImageOCRPaperParser:
            parser_cls = PDFPaperParser
        if parser_cls is None:
            raise ValueError(
                f"Unsupported file type '{ext or content_type or 'unknown'}'. "
                "Upload a PDF, a text file, or an image (OCR)."
            )
        return parser_cls()


def parse_upload(data: bytes, filename: str = "", content_type: str = "") -> ParsedPaper:
    """Convenience: select a parser and parse, never raising on parse errors."""
    parser = ParserFactory.for_upload(filename=filename, content_type=content_type, data=data)
    return parser.parse(data, filename=filename)
