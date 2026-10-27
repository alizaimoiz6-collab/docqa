"""Streamlit UI for DocQA. Talks to the FastAPI backend at API_URL."""

import os

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
CONFIDENCE_COLOR = {"high": "green", "medium": "orange", "low": "red"}

st.set_page_config(page_title="DocQA", page_icon="📄", layout="centered")
st.title("📄 DocQA")
st.caption("Ask questions about a PDF or text document. Every answer cites the passages it relies on.")


def api_error(resp: requests.Response) -> str:
    try:
        body = resp.json()
        msg = body.get("detail") or body
    except ValueError:
        msg = resp.text[:300]
    if resp.status_code == 429 and resp.headers.get("Retry-After"):
        msg = f"{msg} (retry in {resp.headers['Retry-After']}s)"
    return f"{resp.status_code}: {msg}"


# ---- Upload -----------------------------------------------------------------

uploaded = st.file_uploader("Document", type=["pdf", "txt", "md"])
if uploaded and st.session_state.get("uploaded_name") != uploaded.name + str(uploaded.size):
    with st.spinner("Reading document..."):
        try:
            resp = requests.post(
                f"{API_URL}/upload",
                files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream")},
                timeout=120,
            )
        except requests.RequestException as exc:
            st.error(f"Could not reach the API at {API_URL}: {exc}")
            st.stop()
    if resp.ok:
        st.session_state.doc = resp.json()
        st.session_state.uploaded_name = uploaded.name + str(uploaded.size)
        st.session_state.history = []
    else:
        st.error(api_error(resp))
        st.stop()

doc = st.session_state.get("doc")
if not doc:
    st.info("Upload a document to begin.")
    st.stop()

pages = f"{doc['pages']} pages · " if doc.get("pages") else ""
st.success(f"**{doc['filename']}** — {pages}{doc['chunks']} passages · {doc['characters']:,} characters")

# ---- Ask --------------------------------------------------------------------

with st.form("ask", clear_on_submit=False):
    question = st.text_input("Question", placeholder="What does the document say about ...?")
    submitted = st.form_submit_button("Ask", type="primary")

if submitted and question.strip():
    with st.spinner("Thinking..."):
        try:
            resp = requests.post(
                f"{API_URL}/ask",
                json={"doc_id": doc["doc_id"], "question": question.strip()},
                timeout=180,
            )
        except requests.RequestException as exc:
            st.error(f"Could not reach the API: {exc}")
            st.stop()
    if resp.ok:
        st.session_state.history.insert(0, resp.json())
    else:
        st.error(api_error(resp))

for i, ans in enumerate(st.session_state.get("history", [])):
    st.divider()
    st.markdown(f"**Q:** {ans['question']}")
    color = CONFIDENCE_COLOR[ans["confidence"]]
    badge = f":{color}-badge[{ans['confidence']} confidence]"
    if not ans["answerable"]:
        badge += " :gray-badge[not found in document]"
    st.markdown(badge)
    st.markdown(ans["answer"])

    if ans["citations"]:
        verified = sum(c["verified"] for c in ans["citations"])
        with st.expander(f"Citations ({verified}/{len(ans['citations'])} verified)", expanded=i == 0):
            for c in ans["citations"]:
                where = f" · page {c['page']}" if c["page"] else ""
                mark = "✅ verified" if c["verified"] else "⚠️ not found verbatim"
                st.markdown(f"**[{c['chunk_id']}]{where}** {mark}\n\n> {c['quote']}")

    u = ans["usage"]
    st.caption(
        f"{ans['model']} · {ans['latency_ms'] / 1000:.1f}s · "
        f"{u['input_tokens']:,} in / {u['output_tokens']:,} out tokens"
        + (f" · {u['cache_read_input_tokens']:,} cached" if u["cache_read_input_tokens"] else "")
        + f" · {ans['retrieval'].replace('_', ' ')}"
    )
