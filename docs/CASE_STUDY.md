# Case Study: DocQA, document answers you can audit

**[Moiz Alizai](https://www.linkedin.com/in/moizalizai/)** · One-page PDF: [DocQA_Case_Study_Moiz_Alizai.pdf](DocQA_Case_Study_Moiz_Alizai.pdf)

**Role:** Solo build: design, backend, LLM integration, frontend, deployment · **Stack:** Python, FastAPI, Claude API (Opus 5.5), Pydantic, Streamlit, Modal · **Links:** [Live demo](https://alizaimoiz6--docqa-ui.us-east.modal.run) · [GitHub](https://github.com/alizaimoiz6-collab/docqa)

---

### The problem
Teams that work from long documents, such as insurance policies, claims manuals and contracts, spend hours finding one clause, and generic AI chatbots give fluent answers nobody can check. In regulated work, an answer without a source can't be used.

### The solution
A web service that takes a PDF or text document and a question, and returns a **structured answer in which every claim cites a page and quotes the source text**. The server then **verifies each quote against the document** before showing it, so a fabricated citation shows up as unverified instead of reaching the user.

### How it works
1. **Upload:** the document is parsed and split into numbered passages that keep their page numbers.
2. **Ask:** the whole document (up to about 100k tokens), or the most relevant passages by BM25 ranking, goes to Claude with the question.
3. **Structured output:** Claude must return a schema-validated Pydantic object: `answer`, `confidence`, `answerable`, and a list of `{passage, quote}` citations.
4. **Verify:** each quote is matched against the passage it cites and labelled ✅ verified or ⚠️ unverified.

### Key decisions
| Decision | Why it matters |
|---|---|
| Structured outputs (JSON schema from Pydantic) | Every response parses into a type. No brittle text parsing in production |
| Server-side quote verification | Turns "trust the model" into a check that can be measured and audited |
| Long context first, retrieval only for very large files | Fewer missed answers, and no vector database to run or pay for |
| Prompt caching on the document | Follow-up questions on the same file cost less and return faster |
| Explicit "not in document" answers | The model can say the answer isn't there instead of guessing |
| Two-layer error handling (SDK retries, then clear HTTP codes; per-client rate limit) | Stays up under API rate limits and timeouts, and protects the API key on a public URL |

### Results
Measured on the live deployment with an 18-question test set over three sample documents (insurance policy PDF, HR policy, claims-handling manual). The run is reproducible with [`eval/run_eval.py`](../eval/run_eval.py).

| Metric | Value |
|---|---|
| Answerable questions answered correctly | **14 / 14** |
| Questions outside the document correctly declined | **4 / 4** (no invented answers) |
| Citations verified verbatim against the source | **31 / 31 (100%)** |
| End-to-end latency, median / p90 | **3.5 s / 4.1 s** |
| Average cost per question | **$0.005** ($0.012 for the first question on a document, $0.004 for follow-ups) |
| Savings from prompt caching | **55%** lower cost than without caching |
| Automated tests | 26 passing (offline, Claude stubbed) |
| Deployment | Public on Modal, scales to zero when idle |

*Small test set on short documents; longer documents will take longer and cost more per first question.*

### Business value
- **Trust:** reviewers check the cited quote instead of re-reading the document.
- **Speed:** minutes of searching become seconds per question.
- **Low running cost:** serverless hosting and no retrieval infrastructure, so you pay only when it's used.

### What's next
OCR for scanned PDFs, multi-document questions, streaming answers, and a larger evaluation set on long real-world documents, tracked over time.
