"""Turning uploaded files into numbered passages, and choosing which to send."""

import io
import math
import re
import uuid
from collections import Counter

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .schemas import Chunk, Document

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".log"}


class DocumentError(ValueError):
    """The upload can't be turned into usable text. Carries an HTTP status."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def parse_upload(filename: str, data: bytes, chunk_chars: int) -> Document:
    if not data:
        raise DocumentError(422, "The uploaded file is empty.")

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if data[:5] == b"%PDF-" or ext == ".pdf":
        pages = _pdf_pages(data)
        kind, page_count = "pdf", len(pages)
    elif ext in TEXT_EXTENSIONS or ext == "":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        pages = [text]
        kind, page_count = "text", None
    else:
        raise DocumentError(415, f"Unsupported file type '{ext}'. Upload a PDF or a text file.")

    chunks: list[Chunk] = []
    for page_no, page_text in enumerate(pages, start=1):
        for piece in _split(page_text, chunk_chars):
            chunks.append(
                Chunk(id=f"C{len(chunks) + 1}", page=page_no if kind == "pdf" else None, text=piece)
            )

    if not chunks:
        hint = " It may be a scanned PDF without a text layer." if kind == "pdf" else ""
        raise DocumentError(422, "No readable text found in the document." + hint)

    return Document(
        doc_id=uuid.uuid4().hex[:12],
        filename=filename,
        kind=kind,
        pages=page_count,
        chunks=chunks,
    )


def _pdf_pages(data: bytes) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError(422, "The PDF is password-protected.")
        return [page.extract_text() or "" for page in reader.pages]
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError) as exc:
        raise DocumentError(422, f"Could not read the PDF: {exc}") from exc


def _split(text: str, max_chars: int) -> list[str]:
    """Pack paragraphs into chunks of at most ~max_chars, splitting long ones on sentences."""
    text = text.replace("\r\n", "\n")
    paragraphs = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    pieces: list[str] = []
    for para in filter(None, paragraphs):
        if len(para) <= max_chars:
            pieces.append(para)
        else:
            pieces.extend(_hard_wrap(para, max_chars))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _hard_wrap(para: str, max_chars: int) -> list[str]:
    out, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", para):
        while len(sentence) > max_chars:  # one enormous "sentence", e.g. a table
            out.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if current and len(current) + len(sentence) + 1 > max_chars:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        out.append(current)
    return out


# ---- Retrieval ---------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set(
    "a an and are as at be by can do does for from has have how i in is it its of on or that "
    "the their this to was what when where which who why will with you your".split()
)


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOP]


def select_chunks(doc: Document, question: str, char_budget: int, top_k: int) -> tuple[list[Chunk], str]:
    """Send the whole document when it fits; otherwise the top_k chunks by BM25 score."""
    if doc.char_count <= char_budget:
        return doc.chunks, "full_document"

    docs_tokens = [_tokens(c.text) for c in doc.chunks]
    avg_len = sum(len(t) for t in docs_tokens) / len(docs_tokens) or 1
    df = Counter(term for toks in docs_tokens for term in set(toks))
    n = len(doc.chunks)
    query = set(_tokens(question))

    def bm25(toks: list[str], k1: float = 1.5, b: float = 0.75) -> float:
        tf = Counter(toks)
        score = 0.0
        for term in query:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(toks) / avg_len))
        return score

    ranked = sorted(range(n), key=lambda i: bm25(docs_tokens[i]), reverse=True)[:top_k]
    # Keep document order so the model reads passages in sequence.
    return [doc.chunks[i] for i in sorted(ranked)], "top_k"


# ---- Citation verification ------------------------------------------------------


def _normalize(text: str) -> str:
    text = text.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip(" .\"'")


def quote_in_chunk(quote: str, chunk_text: str) -> bool:
    """True if every '...'-separated fragment of the quote appears in the chunk."""
    haystack = _normalize(chunk_text)
    fragments = [_normalize(f) for f in re.split(r"\.\.\.|…", quote)]
    fragments = [f for f in fragments if f]
    return bool(fragments) and all(f in haystack for f in fragments)
