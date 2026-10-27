"""Where parsed documents live between /upload and /ask."""

from collections import OrderedDict
from typing import Protocol

from .schemas import Document


class DocumentStore(Protocol):
    def put(self, doc: Document) -> None: ...
    def get(self, doc_id: str) -> Document | None: ...


class InMemoryStore:
    """Process-local LRU. Fine for local dev and a single container."""

    def __init__(self, max_docs: int = 200):
        self._docs: OrderedDict[str, Document] = OrderedDict()
        self._max = max_docs

    def put(self, doc: Document) -> None:
        self._docs[doc.doc_id] = doc
        self._docs.move_to_end(doc.doc_id)
        while len(self._docs) > self._max:
            self._docs.popitem(last=False)

    def get(self, doc_id: str) -> Document | None:
        doc = self._docs.get(doc_id)
        if doc:
            self._docs.move_to_end(doc_id)
        return doc


class ModalDictStore:
    """Backed by a Modal Dict, so any container can answer for any upload."""

    def __init__(self, name: str = "docqa-documents"):
        import modal

        self._dict = modal.Dict.from_name(name, create_if_missing=True)

    def put(self, doc: Document) -> None:
        self._dict[doc.doc_id] = doc.model_dump()

    def get(self, doc_id: str) -> Document | None:
        data = self._dict.get(doc_id)
        return Document.model_validate(data) if data else None


def make_store(backend: str) -> DocumentStore:
    if backend == "modal":
        return ModalDictStore()
    return InMemoryStore()
