# DocQA: document Q&A with verified citations

Upload a PDF or text file, ask a question, and get a structured answer in which every claim points to a page and quotes the passage it came from. Each quote is then checked against the source text on the server, so a citation the model made up shows up as unverified.

**Live demo:** [UI](https://alizaimoiz6--docqa-ui.us-east.modal.run) · [API docs](https://alizaimoiz6--docqa-api.us-east.modal.run/docs) (it may take a few seconds to wake up after being idle) · **Case study:** [one-page PDF](docs/DocQA_Case_Study_Moiz_Alizai.pdf)

Built by [Moiz Alizai](https://www.linkedin.com/in/moizalizai/).

![stack](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white) ![stack](https://img.shields.io/badge/Claude_API-D97757?logo=anthropic&logoColor=white) ![stack](https://img.shields.io/badge/Streamlit-FF4B4B?logo=streamlit&logoColor=white) ![stack](https://img.shields.io/badge/Modal-000000) ![tests](https://img.shields.io/badge/tests-32_passing-brightgreen)

## Architecture

```mermaid
flowchart LR
    U([User]) --> UI["Streamlit UI<br/>(Modal web_server)"]
    UI -- "POST /upload" --> API
    UI -- "POST /ask" --> API

    subgraph API["FastAPI service (Modal asgi_app)"]
        direction TB
        P["Parse & chunk<br/>pypdf → numbered passages<br/>C1, C2, … with page numbers"]
        S[("Document store<br/>modal.Dict / in-memory LRU")]
        R["Select passages<br/>whole doc if ≤ ~25k tokens,<br/>else BM25 top-k"]
        V["Verify citations<br/>quote ⊂ cited passage?"]
        P --> S
        S --> R
    end

    R -- "system: rules + passages (prompt-cached)<br/>user: question<br/>output_format: LLMAnswer" --> C["Claude API<br/>claude-opus-5-5<br/>structured outputs"]
    C -- "validated Pydantic LLMAnswer" --> V
    V -- "AskResponse JSON" --> UI
```

**Request flow for `/ask`**

```mermaid
sequenceDiagram
    participant UI as Streamlit
    participant API as FastAPI
    participant Store as Doc store
    participant Claude as Claude API
    UI->>API: POST /ask {doc_id, question}
    API->>API: per-client rate limit (429 + Retry-After)
    API->>Store: get(doc_id)  (404 if missing)
    API->>API: select passages (full doc or BM25 top-k)
    API->>Claude: messages.parse(output_format=LLMAnswer)
    Note over API,Claude: SDK retries 429/5xx/timeouts (2x),<br/>then mapped to 429 / 503 / 504
    Claude-->>API: LLMAnswer {answerable, answer, confidence, citations[]}
    API->>API: map chunk_id → page, verify each quote
    API-->>UI: AskResponse
```

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/upload` | `multipart/form-data` field `file` (`.pdf`, `.txt`, `.md`; ≤ 20 MB) | `doc_id`, page/passage counts |
| `POST` | `/ask` | `{"doc_id": "...", "question": "..."}` | `AskResponse` (below) |
| `GET` | `/health` | – | status, model, today's spend vs. daily budget |
| `GET` | `/docs` | – | Interactive OpenAPI docs |

```bash
API_URL=https://alizaimoiz6--docqa-api.us-east.modal.run
curl -F file=@sample_docs/acme_policy.pdf $API_URL/upload
# {"doc_id":"ff5962535418","filename":"acme_policy.pdf","kind":"pdf","pages":3,"chunks":3,"characters":324}

curl -H 'content-type: application/json' \
     -d '{"doc_id":"ff5962535418","question":"When must claims be filed, and what is the ER copay?"}' \
     $API_URL/ask
```

Real response from the sample PDF (note `cache_read_input_tokens`: the document came from the prompt cache):

```json
{
  "doc_id": "776e6674b032",
  "question": "When must claims be filed, and what is the ER copay?",
  "answerable": true,
  "answer": "Claims must be submitted within 90 days of the date of service; late claims are denied unless the member shows good cause. The emergency room copay is $150, which is waived if the member is admitted to the hospital.",
  "confidence": "high",
  "citations": [
    {"chunk_id": "C2", "page": 2, "quote": "Claims must be submitted within 90 days of the date of service.", "verified": true},
    {"chunk_id": "C2", "page": 2, "quote": "Late claims are denied unless the member shows good cause.", "verified": true},
    {"chunk_id": "C3", "page": 3, "quote": "Emergency room visits have a $150 copay that is waived if the member is admitted to the hospital.", "verified": true}
  ],
  "retrieval": "full_document",
  "model": "claude-opus-5-5",
  "usage": {"input_tokens": 23, "output_tokens": 210, "cache_read_input_tokens": 1091, "cache_creation_input_tokens": 0},
  "cost_usd": 0.00451,
  "latency_ms": 2374
}
```

A question the document can't answer returns `answerable: false` and no citations:

```json
{"question": "Does the policy cover dental cleanings?", "answerable": false, "confidence": "high", "citations": [],
 "answer": "The document doesn't say whether dental cleanings are covered. It only addresses outpatient visit copays, claim submission deadlines, and emergency room copays."}
```

### Errors

Every error has the same shape: `{"error": "<code>", "detail": "<message>"}`.

| Status | When |
|---|---|
| `404 not_found` | Unknown or expired `doc_id` (documents are deleted `DOC_TTL_HOURS` after upload) |
| `413 payload_too_large` | Upload over `MAX_UPLOAD_MB`, or document too large for the model |
| `415 unsupported_media_type` | Not a PDF or text file |
| `422 unprocessable` | Empty file, scanned PDF without a text layer, encrypted PDF, bad question, or the model declined |
| `429 rate_limited` | Client exceeded `ASK_RATE_LIMIT_PER_MIN` or `UPLOAD_RATE_LIMIT_PER_MIN`, or Claude rate-limited us after retries. Includes `Retry-After` |
| `429 daily_limit_reached` | The whole app has spent `DAILY_BUDGET_USD` on Claude today (UTC). `Retry-After` points to midnight UTC |
| `502 upstream_error` | Network failure reaching Claude, or an incomplete answer |
| `503 upstream_unavailable` | Claude 5xx / overloaded after retries |
| `504 upstream_timeout` | Claude call exceeded `ANTHROPIC_TIMEOUT` after retries |

## Design decisions

- **Structured outputs over free text.** `client.beta.messages.parse(output_format=LLMAnswer)` constrains Claude to a JSON schema generated from a Pydantic model, so the response is always valid and typed. There's no regex parsing and no "please reply in JSON".
- **Citations the server can check.** Claude's native Citations feature can't be combined with structured outputs (the API rejects the pair). Instead, the document is sent as numbered `<passage id="C7" page="3">` blocks and the schema asks for `{chunk_id, quote}` pairs. The server maps each `chunk_id` back to its page and confirms the quote appears in that passage, ignoring case, whitespace and `...` elisions. Unverified citations are still returned, flagged `verified: false`, so the UI can warn about them.
- **Long-context first, retrieval as a fallback.** Claude Opus 5.5 has a 1M-token window, so documents up to about 100k characters (~25k tokens, ~50 pages) are sent whole: nothing gets missed and there's no embedding infrastructure to run. Larger documents are narrowed to the top 40 passages by BM25, a dependency-free lexical ranker. `retrieval` in the response says which path ran.
- **Prompt caching.** The document is placed in the system prompt with a `cache_control` breakpoint, so follow-up questions on the same document read it from cache instead of paying full input cost again.
- **"I don't know" is a first-class answer.** `answerable: false` lets the model say the document doesn't cover the question, rather than inventing an answer.
- **Failure handling in two layers.** The Anthropic SDK retries 429, 5xx and timeouts with exponential backoff. Whatever still fails is mapped to an HTTP status the client can act on, with the upstream `Retry-After` passed through. A per-client sliding-window limiter protects the API key on the public URL. Server-side refusal fallback is enabled, so a safety-classifier decline is retried on a suitable model before the request fails.
- **A hard daily spend cap.** Every answer's cost is computed from the token usage Claude reports and added to a ledger in a `modal.Dict` shared by all containers. Each container writes only its own key, so concurrent writes never clobber each other. Once the day's total reaches `DAILY_BUDGET_USD`, `/ask` returns `daily_limit_reached` without calling Claude, so a public URL can't run up the bill.

## Run locally

Requires Python 3.10+ and an [Anthropic API key](https://console.anthropic.com/).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=sk-ant-...

uvicorn docqa.server:app --reload --port 8000          # API at http://localhost:8000/docs
streamlit run frontend/streamlit_app.py                 # UI at http://localhost:8501 (in a second terminal)
```

Sample documents are in [`sample_docs/`](sample_docs).

### Evaluation

[`eval/run_eval.py`](eval/run_eval.py) uploads the three sample documents to a deployment and asks 18 questions: 14 with known answers and 4 that the documents can't answer. It checks each answer against expected facts and reports accuracy, citation verification, latency and cost. Results against the live Modal deployment, from [`eval/results.md`](eval/results.md):

| Metric | Value |
|---|---|
| Answerable questions answered correctly | 14/14 |
| Unanswerable questions correctly declined | 4/4 |
| Citations verified verbatim | 31/31 (100%) |
| End-to-end latency, median / p90 | 3.5s / 4.1s |
| Avg cost per question | $0.005 (first question on a doc $0.012, cached follow-ups $0.004) |
| Saved by prompt caching | 55% |

```bash
python eval/run_eval.py --api https://alizaimoiz6--docqa-api.us-east.modal.run
```

The documents are short and the set is small. Treat these as a smoke test, not a benchmark.

### Tests

```bash
pytest
```

The 32 tests cover upload validation, chunking, BM25 selection, quote verification, rate limiting, the daily budget (including spend shared across containers), and mapping each Anthropic SDK exception to an HTTP status. Claude is stubbed out, so the suite runs offline and costs nothing.

## Deploy to Modal

```bash
pip install modal && modal setup                                   # one-time browser login
modal secret create anthropic-secret ANTHROPIC_API_KEY=sk-ant-...
modal deploy modal_app.py
```

Modal prints two public URLs: `https://<workspace>--docqa-api.modal.run` (FastAPI) and `https://<workspace>--docqa-ui.modal.run` (Streamlit). The UI finds the API URL automatically. Uploads are stored in a `modal.Dict`, so any container can answer questions about any upload, and containers scale to zero when idle.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Required |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Model ID |
| `ANTHROPIC_EFFORT` | `medium` | `low` / `medium` / `high` / `xhigh` / `max`. Trades depth for latency and cost |
| `ANTHROPIC_TIMEOUT` | `90` | Seconds per Claude request |
| `ANTHROPIC_MAX_RETRIES` | `2` | SDK retries for 429/5xx/timeouts |
| `MAX_UPLOAD_MB` | `20` | Upload size cap |
| `CONTEXT_CHAR_BUDGET` | `100000` | Above this size, switch from whole document to top-k passages |
| `TOP_K_CHUNKS` | `40` | Passages sent in top-k mode |
| `ASK_RATE_LIMIT_PER_MIN` | `10` | Per-client `/ask` limit |
| `UPLOAD_RATE_LIMIT_PER_MIN` | `5` | Per-client `/upload` limit |
| `DAILY_BUDGET_USD` | `1.0` | Total Claude spend allowed per UTC day across all visitors. `0` disables it |
| `DOC_TTL_HOURS` | `24` | Uploaded documents are deleted this long after upload |
| `DOCQA_STORE` | `memory` | `memory` or `modal` |
| `API_URL` | `http://localhost:8000` | Where the Streamlit UI sends requests |

## Project layout

```
docqa/
  main.py        FastAPI app factory, routes, error handlers, rate limiter
  llm.py         Claude call, prompt, SDK-exception → HTTP mapping
  documents.py   PDF/text parsing, chunking, BM25 selection, quote verification
  schemas.py     Pydantic models (Claude's output contract + API responses)
  store.py       In-memory LRU and modal.Dict document stores
  config.py      Environment-driven settings
frontend/streamlit_app.py
modal_app.py     Modal deployment (API + UI)
eval/            Question set, runner, and latest results against the live app
sample_docs/     Fictional documents for testing
tests/
```

## Security and privacy

- **API key:** stored only as a Modal secret (`anthropic-secret`). It's never in the code, the repo or a response. GitHub secret scanning and push protection are on.
- **Spend:** a shared `DAILY_BUDGET_USD` cap (default $1/day) for Claude, per-client rate limits on `/ask` and `/upload`, and `max_containers` on both Modal functions to cap compute.
- **Data:** documents are sent to the Claude API to answer questions and deleted from the app `DOC_TTL_HOURS` (24) after upload. The UI tells visitors not to upload confidential documents.
- **Dependencies:** versions are pinned in `requirements*.txt`, and GitHub Dependabot alerts are on.

## Limitations and next steps

- Scanned PDFs have no text layer and are rejected. Adding OCR, or sending the PDF to Claude as a `document` block, would cover them.
- Per-client rate limits are kept per container, so they loosen if Modal scales out. The daily budget is shared across containers and is the hard cap. Requests already running when the budget is hit can overshoot it by a few cents.
- Uploads are deleted 24 hours after upload, but they aren't encrypted by the app and there's no login. Anyone with a `doc_id` can ask about that document. Add authentication before handling sensitive data.
- Next: streaming answers, multi-document questions, and a larger evaluation set built on long real-world documents.
