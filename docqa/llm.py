"""Claude integration: one structured-output call per question."""

import html
from dataclasses import dataclass
from typing import Protocol

import anthropic

from .config import Settings
from .schemas import Chunk, LLMAnswer, Usage

SYSTEM_PROMPT = """\
You answer questions about a single document supplied below as numbered passages.

Rules:
- Use only information in the passages. Do not rely on outside knowledge.
- Every factual claim in your answer must be supported by at least one citation.
- A citation is the passage id plus a short quote copied character-for-character from that \
passage. Do not paraphrase inside a quote. Use "..." only to skip words within one passage.
- If the passages do not contain the answer, set answerable to false, say briefly what is \
missing, and return no citations.
- confidence is "high" when the passages state the answer directly, "medium" when it takes \
light inference across passages, "low" when the support is partial or ambiguous.
- Keep the answer concise: a few sentences, or a short list when the question asks for several items."""


class LLMError(Exception):
    """A Claude call failed in a way the API should report. Carries an HTTP status."""

    def __init__(self, status_code: int, detail: str, retry_after: str | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after


@dataclass
class AnswerResult:
    answer: LLMAnswer
    model: str
    usage: Usage


class Answerer(Protocol):
    async def answer(self, question: str, filename: str, chunks: list[Chunk]) -> AnswerResult: ...


def render_document(filename: str, chunks: list[Chunk]) -> str:
    parts = [f'<document name="{html.escape(filename)}">']
    for c in chunks:
        page = f' page="{c.page}"' if c.page is not None else ""
        parts.append(f'<passage id="{c.id}"{page}>\n{c.text}\n</passage>')
    parts.append("</document>")
    return "\n".join(parts)


class ClaudeAnswerer:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        self.client = client or anthropic.AsyncAnthropic(
            timeout=settings.request_timeout, max_retries=settings.max_retries
        )

    async def answer(self, question: str, filename: str, chunks: list[Chunk]) -> AnswerResult:
        s = self.settings
        try:
            response = await self.client.beta.messages.parse(
                model=s.model,
                max_tokens=s.max_output_tokens,
                # The document goes in the system prompt with a cache breakpoint, so
                # follow-up questions on the same document reuse the cached prefix.
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT},
                    {
                        "type": "text",
                        "text": render_document(filename, chunks),
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                messages=[{"role": "user", "content": question}],
                output_format=LLMAnswer,
                output_config={"effort": s.effort},
                # If a safety classifier declines, the API retries on a suitable model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise LLMError(
                429,
                "The model is rate limited right now. Try again shortly.",
                retry_after=exc.response.headers.get("retry-after"),
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise LLMError(504, "The model took too long to answer. Try a narrower question.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(502, "Could not reach the model provider.") from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMError(500, "The server's model credentials are missing or invalid.") from exc
        except TypeError as exc:
            # The SDK raises a bare TypeError when no API key or profile is configured.
            if "authentication" not in str(exc):
                raise
            raise LLMError(500, "The server has no ANTHROPIC_API_KEY configured.") from exc
        except anthropic.RequestTooLargeError as exc:
            raise LLMError(413, "The document is too large to send to the model.") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:  # includes 529 overloaded
                raise LLMError(503, "The model provider is temporarily unavailable.") from exc
            raise LLMError(502, f"Model request rejected: {exc.message}") from exc

        if response.stop_reason == "refusal":
            raise LLMError(422, "The model declined to answer this question.")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMError(502, "The model returned an incomplete answer. Try again.")

        u = response.usage
        return AnswerResult(
            answer=response.parsed_output,
            model=response.model,
            usage=Usage(
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_read_input_tokens=u.cache_read_input_tokens or 0,
                cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
            ),
        )
