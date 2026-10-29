"""Where parsed documents live between /upload and /ask.

Documents expire `ttl_seconds` after upload. Expired documents are dropped when
they are next looked up, and the Modal store also sweeps old ones on each upload.
"""

import time
from collections import OrderedDict
from typing import Callable, Protocol

from .schemas import Document


class DocumentStore(Protocol):
    def put(self, doc: Document) -> None: ...
    def get(self, doc_id: str) -> Document | None: ...


class InMemoryStore:
    """Process-local LRU. Fine for local dev and a single container."""

    def __init__(self, max_docs: int = 200, ttl_seconds: float = 86_400, now: Callable[[], float] = time.time):
        self._docs: OrderedDict[str, Document] = OrderedDict()
        self._max = max_docs
        self._ttl = ttl_seconds
        self._now = now

    def put(self, doc: Document) -> None:
        self._docs[doc.doc_id] = doc
        self._docs.move_to_end(doc.doc_id)
        while len(self._docs) > self._max:
            self._docs.popitem(last=False)

    def get(self, doc_id: str) -> Document | None:
        doc = self._docs.get(doc_id)
        if doc and self._now() - doc.uploaded_at > self._ttl:
            del self._docs[doc_id]
            return None
        if doc:
            self._docs.move_to_end(doc_id)
        return doc


class ModalDictStore:
    """Backed by a Modal Dict, so any container can answer for any upload.

    A second, small Dict maps doc_id -> upload time, so sweeping expired
    documents never has to read the documents themselves."""

    def __init__(self, name: str = "docqa-documents", ttl_seconds: float = 86_400, now: Callable[[], float] = time.time):
        import modal

        self._dict = modal.Dict.from_name(name, create_if_missing=True)
        self._index = modal.Dict.from_name(f"{name}-index", create_if_missing=True)
        self._ttl = ttl_seconds
        self._now = now

    def put(self, doc: Document) -> None:
        self._dict[doc.doc_id] = doc.model_dump()
        self._index[doc.doc_id] = doc.uploaded_at
        self._sweep()

    def get(self, doc_id: str) -> Document | None:
        data = self._dict.get(doc_id)
        if not data:
            return None
        doc = Document.model_validate(data)
        if self._now() - doc.uploaded_at > self._ttl:
            self._delete(doc_id)
            return None
        return doc

    def _sweep(self) -> None:
        cutoff = self._now() - self._ttl
        for doc_id, uploaded_at in list(self._index.items()):
            if uploaded_at < cutoff:
                self._delete(doc_id)

    def _delete(self, doc_id: str) -> None:
        self._dict.pop(doc_id, None)
        self._index.pop(doc_id, None)


def make_store(backend: str, ttl_seconds: float = 86_400) -> DocumentStore:
    if backend == "modal":
        return ModalDictStore(ttl_seconds=ttl_seconds)
    return InMemoryStore(ttl_seconds=ttl_seconds)
