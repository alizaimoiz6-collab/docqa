import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docqa.budget import InMemoryLedger, usage_cost
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


def make_client(answerer=None, ledger=None, **overrides) -> TestClient:
    settings = Settings(**overrides)
    app = create_app(settings=settings, store=InMemoryStore(), answerer=answerer or FakeAnswerer(), ledger=ledger)
    return TestClient(app)


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


def test_upload_is_rate_limited_per_client():
    client = make_client(upload_rate_limit_per_min=2)
    upload_pdf(client)
    upload_pdf(client)
    data = (SAMPLES / "acme_policy.pdf").read_bytes()
    resp = client.post("/upload", files={"file": ("acme_policy.pdf", data, "application/pdf")})
    assert resp.status_code == 429
    assert "uploads per minute" in resp.json()["detail"]


# ---- Daily budget -------------------------------------------------------------

OK_ANSWER = LLMAnswer(answerable=True, answer="ok", confidence="high", citations=[])


class Clock:
    def __init__(self):
        self.day = dt.date(2026, 10, 27)

    def __call__(self):
        return self.day


def test_daily_budget_blocks_questions_once_spent():
    ledger = InMemoryLedger()
    ledger.add(1.00)
    fake = FakeAnswerer(OK_ANSWER)
    client = make_client(fake, ledger=ledger, daily_budget_usd=1.00)
    doc = upload_pdf(client)

    resp = client.post("/ask", json={"doc_id": doc["doc_id"], "question": "What is covered?"})

    assert resp.status_code == 429
    assert resp.json()["error"] == "daily_limit_reached"
    assert "tomorrow" in resp.json()["detail"]
    assert int(resp.headers["Retry-After"]) > 0
    assert fake.calls == []  # Claude was never called


def test_spend_is_recorded_and_resets_the_next_day():
    clock = Clock()
    ledger = InMemoryLedger(today=clock)
    # FakeAnswerer reports 100 input + 20 output tokens on an unknown model,
    # priced at the fallback tier: (100*10 + 20*50) / 1e6 = $0.002 per question.
    client = make_client(FakeAnswerer(OK_ANSWER), ledger=ledger, daily_budget_usd=0.003)
    doc = upload_pdf(client)
    payload = {"doc_id": doc["doc_id"], "question": "What is covered?"}

    first = client.post("/ask", json=payload)
    assert first.status_code == 200
    assert first.json()["cost_usd"] == pytest.approx(0.002)
    assert client.post("/ask", json=payload).status_code == 200  # $0.002 < $0.003, allowed
    assert client.post("/ask", json=payload).json()["error"] == "daily_limit_reached"  # $0.004 spent

    clock.day += dt.timedelta(days=1)
    assert client.post("/ask", json=payload).status_code == 200
    assert client.get("/health").json()["spent_today_usd"] == pytest.approx(0.002)


def test_zero_budget_disables_the_cap():
    ledger = InMemoryLedger()
    ledger.add(500.0)
    client = make_client(FakeAnswerer(OK_ANSWER), ledger=ledger, daily_budget_usd=0)
    doc = upload_pdf(client)
    assert client.post("/ask", json={"doc_id": doc["doc_id"], "question": "What is covered?"}).status_code == 200


def test_usage_cost_uses_model_prices():
    usage = Usage(input_tokens=1000, output_tokens=500, cache_read_input_tokens=10_000, cache_creation_input_tokens=2000)
    # Opus 5.5: 1000*4 + 500*20 + 10000*0.20 + 2000*5 = 26,000 per million tokens
    assert usage_cost("claude-opus-5-5", usage) == pytest.approx(0.026)


# ---- Document expiry ------------------------------------------------------------


def test_documents_expire_after_ttl():
    import time

    offset = {"s": 0.0}
    store = InMemoryStore(ttl_seconds=3600, now=lambda: time.time() + offset["s"])
    app = create_app(settings=Settings(), store=store, answerer=FakeAnswerer(OK_ANSWER), ledger=InMemoryLedger())
    client = TestClient(app)
    doc = upload_pdf(client)
    payload = {"doc_id": doc["doc_id"], "question": "What is covered?"}

    assert client.post("/ask", json=payload).status_code == 200
    offset["s"] = 3601  # just over an hour later
    resp = client.post("/ask", json=payload)
    assert resp.status_code == 404
    assert "expired" in resp.json()["detail"]
