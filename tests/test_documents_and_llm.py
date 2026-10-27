import asyncio
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from docqa.config import Settings
from docqa.documents import parse_upload, quote_in_chunk, select_chunks
from docqa.llm import ClaudeAnswerer, LLMError
from docqa.schemas import Chunk, LLMAnswer


def test_chunks_respect_size_limit():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(30))
    doc = parse_upload("long.txt", text.encode(), chunk_chars=500)
    assert len(doc.chunks) > 5
    assert all(len(c.text) <= 500 for c in doc.chunks)
    assert [c.id for c in doc.chunks[:3]] == ["C1", "C2", "C3"]


def test_select_chunks_uses_top_k_for_large_documents():
    filler = [f"Section {i} discusses general administrative matters." for i in range(50)]
    filler[37] = "The refund window for annual subscriptions is 30 days."
    doc = parse_upload("big.txt", "\n\n".join(filler).encode(), chunk_chars=60)

    chunks, mode = select_chunks(doc, "What is the refund window?", char_budget=500, top_k=3)

    assert mode == "top_k"
    assert len(chunks) == 3
    assert any("refund window" in c.text for c in chunks)


def test_quote_matching_tolerates_whitespace_case_and_ellipsis():
    chunk = "Claims must be submitted  within\n90 days of the date of service."
    assert quote_in_chunk("claims must be submitted within 90 days", chunk)
    assert quote_in_chunk("Claims must be submitted ... date of service.", chunk)
    assert not quote_in_chunk("Claims must be submitted within 60 days", chunk)
    assert not quote_in_chunk("   ", chunk)


# ---- ClaudeAnswerer error mapping (no network) --------------------------------

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, status, headers=None):
    response = httpx2.Response(status, headers=headers or {}, request=REQUEST)
    return cls("boom", response=response, body=None)


class FakeClient:
    def __init__(self, result=None, error=None):
        async def parse(**kwargs):
            self.kwargs = kwargs
            if error:
                raise error
            return result

        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=parse))


def run(answerer):
    return asyncio.run(answerer.answer("q?", "doc.txt", [Chunk(id="C1", text="hello")]))


@pytest.mark.parametrize(
    "error,status",
    [
        (_status_error(anthropic.RateLimitError, 429, {"retry-after": "12"}), 429),
        (anthropic.APITimeoutError(request=REQUEST), 504),
        (anthropic.APIConnectionError(request=REQUEST), 502),
        (_status_error(anthropic.AuthenticationError, 401), 500),
        (_status_error(anthropic.InternalServerError, 500), 503),
        (_status_error(anthropic.BadRequestError, 400), 502),
    ],
)
def test_sdk_errors_become_llm_errors(error, status):
    answerer = ClaudeAnswerer(Settings(), client=FakeClient(error=error))
    with pytest.raises(LLMError) as info:
        run(answerer)
    assert info.value.status_code == status
    if status == 429:
        assert info.value.retry_after == "12"


def _response(stop_reason="end_turn", parsed=None):
    usage = SimpleNamespace(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=None, cache_creation_input_tokens=None
    )
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed, usage=usage, model="m")


def test_refusal_and_truncation_are_reported():
    with pytest.raises(LLMError) as info:
        run(ClaudeAnswerer(Settings(), client=FakeClient(_response("refusal"))))
    assert info.value.status_code == 422

    with pytest.raises(LLMError) as info:
        run(ClaudeAnswerer(Settings(), client=FakeClient(_response("max_tokens"))))
    assert info.value.status_code == 502


def test_request_shape():
    parsed = LLMAnswer(answerable=True, answer="hi", confidence="high", citations=[])
    client = FakeClient(_response(parsed=parsed))
    result = run(ClaudeAnswerer(Settings(model="claude-opus-5-5"), client=client))

    assert result.answer.answer == "hi"
    kw = client.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_format"] is LLMAnswer
    assert kw["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert '<passage id="C1">' in kw["system"][1]["text"]
