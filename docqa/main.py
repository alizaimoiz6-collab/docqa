"""FastAPI app: POST /upload a document, POST /ask a question about it."""

import logging
import time
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from .budget import SpendLedger, make_ledger, seconds_until_utc_midnight, usage_cost
from .config import Settings, get_settings
from .documents import DocumentError, parse_upload, quote_in_chunk, select_chunks
from .llm import Answerer, ClaudeAnswerer, LLMError
from .schemas import AskRequest, AskResponse, Citation, ErrorResponse, UploadResponse
from .store import DocumentStore, make_store

log = logging.getLogger("docqa")


class RateLimiter:
    """Sliding one-minute window per client key, kept in process memory."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> float | None:
        """Record a hit; return seconds to wait if the client is over the limit."""
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return 60 - (now - hits[0])
        hits.append(now)
        return None


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_app(
    settings: Settings | None = None,
    store: DocumentStore | None = None,
    answerer: Answerer | None = None,
    ledger: SpendLedger | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    store = store or make_store(settings.store_backend, ttl_seconds=settings.doc_ttl_hours * 3600)
    ledger = ledger or make_ledger(settings.store_backend)
    ask_limiter = RateLimiter(settings.ask_rate_limit_per_min)
    upload_limiter = RateLimiter(settings.upload_rate_limit_per_min)
    _answerer = answerer

    def get_answerer() -> Answerer:
        nonlocal _answerer
        if _answerer is None:  # built lazily so the app starts without an API key
            _answerer = ClaudeAnswerer(settings)
        return _answerer

    app = FastAPI(
        title="DocQA",
        version="1.0.0",
        description="Upload a PDF or text document, ask questions, get answers with verified citations.",
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    error_responses = {code: {"model": ErrorResponse} for code in (404, 413, 415, 422, 429, 502, 503, 504)}

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        headers = getattr(exc, "headers", None)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": _reason(exc.status_code), "detail": str(exc.detail)},
            headers=headers,
        )

    @app.exception_handler(DocumentError)
    async def document_error(_: Request, exc: DocumentError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": _reason(exc.status_code), "detail": exc.detail},
        )

    @app.exception_handler(LLMError)
    async def llm_error(_: Request, exc: LLMError):
        log.warning("LLM error %s: %s", exc.status_code, exc.detail)
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": _reason(exc.status_code), "detail": exc.detail},
            headers=headers,
        )

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/docs")

    def check_rate(limiter: RateLimiter, request: Request, what: str) -> None:
        wait = limiter.check(_client_key(request))
        if wait is not None:
            raise HTTPException(
                429,
                f"Limit is {limiter.per_minute} {what} per minute.",
                headers={"Retry-After": str(int(wait) + 1)},
            )

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "model": settings.model,
            "daily_budget_usd": settings.daily_budget_usd,
            "spent_today_usd": round(ledger.spent_today(), 4),
        }

    @app.post("/upload", response_model=UploadResponse, responses=error_responses)
    async def upload(request: Request, file: UploadFile = File(...)):
        check_rate(upload_limiter, request, "uploads")
        limit = settings.max_upload_mb * 1024 * 1024
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB limit.")

        doc = parse_upload(file.filename or "document", data, settings.chunk_chars)
        store.put(doc)
        log.info("Stored %s (%s, %d chunks)", doc.doc_id, doc.filename, len(doc.chunks))
        return UploadResponse(
            doc_id=doc.doc_id,
            filename=doc.filename,
            kind=doc.kind,
            pages=doc.pages,
            chunks=len(doc.chunks),
            characters=doc.char_count,
        )

    @app.post("/ask", response_model=AskResponse, responses=error_responses)
    async def ask(body: AskRequest, request: Request, answerer: Answerer = Depends(get_answerer)):
        check_rate(ask_limiter, request, "questions")

        if settings.daily_budget_usd > 0 and ledger.spent_today() >= settings.daily_budget_usd:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "daily_limit_reached",
                    "detail": "Daily demo limit reached. Please try again tomorrow.",
                },
                headers={"Retry-After": str(seconds_until_utc_midnight())},
            )

        doc = store.get(body.doc_id)
        if doc is None:
            raise HTTPException(
                404,
                f"Unknown or expired doc_id. Documents are deleted {settings.doc_ttl_hours:g} hours "
                "after upload; upload it again.",
            )

        chunks, retrieval = select_chunks(
            doc, body.question, settings.context_char_budget, settings.top_k_chunks
        )
        started = time.perf_counter()
        result = await answerer.answer(body.question, doc.filename, chunks)
        latency_ms = int((time.perf_counter() - started) * 1000)
        cost = usage_cost(result.model, result.usage)
        ledger.add(cost)

        by_id = {c.id: c for c in doc.chunks}
        citations = []
        for cit in result.answer.citations if result.answer.answerable else []:
            chunk = by_id.get(cit.chunk_id)
            citations.append(
                Citation(
                    chunk_id=cit.chunk_id,
                    page=chunk.page if chunk else None,
                    quote=cit.quote,
                    verified=bool(chunk) and quote_in_chunk(cit.quote, chunk.text),
                )
            )

        return AskResponse(
            doc_id=doc.doc_id,
            question=body.question,
            answerable=result.answer.answerable,
            answer=result.answer.answer,
            confidence=result.answer.confidence,
            citations=citations,
            retrieval=retrieval,
            model=result.model,
            usage=result.usage,
            cost_usd=round(cost, 6),
            latency_ms=latency_ms,
        )

    return app


def _reason(status: int) -> str:
    return {
        404: "not_found",
        413: "payload_too_large",
        415: "unsupported_media_type",
        422: "unprocessable",
        429: "rate_limited",
        500: "server_error",
        502: "upstream_error",
        503: "upstream_unavailable",
        504: "upstream_timeout",
    }.get(status, "error")
