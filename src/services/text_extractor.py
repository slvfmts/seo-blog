"""
Text extraction from uploaded documents.

Supports: .md, .txt, .pdf, .docx
"""

import logging

logger = logging.getLogger(__name__)


def extract_text(file_path: str, mime_type: str) -> tuple[str, int]:
    """
    Extract plain text from a file.

    Args:
        file_path: Path to the file on disk.
        mime_type: MIME type of the file.

    Returns:
        (text, word_count) tuple.
    """
    if mime_type in ("text/plain", "text/markdown"):
        return _extract_plain(file_path)
    elif mime_type == "application/pdf":
        return _extract_pdf(file_path)
    elif mime_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        return _extract_docx(file_path)
    else:
        raise ValueError(f"Unsupported mime type: {mime_type}")


def _extract_plain(file_path: str) -> tuple[str, int]:
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()
    return text, len(text.split())


def _extract_pdf(file_path: str) -> tuple[str, int]:
    import pdfplumber

    pages = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
    text = "\n\n".join(pages)
    return text, len(text.split())


def _extract_docx(file_path: str) -> tuple[str, int]:
    from docx import Document

    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n\n".join(paragraphs)
    return text, len(text.split())
