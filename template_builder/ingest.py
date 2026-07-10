"""Read contract files into plain text. Supports .txt, .md, .docx, .pdf."""

import os


class IngestError(ValueError):
    pass


def read_document(path: str) -> str:
    if not os.path.exists(path):
        raise IngestError(f"{path}: file not found")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError:
            raise IngestError(f"{path}: not valid UTF-8 text") from None
    elif ext == ".docx":
        text = _read_docx(path)
    elif ext == ".pdf":
        text = _read_pdf(path)
    else:
        raise IngestError(f"{path}: unsupported file type {ext!r} (use .txt, .md, .docx or .pdf)")
    if not text.strip():
        raise IngestError(f"{path}: no text could be extracted")
    return text


def _read_docx(path: str) -> str:
    try:
        from docx import Document
    except ImportError:
        raise IngestError("python-docx is not installed; run: pip install python-docx") from None
    try:
        doc = Document(path)
        # Paragraph text only. Tables and headers/footers are skipped — noted
        # in the README as a limitation of the simple ingester.
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except IngestError:
        raise
    except Exception as e:
        raise IngestError(f"{path}: could not be read as .docx ({e})") from None


def _read_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise IngestError("pypdf is not installed; run: pip install pypdf") from None
    try:
        reader = PdfReader(path)
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:
        raise IngestError(f"{path}: could not be read as .pdf ({e})") from None
