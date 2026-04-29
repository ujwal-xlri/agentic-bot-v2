import os
import pathlib
import streamlit as st
from datetime import datetime
import defaults

COLLECTION_NAME = os.getenv("COLLECTION_NAME", defaults.COLLECTION_NAME)
from log_config import setup_logger
from modules.query import query
from modules.ingestion import ingest, ingest_folder
from modules.export import FailedFileRecord, build_failed_records, generate_failed_files_excel

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

    /* Source reference buttons (below bot messages) */
    .src-label {
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 0.08em;
        color: #5a5f7a;
        text-transform: uppercase;
        border-top: 1px solid #2a2d3a;
        padding-top: 6px;
        margin-bottom: 2px;
    }
</style>
""", unsafe_allow_html=True)


# ── Eager loading (cached for entire session) ─────────────────────────────────
@st.cache_resource(show_spinner="Connecting to ChromaDB...")
def load_chroma_client():
    import chromadb
    host = os.getenv("CHROMA_HOST", defaults.CHROMA_HOST)
    port = int(os.getenv("CHROMA_PORT", defaults.CHROMA_PORT))
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
        host = os.getenv("OLLAMA_HOST", defaults.OLLAMA_HOST)
        port = os.getenv("OLLAMA_PORT", defaults.OLLAMA_PORT)
        urllib.request.urlopen(f"http://{host}:{port}/api/tags", timeout=2)
        return True
    except Exception:
        return False

def get_collection_count():
    try:
        col = chroma_client.get_or_create_collection(COLLECTION_NAME)
        return col.count()
    except Exception:
        return 0


def get_all_pdfs() -> dict:
    """
    Scan PDF_DIR and return {folder_name: [list of pdf path strings]}.
    """
    pdf_dir = pathlib.Path(os.getenv("PDF_DIR", defaults.PDF_DIR))
    folders = {}
    for pdf in sorted(pdf_dir.rglob("*.pdf")):
        folder = pdf.parent.name
        folders.setdefault(folder, []).append(str(pdf))
    return folders


@st.cache_data(show_spinner=False)
def render_pdf_page(pdf_path: str, page_number: int) -> bytes:
    import fitz
    doc = fitz.open(pdf_path)
    pix = doc[page_number - 1].get_pixmap(matrix=fitz.Matrix(1.8, 1.8))
    data = pix.tobytes("png")
    doc.close()
    return data


@st.cache_data(show_spinner=False)
def get_pdf_page_count(pdf_path: str) -> int:
    import fitz
    doc = fitz.open(pdf_path)
    n = doc.page_count
    doc.close()
    return n


# ── Session state init ────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "page" not in st.session_state:
    st.session_state["page"] = "chat"
if "last_ingested" not in st.session_state:
    st.session_state["last_ingested"] = "Never"
if "failed_upload_excel" not in st.session_state:
    st.session_state["failed_upload_excel"] = None
if "failed_bulk_excel" not in st.session_state:
    st.session_state["failed_bulk_excel"] = None
if "task_in_progress" not in st.session_state:
    st.session_state["task_in_progress"] = False
if "ingest_upload_triggered" not in st.session_state:
    st.session_state["ingest_upload_triggered"] = False
if "bulk_ingest_triggered" not in st.session_state:
    st.session_state["bulk_ingest_triggered"] = False
if "pending_upload_files" not in st.session_state:
    st.session_state["pending_upload_files"] = []
if "upload_ingest_results" not in st.session_state:
    st.session_state["upload_ingest_results"] = []
if "upload_ingest_total" not in st.session_state:
    st.session_state["upload_ingest_total"] = 0
if "show_upload_balloons" not in st.session_state:
    st.session_state["show_upload_balloons"] = False
if "bulk_ingest_summary" not in st.session_state:
    st.session_state["bulk_ingest_summary"] = None
if "pdf_viewer_open" not in st.session_state:
    st.session_state["pdf_viewer_open"] = False
if "pdf_viewer_path" not in st.session_state:
    st.session_state["pdf_viewer_path"] = ""
if "pdf_viewer_page" not in st.session_state:
    st.session_state["pdf_viewer_page"] = 1
if "pdf_viewer_ref_page" not in st.session_state:
    st.session_state["pdf_viewer_ref_page"] = 1
if "pdf_viewer_total" not in st.session_state:
    st.session_state["pdf_viewer_total"] = 1
if "pdf_viewer_filename" not in st.session_state:
    st.session_state["pdf_viewer_filename"] = ""


# ── Task lock ─────────────────────────────────────────────────────────────────
task_running = st.session_state["task_in_progress"]

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚡ TGTRANSCO Bot")
    st.markdown("---")

    # Service status
    st.markdown('<div class="section-label">System Status</div>', unsafe_allow_html=True)

    chroma_ok = check_chromadb()
    ollama_ok = check_ollama()
    model_name = os.getenv("OLLAMA_MODEL", defaults.OLLAMA_MODEL)
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
    if task_running:
        st.warning("⏳ Task in progress — navigation locked")

    if st.button("💬  Chat", use_container_width=True, disabled=task_running,
                 type="primary" if st.session_state["page"] == "chat" else "secondary"):
        st.session_state["page"] = "chat"
        st.rerun()

    if st.button("📁  Documents", use_container_width=True, disabled=task_running,
                 type="primary" if st.session_state["page"] == "docs" else "secondary"):
        st.session_state["page"] = "docs"
        st.rerun()

    if st.button("⬆️  Upload & Ingest", use_container_width=True, disabled=task_running,
                 type="primary" if st.session_state["page"] == "upload" else "secondary"):
        st.session_state["page"] = "upload"
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PDF VIEWER MODAL
# ══════════════════════════════════════════════════════════════════════════════
@st.dialog("📄 Document Viewer", width="large")
def _pdf_viewer_modal():
    fp     = st.session_state["pdf_viewer_path"]
    cur_pg = st.session_state["pdf_viewer_page"]
    total  = st.session_state["pdf_viewer_total"]
    ref_pg = st.session_state["pdf_viewer_ref_page"]
    fname  = st.session_state["pdf_viewer_filename"]

    st.caption(f"**{fname}** · Page {cur_pg} of {total}")

    c1, c2, c3, _ = st.columns([1, 1, 1, 4])
    with c1:
        if st.button("← Prev", key="pdf_prev",
                     disabled=(cur_pg <= 1), use_container_width=True):
            st.session_state["pdf_viewer_page"] -= 1
            st.rerun()
    with c2:
        if st.button("Next →", key="pdf_next",
                     disabled=(cur_pg >= total), use_container_width=True):
            st.session_state["pdf_viewer_page"] += 1
            st.rerun()
    with c3:
        if st.button(f"↩ p.{ref_pg}", key="pdf_ref",
                     disabled=(cur_pg == ref_pg), use_container_width=True,
                     help=f"Jump to referenced page {ref_pg}"):
            st.session_state["pdf_viewer_page"] = ref_pg
            st.rerun()

    try:
        img_bytes = render_pdf_page(fp, cur_pg)
        st.image(img_bytes, use_container_width=True)
    except Exception as e:
        st.error(f"Cannot render page: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: CHAT
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state["page"] == "chat":

    st.markdown("### Chat")

    if st.session_state["pdf_viewer_open"] and st.session_state["pdf_viewer_path"]:
        _pdf_viewer_modal()

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
        for mi, msg in enumerate(st.session_state["messages"]):
            if msg["role"] == "user":
                st.markdown(f'<div class="user-msg">{msg["content"]}</div>',
                            unsafe_allow_html=True)
            else:
                elapsed_html = ""
                if msg.get("elapsed"):
                    elapsed_html = f'<div style="font-size:11px;color:#5a5f7a;margin-top:8px;">⏱ {msg["elapsed"]}s</div>'

                st.markdown(
                    f'<div class="bot-msg">{msg["content"]}{elapsed_html}</div>',
                    unsafe_allow_html=True
                )

                if msg.get("sources"):
                    st.markdown('<div class="src-label">Sources — click to open</div>',
                                unsafe_allow_html=True)
                    src_cols = st.columns(min(len(msg["sources"]), 4))
                    for si, src in enumerate(msg["sources"]):
                        fname = src["filename"]
                        pg    = src["page_number"]
                        short = (fname[:22] + "…") if len(fname) > 22 else fname
                        with src_cols[si % len(src_cols)]:
                            if st.button(
                                f"📄 {short}  p.{pg}",
                                key=f"src_{mi}_{si}",
                                use_container_width=True,
                                help=f"{fname} — page {pg}",
                            ):
                                fp = src.get("full_path", "")
                                if fp and pathlib.Path(fp).exists():
                                    pg_int = int(pg) if str(pg).isdigit() else 1
                                    st.session_state.update({
                                        "pdf_viewer_open":     True,
                                        "pdf_viewer_path":     fp,
                                        "pdf_viewer_filename": fname,
                                        "pdf_viewer_page":     pg_int,
                                        "pdf_viewer_ref_page": pg_int,
                                        "pdf_viewer_total":    get_pdf_page_count(fp),
                                    })
                                else:
                                    st.toast(f"File not found: {fname}", icon="⚠️")
                                st.rerun()

    # Chat input
    if chunk_count == 0:
        st.info("No documents have been indexed yet. Go to **Upload & Ingest** to add documents before chatting.")

    if question := st.chat_input("Ask a question about your documents...", disabled=(chunk_count == 0)):
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

    upload_dir = pathlib.Path(os.getenv("PDF_DIR", defaults.PDF_DIR)) / "Uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # ── Phase 2: execute triggered upload ingest ──────────────────────────────
    if st.session_state["ingest_upload_triggered"]:
        pending = st.session_state["pending_upload_files"]
        results = []
        total_chunks = 0
        upload_failures: list = []
        for file_data in pending:
            fname = file_data["name"]
            fbytes = file_data["bytes"]
            size_mb = round(len(fbytes) / 1024 / 1024, 2)
            logger.info(f"UPLOAD | file={fname!r} | size_mb={size_mb}")
            save_path = upload_dir / fname
            with open(save_path, "wb") as out:
                out.write(fbytes)
            try:
                with st.spinner(f"Ingesting {fname}..."):
                    added, replaced = ingest(str(save_path))
                if added == 0:
                    upload_failures.append(FailedFileRecord(
                        fname, "No text could be extracted (image-only or empty document)"
                    ))
                    results.append({"name": fname, "status": "empty"})
                else:
                    st.session_state["last_ingested"] = datetime.now().strftime("%d %b %Y, %H:%M")
                    results.append({"name": fname, "status": "success",
                                    "added": added, "replaced": replaced})
                    total_chunks += added
            except Exception as e:
                logger.error(f"INGEST_ERROR | file={fname!r} | error={e}")
                upload_failures.append(FailedFileRecord(fname, str(e)))
                results.append({"name": fname, "status": "error", "message": str(e)})

        st.session_state["upload_ingest_results"] = results
        st.session_state["upload_ingest_total"] = total_chunks
        st.session_state["show_upload_balloons"] = total_chunks > 0
        st.session_state["failed_upload_excel"] = None
        if upload_failures:
            st.session_state["failed_upload_excel"] = generate_failed_files_excel(upload_failures)
            st.session_state["failed_upload_name"] = (
                f"failed_files_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            )
        st.session_state["ingest_upload_triggered"] = False
        st.session_state["task_in_progress"] = False
        st.session_state["pending_upload_files"] = []
        st.rerun()

    # ── Phase 2: execute triggered bulk ingest ────────────────────────────────
    if st.session_state["bulk_ingest_triggered"]:
        logger.info("BULK_INGEST_START | source=/app/pdfs")
        with st.spinner("Ingesting all PDFs — this may take a while..."):
            summary = ingest_folder()
        total = sum(v["added"] for v in summary.values())
        if total:
            st.session_state["last_ingested"] = datetime.now().strftime("%d %b %Y, %H:%M")
        fail_files = [n for n, v in summary.items() if v["added"] == 0]
        logger.info(f"BULK_INGEST_DONE | files={len(summary)} | total_chunks={total}")
        st.session_state["bulk_ingest_summary"] = summary
        st.session_state["failed_bulk_excel"] = None
        if fail_files:
            records = build_failed_records(summary)
            st.session_state["failed_bulk_excel"] = generate_failed_files_excel(records)
            st.session_state["failed_bulk_name"] = (
                f"failed_files_bulk_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            )
        st.session_state["bulk_ingest_triggered"] = False
        st.session_state["task_in_progress"] = False
        st.rerun()

    # ── Normal render ─────────────────────────────────────────────────────────
    uploaded = st.file_uploader(
        "Drop PDFs here or click to browse",
        type=["pdf"],
        accept_multiple_files=True
    )

    if uploaded:
        if st.button("⚡ Ingest All", type="primary"):
            st.session_state["upload_ingest_results"] = []
            st.session_state["upload_ingest_total"] = 0
            st.session_state["pending_upload_files"] = [
                {"name": f.name, "size": f.size, "bytes": f.read()}
                for f in uploaded
            ]
            st.session_state["ingest_upload_triggered"] = True
            st.session_state["task_in_progress"] = True
            st.rerun()

    # Show results from last upload ingest
    if st.session_state["upload_ingest_results"]:
        total = st.session_state["upload_ingest_total"]
        for r in st.session_state["upload_ingest_results"]:
            if r["status"] == "success":
                replaced_note = f" (replaced {r['replaced']} outdated chunks)" if r.get("replaced") else ""
                st.success(f"✓ **{r['name']}**: {r['added']} chunks ingested{replaced_note}")
            elif r["status"] == "empty":
                st.error(f"✗ **{r['name']}**: No text could be extracted — not indexed")
            elif r["status"] == "error":
                st.error(f"✗ **{r['name']}**: Failed — {r['message']}")
        if total:
            st.success(f"Done — {total} total chunks added to knowledge base.")
        if st.session_state["show_upload_balloons"]:
            st.balloons()
            st.session_state["show_upload_balloons"] = False

    if st.session_state.get("failed_upload_excel"):
        st.warning("One or more uploaded files could not be ingested. Download the report for details.")
        st.download_button(
            label="⬇️ Download Failed Files Report (Excel)",
            data=st.session_state["failed_upload_excel"],
            file_name=st.session_state.get("failed_upload_name", "failed_files.xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_upload_failed",
        )

    st.markdown("---")
    st.markdown('<div class="section-label">Bulk Ingest from Volume</div>',
                unsafe_allow_html=True)
    st.markdown(
        "<small style='color:#5a5f7a'>Ingest all PDFs already present in the mounted "
        "<code>/app/pdfs</code> folder.</small>",
        unsafe_allow_html=True
    )

    if st.button("Ingest All PDFs from /app/pdfs"):
        st.session_state["bulk_ingest_summary"] = None
        st.session_state["bulk_ingest_triggered"] = True
        st.session_state["task_in_progress"] = True
        st.rerun()

    # Show results from last bulk ingest
    if st.session_state["bulk_ingest_summary"] is not None:
        summary = st.session_state["bulk_ingest_summary"]
        total = sum(v["added"] for v in summary.values())
        ok_files = [n for n, v in summary.items() if v["added"] > 0]
        fail_files_list = [n for n, v in summary.items() if v["added"] == 0]
        st.success(f"Done — {len(ok_files)}/{len(summary)} files indexed, {total:,} chunks added.")
        for name in ok_files:
            info = summary[name]
            replaced_note = f" (replaced {info['replaced']} outdated)" if info["replaced"] else ""
            st.markdown(f"- ✓ **{name}**: {info['added']} chunks{replaced_note}")
        for name in fail_files_list:
            err = summary[name].get("error", "no text could be extracted")
            st.markdown(f"- ✗ **{name}**: not indexed — {err}")

    if st.session_state.get("failed_bulk_excel"):
        st.warning("One or more files from the volume could not be ingested. Download the report for details.")
        st.download_button(
            label="⬇️ Download Failed Files Report (Excel)",
            data=st.session_state["failed_bulk_excel"],
            file_name=st.session_state.get("failed_bulk_name", "failed_files_bulk.xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_bulk_failed",
        )

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
            col = chroma_client.get_or_create_collection(COLLECTION_NAME)
            total_before = col.count()
            chroma_client.delete_collection(COLLECTION_NAME)
            chroma_client.create_collection(COLLECTION_NAME)
            logger.warning(f"DB_CLEARED | chunks_removed={total_before}")
            st.success(f"Knowledge base cleared — {total_before:,} chunks removed.")
            st.session_state["last_ingested"] = "Never"
            st.rerun()
        except Exception as e:
            logger.error(f"DB_CLEAR_ERROR | error={e}")
            st.error(f"Failed to clear database: {e}")