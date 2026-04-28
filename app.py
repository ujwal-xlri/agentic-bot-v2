import os
import pathlib
import streamlit as st
from datetime import datetime
from log_config import setup_logger
from modules.query import query
from modules.ingestion import ingest, ingest_folder

logger = setup_logger("app")

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="TGTRANSCO Bot",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* Base */
    [data-testid="stAppViewContainer"] { background: #0f1117; }
    [data-testid="stSidebar"] { background: #1a1d27; border-right: 1px solid #2a2d3a; }
    
    /* Hide default header */
    [data-testid="stHeader"] { background: transparent; }
    
    /* Chat messages */
    .user-msg {
        background: #1e2130;
        border: 1px solid #2a2d3a;
        border-radius: 12px 12px 4px 12px;
        padding: 14px 18px;
        margin: 8px 0;
        color: #e8eaf0;
        font-size: 15px;
        line-height: 1.6;
        max-width: 80%;
        margin-left: auto;
    }
    .bot-msg {
        background: #161922;
        border: 1px solid #2a2d3a;
        border-left: 3px solid #f0a500;
        border-radius: 4px 12px 12px 12px;
        padding: 14px 18px;
        margin: 8px 0;
        color: #e8eaf0;
        font-size: 15px;
        line-height: 1.7;
        max-width: 90%;
    }
    
    /* Source chips */
    .source-row {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-top: 10px;
        padding-top: 10px;
        border-top: 1px solid #2a2d3a;
    }
    .source-chip {
        background: #1e2130;
        border: 1px solid #f0a500;
        border-radius: 20px;
        padding: 3px 10px;
        font-size: 11px;
        color: #f0a500;
        white-space: nowrap;
    }
    .source-chip span {
        color: #8b8fa8;
        margin-left: 4px;
    }
    
    /* Status indicators */
    .status-dot {
        display: inline-block;
        width: 8px; height: 8px;
        border-radius: 50%;
        margin-right: 6px;
    }
    .status-ok   { background: #22c55e; }
    .status-warn { background: #f0a500; }
    .status-err  { background: #ef4444; }
    
    /* Section headers */
    .section-label {
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.1em;
        color: #5a5f7a;
        text-transform: uppercase;
        margin: 16px 0 8px;
    }
    
    /* Document cards */
    .doc-card {
        background: #1a1d27;
        border: 1px solid #2a2d3a;
        border-radius: 8px;
        padding: 10px 14px;
        margin: 4px 0;
        cursor: pointer;
        transition: border-color 0.2s;
    }
    .doc-card:hover { border-color: #f0a500; }
    .doc-name { color: #e8eaf0; font-size: 14px; font-weight: 500; }
    .doc-meta { color: #5a5f7a; font-size: 12px; margin-top: 2px; }
    
    /* Folder header */
    .folder-header {
        font-size: 13px;
        font-weight: 600;
        color: #f0a500;
        margin: 16px 0 6px;
        padding-bottom: 4px;
        border-bottom: 1px solid #2a2d3a;
    }
    
    /* Input styling */
    [data-testid="stChatInput"] textarea {
        background: #1a1d27 !important;
        border: 1px solid #2a2d3a !important;
        color: #e8eaf0 !important;
        border-radius: 8px !important;
    }
    
    /* Spinner */
    .thinking {
        color: #5a5f7a;
        font-size: 13px;
        font-style: italic;
        padding: 8px 0;
    }

    /* Welcome screen */
    .welcome {
        text-align: center;
        padding: 60px 20px;
        color: #5a5f7a;
    }
    .welcome h2 { color: #e8eaf0; font-size: 24px; font-weight: 500; margin-bottom: 8px; }
    .welcome p  { font-size: 15px; line-height: 1.6; }
</style>
""", unsafe_allow_html=True)


# ── Eager loading (cached for entire session) ─────────────────────────────────
@st.cache_resource(show_spinner="Connecting to ChromaDB...")
def load_chroma_client():
    import chromadb
    host = os.getenv("CHROMA_HOST", "localhost")
    port = int(os.getenv("CHROMA_PORT", "8000"))
    return chromadb.HttpClient(host=host, port=port)

# Load at startup
chroma_client = load_chroma_client()
logger.info("APP_START | Streamlit app initialized")


# ── Service status checks ─────────────────────────────────────────────────────
def check_chromadb():
    try:
        chroma_client.heartbeat()
        return True
    except Exception:
        return False

def check_ollama():
    try:
        import urllib.request
        host = os.getenv("OLLAMA_HOST", "localhost")
        port = os.getenv("OLLAMA_PORT", "11434")
        urllib.request.urlopen(f"http://{host}:{port}/api/tags", timeout=2)
        return True
    except Exception:
        return False

def get_collection_count():
    try:
        col = chroma_client.get_or_create_collection("tgtransco")
        return col.count()
    except Exception:
        return 0


def get_all_pdfs() -> dict:
    """
    Scan PDF_DIR and return {folder_name: [list of pdf path strings]}.
    """
    pdf_dir = pathlib.Path(os.getenv("PDF_DIR", "/app/pdfs"))
    folders = {}
    for pdf in sorted(pdf_dir.rglob("*.pdf")):
        folder = pdf.parent.name
        folders.setdefault(folder, []).append(str(pdf))
    return folders


# ── Session state init ────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "page" not in st.session_state:
    st.session_state["page"] = "chat"
if "last_ingested" not in st.session_state:
    st.session_state["last_ingested"] = "Never"


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚡ TGTRANSCO Bot")
    st.markdown("---")

    # Service status
    st.markdown('<div class="section-label">System Status</div>', unsafe_allow_html=True)

    chroma_ok = check_chromadb()
    ollama_ok = check_ollama()
    model_name = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    chunk_count = get_collection_count()

    st.markdown(f"""
    <div style="font-size:13px; line-height:2;">
        <div><span class="status-dot {'status-ok' if chroma_ok else 'status-err'}"></span>
        ChromaDB {'connected' if chroma_ok else 'offline'}</div>
        <div><span class="status-dot {'status-ok' if ollama_ok else 'status-err'}"></span>
        Ollama {'ready' if ollama_ok else 'offline'}</div>
        <div><span class="status-dot status-ok"></span>
        Model: {model_name}</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    # Stats
    st.markdown('<div class="section-label">Knowledge Base</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div style="font-size:13px; color:#8b8fa8; line-height:2;">
        <div>Chunks indexed: <strong style="color:#e8eaf0">{chunk_count:,}</strong></div>
        <div>Last ingested: <strong style="color:#e8eaf0">{st.session_state['last_ingested']}</strong></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    # Navigation
    if st.button("💬  Chat", use_container_width=True,
                 type="primary" if st.session_state["page"] == "chat" else "secondary"):
        st.session_state["page"] = "chat"
        st.rerun()

    if st.button("📁  Documents", use_container_width=True,
                 type="primary" if st.session_state["page"] == "docs" else "secondary"):
        st.session_state["page"] = "docs"
        st.rerun()

    if st.button("⬆️  Upload & Ingest", use_container_width=True,
                 type="primary" if st.session_state["page"] == "upload" else "secondary"):
        st.session_state["page"] = "upload"
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: CHAT
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state["page"] == "chat":

    st.markdown("### Chat")

    # Render history
    if not st.session_state["messages"]:
        st.markdown("""
        <div class="welcome">
            <h2>Ask anything about your documents</h2>
            <p>Your questions are answered using the ingested PDF library.<br>
            Sources are shown below each answer.</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        for msg in st.session_state["messages"]:
            if msg["role"] == "user":
                st.markdown(f'<div class="user-msg">{msg["content"]}</div>',
                            unsafe_allow_html=True)
            else:
                sources_html = ""
                if msg.get("sources"):
                    chips = "".join([
                        f'<div class="source-chip">{s["filename"]}'
                        f'<span>p.{s["page_number"]}</span></div>'
                        for s in msg["sources"]
                    ])
                    sources_html = f'<div class="source-row">{chips}</div>'

                elapsed_html = ""
                if msg.get("elapsed"):
                    elapsed_html = f'<div style="font-size:11px;color:#5a5f7a;margin-top:8px;">⏱ {msg["elapsed"]}s</div>'

                st.markdown(
                    f'<div class="bot-msg">{msg["content"]}{sources_html}{elapsed_html}</div>',
                    unsafe_allow_html=True
                )

    # Chat input
    if question := st.chat_input("Ask a question about your documents..."):
        # Add user message
        st.session_state["messages"].append({"role": "user", "content": question})

        # Run query
        with st.spinner("Thinking..."):
            try:
                result = query(question)
                st.session_state["messages"].append({
                    "role":    "assistant",
                    "content": result["answer"],
                    "sources": result["sources"],
                    "elapsed": result["elapsed"],
                })
            except Exception as e:
                logger.error(f"QUERY_ERROR | q={question!r} | error={e}")
                st.session_state["messages"].append({
                    "role":    "assistant",
                    "content": f"Error: {str(e)}",
                    "sources": [],
                    "elapsed": None,
                })

        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DOCUMENTS
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state["page"] == "docs":

    st.markdown("### Documents")

    search = st.text_input("🔍  Search documents...", placeholder="Type to filter...")

    all_pdfs = get_all_pdfs()

    if not all_pdfs:
        st.info("No PDFs found. Upload documents using the Upload & Ingest page.")
    else:
        for folder, paths in sorted(all_pdfs.items()):
            # Filter
            filtered = [p for p in paths
                        if not search or search.lower() in pathlib.Path(p).name.lower()]
            if not filtered:
                continue

            st.markdown(f'<div class="folder-header">📂 {folder} ({len(filtered)})</div>',
                        unsafe_allow_html=True)

            for pdf_path in filtered:
                pdf   = pathlib.Path(pdf_path)
                fsize = round(pdf.stat().st_size / 1024 / 1024, 1) if pdf.exists() else "?"
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.markdown(f"""
                    <div class="doc-card">
                        <div class="doc-name">📄 {pdf.name}</div>
                        <div class="doc-meta">{fsize} MB · {pdf_path}</div>
                    </div>
                    """, unsafe_allow_html=True)
                with col2:
                    if st.button("Open", key=f"open_{pdf_path}"):
                        try:
                            os.startfile(pdf_path)
                        except Exception:
                            st.warning("Cannot open file from inside container. "
                                       "Access it directly from your host machine.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: UPLOAD & INGEST
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state["page"] == "upload":

    st.markdown("### Upload & Ingest")

    upload_dir = pathlib.Path(os.getenv("PDF_DIR", "/app/pdfs")) / "Uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    uploaded = st.file_uploader(
        "Drop PDFs here or click to browse",
        type=["pdf"],
        accept_multiple_files=True
    )

    if uploaded:
        if st.button("⚡ Ingest All", type="primary"):
            total_chunks = 0
            for f in uploaded:
                save_path = upload_dir / f.name
                size_mb   = round(f.size / 1024 / 1024, 2)
                logger.info(f"UPLOAD | file={f.name!r} | size_mb={size_mb}")
                with open(save_path, "wb") as out:
                    out.write(f.read())

                st.markdown(f"**{f.name}**")
                try:
                    with st.spinner(f"Ingesting {f.name}..."):
                        added, replaced = ingest(str(save_path))
                    st.session_state["last_ingested"] = datetime.now().strftime("%d %b %Y, %H:%M")
                    if replaced:
                        st.success(f"✓ {added} chunks ingested (replaced {replaced} outdated chunks)")
                    else:
                        st.success(f"✓ {added} chunks ingested")
                    total_chunks += added
                except Exception as e:
                    logger.error(f"INGEST_ERROR | file={f.name!r} | error={e}")
                    st.error(f"✗ Failed: {e}")

            if total_chunks:
                st.balloons()
                st.success(f"Done — {total_chunks} total chunks added to knowledge base.")

    st.markdown("---")
    st.markdown('<div class="section-label">Bulk Ingest from Volume</div>',
                unsafe_allow_html=True)
    st.markdown(
        "<small style='color:#5a5f7a'>Ingest all PDFs already present in the mounted "
        "<code>/app/pdfs</code> folder.</small>",
        unsafe_allow_html=True
    )

    if st.button("Ingest All PDFs from /app/pdfs"):
        logger.info("BULK_INGEST_START | source=/app/pdfs")
        with st.spinner("Ingesting all PDFs — this may take a while..."):
            summary = ingest_folder()
        total = sum(v["added"] for v in summary.values())
        logger.info(f"BULK_INGEST_DONE | files={len(summary)} | total_chunks={total}")
        st.success(f"Done — {len(summary)} files processed.")
        for name, info in summary.items():
            replaced_note = f", replaced {info['replaced']}" if info["replaced"] else ""
            st.markdown(f"- **{name}**: {info['added']} chunks{replaced_note}")
        st.session_state["last_ingested"] = datetime.now().strftime("%d %b %Y, %H:%M")

    st.markdown("---")
    st.markdown('<div class="section-label">Danger Zone</div>', unsafe_allow_html=True)
    st.markdown(
        "<small style='color:#5a5f7a'>Permanently deletes all indexed chunks from the "
        "knowledge base. PDFs on disk are not affected.</small>",
        unsafe_allow_html=True
    )

    confirm = st.checkbox("I understand this will erase all indexed data")
    if st.button("🗑️ Clear Knowledge Base", type="secondary", disabled=not confirm):
        try:
            col = chroma_client.get_or_create_collection("tgtransco")
            total_before = col.count()
            chroma_client.delete_collection("tgtransco")
            chroma_client.create_collection("tgtransco")
            logger.warning(f"DB_CLEARED | chunks_removed={total_before}")
            st.success(f"Knowledge base cleared — {total_before:,} chunks removed.")
            st.session_state["last_ingested"] = "Never"
            st.rerun()
        except Exception as e:
            logger.error(f"DB_CLEAR_ERROR | error={e}")
            st.error(f"Failed to clear database: {e}")