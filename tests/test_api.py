from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docqa.config import Settings
from docqa.llm import AnswerResult, LLMError
from docqa.main import create_app
from docqa.schemas import LLMAnswer, LLMCitation, Usage
from docqa.store import InMemoryStore

SAMPLES = Path(__file__).parent.parent / "sample_docs"


class FakeAnswerer:
    def __init__(self, answer: LLMAnswer | None = None, error: LLMError | None = None):
        self._answer = answer
        self._error = error
        self.calls: list[tuple[str, list]] = []

    async def answer(self, question, filename, chunks):
        self.calls.append((question, chunks))
        if self._error:
            raise self._error
        return AnswerResult(
            answer=self._answer,
            model="fake-model",
            usage=Usage(input_tokens=100, output_tokens=20),
        )


def make_client(answerer=None, **overrides) -> TestClient:
    settings = Settings(**overrides)
    return TestClient(create_app(settings=settings, store=InMemoryStore(), answerer=answerer or FakeAnswerer()))


def upload_pdf(client: TestClient) -> dict:
    data = (SAMPLES / "acme_policy.pdf").read_bytes()
    resp = client.post("/upload", files={"file": ("acme_policy.pdf", data, "application/pdf")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_upload_pdf_reports_pages_and_chunks():
    body = upload_pdf(make_client())
    assert body["kind"] == "pdf"
    assert body["pages"] == 3
    assert body["chunks"] == 3


def test_upload_text():
    client = make_client()
    data = (SAMPLES / "remote_work_policy.txt").read_bytes()
    resp = client.post("/upload", files={"file": ("policy.txt", data, "text/plain")})
    assert resp.status_code == 200
    assert resp.json()["kind"] == "text"
    assert resp.json()["pages"] is None


@pytest.mark.parametrize(
    "name,data,status",
    [
        ("image.png", b"\x89PNG....", 415),
        ("empty.txt", b"", 422),
        ("broken.pdf", b"%PDF-1.4 not really a pdf", 422),
    ],
)
def test_upload_rejects_bad_files(name, data, status):
    resp = make_client().post("/upload", files={"file": (name, data)})
    assert resp.status_code == status
    assert resp.json()["detail"]


def test_upload_rejects_oversized_file():
    client = make_client(max_upload_mb=1)
    resp = client.post("/upload", files={"file": ("big.txt", b"a" * (1024 * 1024 + 10))})
    assert resp.status_code == 413


def test_ask_unknown_doc_is_404():
    resp = make_client().post("/ask", json={"doc_id": "nope", "question": "What is covered?"})
    assert resp.status_code == 404


def test_ask_returns_answer_with_verified_and_unverified_citations():
    answer = LLMAnswer(
        answerable=True,
        answer="Claims must be filed within 90 days; ER copay is $150.",
        confidence="high",
        citations=[
            LLMCitation(chunk_id="C2", quote="Claims must be submitted within 90 days"),
            LLMCitation(chunk_id="C3", quote="Emergency room visits have a $150 copay ... waived"),
            LLMCitation(chunk_id="C3", quote="This sentence is not in the document"),
            LLMCitation(chunk_id="C99", quote="made-up passage"),
        ],
    )
    fake = FakeAnswerer(answer)
    client = make_client(fake)
    doc = upload_pdf(client)

    resp = client.post("/ask", json={"doc_id": doc["doc_id"], "question": "What are the claim deadlines?"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retrieval"] == "full_document"
    assert [(c["page"], c["verified"]) for c in body["citations"]] == [
        (2, True),
        (3, True),
        (3, False),
        (None, False),
    ]
    assert len(fake.calls[0][1]) == 3  # whole document sent


def test_unanswerable_drops_citations():
    answer = LLMAnswer(
        answerable=False,
        answer="The document does not mention dental coverage.",
        confidence="low",
        citations=[LLMCitation(chunk_id="C1", quote="Acme Health")],
    )
    client = make_client(FakeAnswerer(answer))
    doc = upload_pdf(client)
    body = client.post("/ask", json={"doc_id": doc["doc_id"], "question": "Is dental covered?"}).json()
    assert body["answerable"] is False
    assert body["citations"] == []


def test_ask_is_rate_limited_per_client():
    answer = LLMAnswer(answerable=True, answer="ok", confidence="high", citations=[])
    client = make_client(FakeAnswerer(answer), ask_rate_limit_per_min=2)
    doc = upload_pdf(client)
    payload = {"doc_id": doc["doc_id"], "question": "What is covered?"}

    assert client.post("/ask", json=payload).status_code == 200
    assert client.post("/ask", json=payload).status_code == 200
    resp = client.post("/ask", json=payload)
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_llm_errors_map_to_http_status(status):
    error = LLMError(status, "upstream problem", retry_after="7" if status == 429 else None)
    client = make_client(FakeAnswerer(error=error))
    doc = upload_pdf(client)
    resp = client.post("/ask", json={"doc_id": doc["doc_id"], "question": "What is covered?"})
    assert resp.status_code == status
    assert resp.json()["detail"] == "upstream problem"
    if status == 429:
        assert resp.headers["Retry-After"] == "7"


def test_question_validation():
    resp = make_client().post("/ask", json={"doc_id": "x", "question": "?"})
    assert resp.status_code == 422
