"""
streamlit_app.py
Meridian — end-user chat interface. Ask a question, get an answer. Nothing else.

No document counts, no entity browser, no knowledge graph — those are internal
tooling now living in admin_app.py. This file is the actual product surface.

Run:
    streamlit run streamlit_app.py
"""

import os
import sqlite3
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from vector_embedding.src.generate_answer import (
    retrieve_entity_context,
    retrieve_graph_context,
    retrieve_vector_chunks,
)

DB_PATH = str(Path(__file__).resolve().parent.parent / "entity_extraction" / "output" / "meridian.db")

st.set_page_config(page_title="Meridian", page_icon="🧪", layout="centered")

# Hide every trace of Streamlit chrome — sidebar, menu, footer, header —
# so this reads as a standalone product, not a dev tool.
st.markdown("""
<style>
    #MainMenu, header, footer, [data-testid="stSidebar"], [data-testid="collapsedControl"] {
        visibility: hidden;
        display: none;
    }
    .block-container {
        padding-top: 3rem;
        max-width: 720px;
    }
    .meridian-title {
        font-size: 2rem;
        font-weight: 700;
        margin-bottom: 0.1rem;
    }
    .meridian-subtitle {
        color: #8a8a8a;
        font-size: 0.95rem;
        margin-bottom: 2rem;
    }
    .meridian-sources {
        font-size: 0.8rem;
        color: #8a8a8a;
        margin-top: 0.3rem;
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Retrieval + answer generation
# The UI stays the same, but the context now comes from the shared end-to-end
# pipeline: vector chunks + entity store + graph relationships.
# ---------------------------------------------------------------------------


def retrieve_context(question: str, db_path: str, top_k: int = 12) -> list[dict]:
    vector_chunks = retrieve_vector_chunks(question, top_k=max(3, top_k // 2))
    entity_rows = retrieve_entity_context(question, top_k=max(3, top_k // 2))
    graph_rows = retrieve_graph_context(question, top_k=max(2, top_k // 3))

    context_rows = []

    for chunk in vector_chunks:
        context_rows.append({
            "document_id": chunk["document_id"],
            "page": chunk["page"],
            "entity_type": "VECTOR_CHUNK",
            "entity_name": chunk["chunk_id"],
            "entity_code": None,
            "context_snippet": chunk["text"],
        })

    for row in entity_rows:
        context_rows.append({
            "document_id": row["document_id"],
            "page": row["page"],
            "entity_type": row["entity_type"],
            "entity_name": row["entity_name"],
            "entity_code": row.get("entity_code"),
            "context_snippet": row.get("context_snippet", ""),
        })

    for row in graph_rows:
        context_rows.append({
            "document_id": row["document_id"],
            "page": None,
            "entity_type": "GRAPH_RELATION",
            "entity_name": f"{row['source_name']} -> {row['target_name']}",
            "entity_code": None,
            "context_snippet": (
                f"{row['source_name']} ({row['source_type']}) "
                f"{row['relationship_type']} {row['target_name']} ({row['target_type']})"
            ),
        })

    return context_rows[:top_k]


def generate_answer(question: str, context_rows: list[dict]) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "Something's not configured correctly on our end — please contact support."

    if not context_rows:
        return "I couldn't find anything related to that in the available records."

    context_text = "\n".join(
        f"- [{r['document_id']} / page {r['page'] or 'N/A'}] "
        f"{r['entity_type']}: {r['entity_name']}"
        + (f" ({r['entity_code']})" if r.get("entity_code") else "")
        + (f" — context: {r['context_snippet']}" if r.get("context_snippet") else "")
        for r in context_rows
    )

    client = genai.Client(api_key=api_key)
    system_prompt = (
        "You answer questions about pharmaceutical manufacturing records using ONLY "
        "the evidence provided below. If the context doesn't contain the answer, say so "
        "directly — never guess or use outside knowledge. Answer in plain, direct prose."
    )
    resp = client.models.generate_content(
        model="gemini-flash-latest",
        contents=f"Context:\n{context_text}\n\nQuestion: {question}",
        config=types.GenerateContentConfig(system_instruction=system_prompt, temperature=0.2),
    )
    return resp.text


def format_sources(context_rows: list[dict]) -> str:
    """A minimal, non-technical source line — document + page only, no entity
    types, no chunk IDs, no counts. Just enough for audit trust in a pharma
    context without looking like a debug panel."""
    seen = []
    for r in context_rows:
        label = f"{r['document_id']} (p.{r['page']})" if r.get("page") else r["document_id"]
        if label not in seen:
            seen.append(label)
    return ", ".join(seen[:5])


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.markdown('<div class="meridian-title">🧪 Meridian</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="meridian-subtitle">Ask anything about your manufacturing records.</div>',
    unsafe_allow_html=True,
)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if not Path(DB_PATH).exists():
    st.info("Meridian is still getting set up — check back shortly.")
    st.stop()

for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"], avatar="🧪" if msg["role"] == "assistant" else None):
        st.markdown(msg["content"])
        if msg.get("sources_label"):
            st.markdown(
                f'<div class="meridian-sources">Sources: {msg["sources_label"]}</div>',
                unsafe_allow_html=True,
            )

question = st.chat_input("Message Meridian...")
if question:
    st.session_state.chat_history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="🧪"):
        with st.spinner("Thinking..."):
            context_rows = retrieve_context(question, DB_PATH)
            answer = generate_answer(question, context_rows)
        st.markdown(answer)
        sources_label = format_sources(context_rows) if context_rows else None
        if sources_label:
            st.markdown(
                f'<div class="meridian-sources">Sources: {sources_label}</div>',
                unsafe_allow_html=True,
            )

    st.session_state.chat_history.append(
        {"role": "assistant", "content": answer, "sources_label": sources_label}
    )