"""Pydantic models: what Claude must return, and what the API returns."""

from typing import Literal

from pydantic import BaseModel, Field

# ---- Structured output contract for Claude ----------------------------------


class LLMCitation(BaseModel):
    chunk_id: str = Field(description="ID of the passage the quote comes from, e.g. 'C12'.")
    quote: str = Field(
        description="A short verbatim quote (one sentence or less) copied exactly from that passage."
    )


class LLMAnswer(BaseModel):
    answerable: bool = Field(description="False if the document does not contain the answer.")
    answer: str = Field(description="Concise answer grounded only in the document.")
    confidence: Literal["high", "medium", "low"]
    citations: list[LLMCitation] = Field(
        description="Passages that support the answer. Empty when answerable is false."
    )


# ---- Public API -------------------------------------------------------------


class Chunk(BaseModel):
    id: str
    page: int | None = None
    text: str


class Document(BaseModel):
    doc_id: str
    filename: str
    kind: Literal["pdf", "text"]
    pages: int | None
    chunks: list[Chunk]

    @property
    def char_count(self) -> int:
        return sum(len(c.text) for c in self.chunks)


class UploadResponse(BaseModel):
    doc_id: str
    filename: str
    kind: Literal["pdf", "text"]
    pages: int | None
    chunks: int
    characters: int


class AskRequest(BaseModel):
    doc_id: str
    question: str = Field(min_length=3, max_length=2000)


class Citation(BaseModel):
    chunk_id: str
    page: int | None
    quote: str
    verified: bool = Field(description="True if the quote was found verbatim in the cited passage.")


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class AskResponse(BaseModel):
    doc_id: str
    question: str
    answerable: bool
    answer: str
    confidence: Literal["high", "medium", "low"]
    citations: list[Citation]
    retrieval: Literal["full_document", "top_k"]
    model: str
    usage: Usage
    cost_usd: float
    latency_ms: int


class ErrorResponse(BaseModel):
    error: str
    detail: str
