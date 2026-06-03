"""Validated multimodal attachment ingestion for the unified Study Coach."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import base64
from io import BytesIO
import re
from typing import Any, Dict, Iterable, List

from .settings import coach_settings


_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
_ALLOWED_DOCUMENT_TYPES = {"application/pdf", "text/plain"}
_ALLOWED_TYPES = _ALLOWED_IMAGE_TYPES | _ALLOWED_DOCUMENT_TYPES
_DATA_URL_RE = re.compile(r"^data:(?P<mime>[-\w.+/]+)(?:;charset=[^;,]+)?;base64,", re.IGNORECASE)


@dataclass
class AttachmentBundle:
    context: str = ""
    vision_summary: str = ""
    citations: List[Dict[str, Any]] = field(default_factory=list)
    safe_attachments: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    image_count: int = 0
    document_count: int = 0

    @property
    def has_material(self) -> bool:
        return bool(self.context.strip() or self.vision_summary.strip())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _safe_name(value: Any, index: int) -> str:
    clean = re.sub(r"[^a-zA-Z0-9._ -]+", "", str(value or "").strip())
    return clean[:100] or f"attachment-{index + 1}"


def _decode_data_url(value: Any) -> tuple[bytes, str]:
    data_url = str(value or "")
    if "," not in data_url:
        return b"", ""
    header, payload = data_url.split(",", 1)
    match = _DATA_URL_RE.match(f"{header},")
    if not match:
        return b"", ""
    try:
        return base64.b64decode(payload, validate=True), match.group("mime").lower()
    except Exception:
        return b"", match.group("mime").lower()


def _looks_like_text(raw: bytes) -> bool:
    if b"\x00" in raw[:512]:
        return False
    try:
        raw[:4096].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _matches_file_signature(raw: bytes, mime_type: str) -> bool:
    if mime_type == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/webp":
        return len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    if mime_type == "application/pdf":
        return raw.startswith(b"%PDF-")
    if mime_type == "text/plain":
        return _looks_like_text(raw)
    return False


def _extract_pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(BytesIO(raw))
        pages = [
            str(page.extract_text() or "").strip()
            for page in reader.pages[: coach_settings.max_pdf_pages]
        ]
    except Exception:
        return ""
    return "\n\n".join(page for page in pages if page)[: coach_settings.max_attachment_chars]


def _vision_summary(question: str, images: List[Dict[str, Any]], llm_router: Any) -> str:
    if not images or llm_router is None:
        return ""
    content: List[Dict[str, Any]] = [{
        "type": "text",
        "text": (
            "Read the attached school-study image or screenshot carefully. Extract the visible "
            "question, formulas, labels, and any handwritten working. Describe only what is visible. "
            f"The student asked: {question}"
        ),
    }]
    for image in images[: coach_settings.max_image_attachments]:
        content.append({"type": "image_url", "image_url": {"url": image["data_url"]}})
    try:
        return llm_router.complete(
            role="vision",
            messages=[{"role": "user", "content": content}],
            complexity="fast",
            temperature=0.05,
            max_tokens=520,
        )
    except Exception:
        return ""


def prepare_attachments(
    attachments: Iterable[Dict[str, Any]] | None,
    question: str,
    llm_router: Any = None,
) -> AttachmentBundle:
    """Return bounded text/vision context without persisting raw attachment bytes."""
    bundle = AttachmentBundle()
    images_for_vision: List[Dict[str, Any]] = []
    documents: List[str] = []

    for index, item in enumerate(list(attachments or [])[: coach_settings.max_attachments]):
        if not isinstance(item, dict):
            continue
        mime_type = str(item.get("mime_type") or item.get("type") or "").lower().strip()
        name = _safe_name(item.get("name"), index)
        data_url = str(item.get("data_url") or "")
        raw, data_url_mime = _decode_data_url(data_url)
        if not raw:
            bundle.warnings.append(f"{name}: file data could not be read.")
            continue
        if mime_type not in _ALLOWED_TYPES:
            bundle.warnings.append(f"{name}: unsupported file type.")
            continue
        if data_url_mime and data_url_mime != mime_type:
            bundle.warnings.append(f"{name}: file type does not match the uploaded data.")
            continue
        if not _matches_file_signature(raw, mime_type):
            bundle.warnings.append(f"{name}: file signature did not match the declared type.")
            continue

        max_bytes = (
            coach_settings.max_image_bytes
            if mime_type in _ALLOWED_IMAGE_TYPES
            else coach_settings.max_document_bytes
        )
        if len(raw) > max_bytes:
            bundle.warnings.append(f"{name}: file is too large.")
            continue

        safe_item = {"name": name, "mime_type": mime_type, "size_bytes": len(raw)}
        if mime_type in _ALLOWED_IMAGE_TYPES:
            bundle.image_count += 1
            bundle.safe_attachments.append(safe_item)
            images_for_vision.append({"name": name, "data_url": data_url})
            bundle.citations.append({
                "id": f"upload-image-{index + 1}",
                "label": name,
                "source": "Uploaded image",
                "section_id": "",
                "excerpt": "Image or screenshot supplied by the student.",
            })
            continue

        if mime_type == "application/pdf":
            text = _extract_pdf_text(raw)
        elif mime_type == "text/plain":
            text = raw.decode("utf-8", errors="ignore")[: coach_settings.max_attachment_chars]
        bundle.document_count += 1
        bundle.safe_attachments.append(safe_item)
        if text.strip():
            documents.append(f"UPLOADED MATERIAL: {name}\n{text.strip()}")
        else:
            bundle.warnings.append(f"{name}: no readable text was extracted.")
        bundle.citations.append({
            "id": f"upload-document-{index + 1}",
            "label": name,
            "source": "Uploaded material",
            "section_id": "",
            "excerpt": text[:220].strip() or "Uploaded document supplied by the student.",
        })

    bundle.context = "\n\n".join(documents)[: coach_settings.max_attachment_chars]
    bundle.vision_summary = _vision_summary(question, images_for_vision, llm_router)
    return bundle
