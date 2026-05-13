import os
import html
import time
import pathlib
import streamlit as st
from datetime import datetime
import defaults

COLLECTION_NAME   = os.getenv("COLLECTION_NAME", defaults.COLLECTION_NAME)
ENABLE_EVALUATION = os.getenv("ENABLE_EVALUATION", "false").lower() == "true"
from log_config import setup_logger
from modules.query import query_stream
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
    /* ── Base ──────────────────────────────────────────────────────── */
    [data-testid="stAppViewContainer"] { background: #ffffff; }
    [data-testid="stSidebar"] { background: #5c6bc0; border-right: 1px solid #4a5ab0; }
    [data-testid="stHeader"] { background: transparent; }

    /* ── Sidebar text ───────────────────────────────────────────────── */
    [data-testid="stSidebar"],
    [data-testid="stSidebar"] p, [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] div, [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3 { color: #ffffff; }
    [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.25); }
    [data-testid="stSidebar"] .section-label { color: rgba(255,255,255,0.65); }

    /* ── Sidebar buttons ────────────────────────────────────────────── */
    [data-testid="stSidebar"] button {
        background-color: rgba(255,255,255,0.12) !important;
        color: #ffffff !important;
        border: 1px solid rgba(255,255,255,0.35) !important;
    }
    [data-testid="stSidebar"] button:hover {
        background-color: rgba(255,255,255,0.22) !important;
        border-color: rgba(255,255,255,0.6) !important;
    }
    [data-testid="stSidebar"] [data-testid="baseButton-primary"] {
        background-color: rgba(255,255,255,0.28) !important;
        border-color: rgba(255,255,255,0.65) !important;
        font-weight: 600 !important;
    }

    /* ── Chat messages ──────────────────────────────────────────────── */
    .user-msg {
        background: #eef1fb; border: 1px solid #c5cae9;
        border-radius: 12px 12px 4px 12px;
        padding: 14px 18px; margin: 8px 0;
        color: #1a1b2e; font-size: 15px; line-height: 1.6;
        max-width: 80%; margin-left: auto;
    }
    .bot-msg {
        background: #f5f6ff; border: 1px solid #c5cae9;
        border-left: 3px solid #5c6bc0;
        border-radius: 4px 12px 12px 12px;
        padding: 14px 18px; margin: 8px 0;
        color: #1a1b2e; font-size: 15px; line-height: 1.7; max-width: 90%;
    }

    /* ── Source chips ───────────────────────────────────────────────── */
    .source-row {
        display: flex; flex-wrap: wrap; gap: 6px;
        margin-top: 10px; padding-top: 10px; border-top: 1px solid #c5cae9;
    }
    .source-chip {
        background: #eef1fb; border: 1px solid #5c6bc0;
        border-radius: 20px; padding: 3px 10px;
        font-size: 11px; color: #5c6bc0; white-space: nowrap;
    }
    .source-chip span { color: #757575; margin-left: 4px; }

    /* ── Status indicators ──────────────────────────────────────────── */
    .status-dot {
        display: inline-block; width: 8px; height: 8px;
        border-radius: 50%; margin-right: 6px;
    }
    .status-ok   { background: #62bc62; }
    .status-warn { background: #c67605; }
    .status-err  { background: #ef4444; }

    /* ── Section headers ────────────────────────────────────────────── */
    .section-label {
        font-size: 11px; font-weight: 600; letter-spacing: 0.1em;
        color: #9e9ea8; text-transform: uppercase; margin: 16px 0 8px;
    }

    /* ── Document / delete cards ────────────────────────────────────── */
    .doc-card {
        background: #f5f6ff; border: 1px solid #c5cae9;
        border-radius: 8px; padding: 10px 14px; margin: 4px 0;
        cursor: pointer; transition: border-color 0.2s;
    }
    .doc-card:hover { border-color: #5c6bc0; }
    .doc-name { color: #1a1b2e; font-size: 14px; font-weight: 500; }
    .doc-meta { color: #757575; font-size: 12px; margin-top: 2px; }

    /* ── Folder header ──────────────────────────────────────────────── */
    .folder-header {
        font-size: 13px; font-weight: 600; color: #5c6bc0;
        margin: 16px 0 6px; padding-bottom: 4px; border-bottom: 1px solid #c5cae9;
    }

    /* ── Chat input ─────────────────────────────────────────────────── */
    [data-testid="stChatInput"] textarea {
        background: #f5f6ff !important; border: 1px solid #c5cae9 !important;
        color: #1a1b2e !important; border-radius: 8px !important;
    }

    /* ── Misc ───────────────────────────────────────────────────────── */
    .thinking { color: #9e9ea8; font-size: 13px; font-style: italic; padding: 8px 0; }
    .welcome { text-align: center; padding: 60px 20px; color: #9e9ea8; }
    .welcome h2 { color: #1a1b2e; font-size: 24px; font-weight: 500; margin-bottom: 8px; }
    .welcome p  { font-size: 15px; line-height: 1.6; }
    .src-label {
        font-size: 10px; font-weight: 600; letter-spacing: 0.08em;
        color: #9e9ea8; text-transform: uppercase;
        border-top: 1px solid #c5cae9; padding-top: 6px; margin-bottom: 2px;
    }

    /* ── Home mode cards ────────────────────────────────────────────── */
    .mode-card {
        background: #f5f6ff; border: 1px solid #c5cae9;
        border-radius: 12px; padding: 48px 32px; text-align: center;
        margin-bottom: 16px; transition: border-color 0.2s;
    }
    .mode-card:hover { border-color: #5c6bc0; }
    .mode-icon  { font-size: 52px; margin-bottom: 16px; }
    .mode-title { font-size: 22px; font-weight: 600; color: #1a1b2e; margin-bottom: 10px; }
    .mode-desc  { font-size: 14px; color: #616161; line-height: 1.7; }

    /* ── Chunk cards ────────────────────────────────────────────────── */
    .chunk-card {
        background: #f5f6ff; border: 1px solid #c5cae9;
        border-radius: 8px; padding: 12px 14px; height: 130px;
        overflow: hidden; cursor: pointer; transition: border-color 0.2s;
        display: flex; flex-direction: column; gap: 6px;
    }
    .chunk-card:hover { border-color: #5c6bc0; }
    .chunk-card-meta {
        font-size: 10px; font-weight: 600; color: #5c6bc0;
        letter-spacing: 0.06em; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis;
    }
    .chunk-card-body {
        font-size: 12px; color: #757575; line-height: 1.5;
        overflow: hidden; display: -webkit-box;
        -webkit-line-clamp: 5; -webkit-box-orient: vertical;
    }

    /* ── Chunk reader modal ─────────────────────────────────────────── */
    .chunk-reader-meta {
        font-size: 12px; color: #5c6bc0; font-weight: 600;
        margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #c5cae9;
    }
    .chunk-reader-body {
        font-size: 14px; color: #1a1b2e; line-height: 1.8;
        white-space: pre-wrap; word-break: break-word;
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
if not st.session_state.get("_app_logged"):
    logger.info("APP_START | Streamlit app initialized")
    st.session_state["_app_logged"] = True


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


def get_document_count():
    try:
        col = chroma_client.get_or_create_collection(COLLECTION_NAME)
        total = col.count()
        if total == 0:
            return 0
        seen, offset, batch = set(), 0, 500
        while offset < total:
            result = col.get(limit=batch, offset=offset, include=["metadatas"])
            for meta in result["metadatas"]:
                seen.add(meta.get("filename", ""))
            offset += batch
        return len(seen)
    except Exception:
        return 0


def find_existing_filenames(filenames: list) -> list:
    try:
        col = chroma_client.get_or_create_collection(COLLECTION_NAME)
        return [f for f in filenames if col.get(where={"filename": f}, limit=1)["ids"]]
    except Exception:
        return []


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
    st.session_state["page"] = "home"
if "mode" not in st.session_state:
    st.session_state["mode"] = None

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
if "awaiting_duplicate_confirm" not in st.session_state:
    st.session_state["awaiting_duplicate_confirm"] = False
if "duplicate_files" not in st.session_state:
    st.session_state["duplicate_files"] = []
if "models_ready" not in st.session_state:
    st.session_state["models_ready"] = False
if "model_load_time" not in st.session_state:
    st.session_state["model_load_time"] = None
if "upload_ingest_results" not in st.session_state:
    st.session_state["upload_ingest_results"] = []
if "upload_ingest_total" not in st.session_state:
    st.session_state["upload_ingest_total"] = 0
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
if "pending_query" not in st.session_state:
    st.session_state["pending_query"] = None
if "chunk_viewer_open" not in st.session_state:
    st.session_state["chunk_viewer_open"] = False
if "chunk_viewer_data" not in st.session_state:
    st.session_state["chunk_viewer_data"] = None
if "pending_delete_file" not in st.session_state:
    st.session_state["pending_delete_file"] = None
if "awaiting_file_delete_confirm" not in st.session_state:
    st.session_state["awaiting_file_delete_confirm"] = False
if "delete_result" not in st.session_state:
    st.session_state["delete_result"] = None
if "eval_results" not in st.session_state:
    st.session_state["eval_results"] = None


# ── Task lock ─────────────────────────────────────────────────────────────────
# If a query is queued, lock before any widget is rendered this pass so that
# sidebar nav, source buttons, and chat input are all disabled.
if st.session_state["pending_query"] is not None:
    st.session_state["task_in_progress"] = True
task_running = st.session_state["task_in_progress"]

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚡ TGTRANSCO Bot")
    st.markdown("---")

    # Service status
    st.markdown('<div class="section-label">System Status</div>', unsafe_allow_html=True)

    chroma_ok   = check_chromadb()
    ollama_ok   = check_ollama()
    model_name  = os.getenv("OLLAMA_MODEL", defaults.OLLAMA_MODEL)
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
    doc_count = get_document_count()
    st.markdown(f"""
    <div style="font-size:13px; color:rgba(255,255,255,0.75); line-height:2;">
        <div>Documents ingested: <strong style="color:#ffffff">{doc_count:,}</strong></div>
        <div>Chunks indexed: <strong style="color:#ffffff">{chunk_count:,}</strong></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    # Navigation
    if task_running:
        st.warning("⏳ Task in progress — navigation locked")

    _cur     = st.session_state["page"]
    _loading = (_cur == "loading")
    _mode    = st.session_state["mode"]

    if _cur not in ("home", "loading"):
        if st.button("← Back", use_container_width=True, disabled=task_running):
            st.session_state["page"] = "home"
            st.rerun()

    if _mode == "query":
        if st.button("💬  Chat", use_container_width=True,
                     disabled=task_running or _loading,
                     type="primary" if _cur == "chat" else "secondary"):
            st.session_state["page"] = "chat"
            st.rerun()

    if _mode == "ingest":
        if st.button("⬆️  Ingest", use_container_width=True,
                     disabled=task_running or _loading,
                     type="primary" if _cur == "upload" else "secondary"):
            st.session_state["page"] = "upload"
            st.rerun()

    if _mode in ("query", "ingest"):
        if st.button("📁  Documents", use_container_width=True,
                     disabled=task_running or _loading,
                     type="primary" if _cur == "docs" else "secondary"):
            st.session_state["page"] = "docs"
            st.rerun()

    if _mode in ("query", "ingest"):
        if st.button("🧩  Chunks", use_container_width=True,
                     disabled=task_running or _loading,
                     type="primary" if _cur == "chunks" else "secondary"):
            st.session_state["page"] = "chunks"
            st.rerun()

    if _mode in ("query", "ingest"):
        if st.button("🗑️  Delete", use_container_width=True,
                     disabled=task_running or _loading,
                     type="primary" if _cur == "delete" else "secondary"):
            st.session_state["page"] = "delete"
            st.rerun()

    if ENABLE_EVALUATION and _mode in ("query", "ingest"):
        if st.button("🔬  Evaluate", use_container_width=True,
                     disabled=task_running or _loading,
                     type="primary" if _cur == "evaluate" else "secondary"):
            st.session_state["page"] = "evaluate"
            st.rerun()


@st.cache_data(show_spinner=False, ttl=60)
def get_all_chunks(limit: int = 2000) -> list[dict]:
    """Return up to `limit` chunks as list of {id, text, filename, page, headings}."""
    try:
        col = chroma_client.get_or_create_collection(COLLECTION_NAME)
        total = col.count()
        if total == 0:
            return []
        result = col.get(limit=min(limit, total), include=["documents", "metadatas"])
        out = []
        for cid, text, meta in zip(result["ids"], result["documents"], result["metadatas"]):
            out.append({
                "id":       cid,
                "text":     text or "",
                "filename": meta.get("filename", "unknown"),
                "page":     meta.get("page", "?"),
                "headings": meta.get("headings", ""),
            })
        return out
    except Exception:
        return []


@st.cache_data(show_spinner=False, ttl=60)
def get_file_chunk_counts() -> dict:
    """Return {filename: {"count": int, "folder": str}} for every indexed file."""
    try:
        col = chroma_client.get_or_create_collection(COLLECTION_NAME)
        total = col.count()
        if total == 0:
            return {}
        counts: dict = {}
        offset, batch = 0, 500
        while offset < total:
            result = col.get(limit=batch, offset=offset, include=["metadatas"])
            for meta in result["metadatas"]:
                fname  = meta.get("filename", "unknown")
                folder = meta.get("folder", "")
                entry  = counts.setdefault(fname, {"count": 0, "folder": folder})
                entry["count"] += 1
            offset += batch
        return counts
    except Exception:
        return {}


def delete_file_chunks(filename: str) -> int:
    """Delete all indexed chunks for *filename*. Returns number of chunks removed."""
    try:
        col     = chroma_client.get_or_create_collection(COLLECTION_NAME)
        results = col.get(where={"filename": filename}, include=[])
        ids     = results.get("ids", [])
        if ids:
            col.delete(ids=ids)
        return len(ids)
    except Exception as e:
        logger.exception(f"DELETE_FILE_CHUNKS_FAIL | file={filename!r} | error={e}")
        raise


# ══════════════════════════════════════════════════════════════════════════════
# CHUNK READER MODAL
# ══════════════════════════════════════════════════════════════════════════════
@st.dialog("🧩 Chunk Reader", width="large")
def _chunk_reader_modal():
    st.session_state["chunk_viewer_open"] = False
    chunk = st.session_state["chunk_viewer_data"]
    if not chunk:
        st.warning("No chunk selected.")
        return

    fname    = chunk["filename"]
    page     = chunk["page"]
    headings = chunk["headings"]
    text     = chunk["text"]
    chars    = len(text)

    meta_parts = [f"📄 {fname}", f"page {page}", f"{chars:,} chars (~{chars // 4} tokens)"]
    if headings:
        meta_parts.append(f"§ {headings}")
    st.markdown(
        f'<div class="chunk-reader-meta">{" · ".join(meta_parts)}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="chunk-reader-body">{html.escape(text)}</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PDF VIEWER MODAL
# ══════════════════════════════════════════════════════════════════════════════
@st.dialog("📄 Document Viewer", width="large")
def _pdf_viewer_modal():
    # Reset the open flag immediately so external reruns (query submit, etc.)
    # don't reopen the dialog — navigation buttons set it back before rerunning.
    st.session_state["pdf_viewer_open"] = False

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
            st.session_state["pdf_viewer_open"] = True
            st.session_state["pdf_viewer_page"] -= 1
            st.rerun()
    with c2:
        if st.button("Next →", key="pdf_next",
                     disabled=(cur_pg >= total), use_container_width=True):
            st.session_state["pdf_viewer_open"] = True
            st.session_state["pdf_viewer_page"] += 1
            st.rerun()
    with c3:
        if st.button(f"↩ p.{ref_pg}", key="pdf_ref",
                     disabled=(cur_pg == ref_pg), use_container_width=True,
                     help=f"Jump to referenced page {ref_pg}"):
            st.session_state["pdf_viewer_open"] = True
            st.session_state["pdf_viewer_page"] = ref_pg
            st.rerun()

    try:
        img_bytes = render_pdf_page(fp, cur_pg)
        st.image(img_bytes, use_container_width=True)
    except Exception as e:
        st.error(f"Cannot render page: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: HOME
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state["page"] == "home":

    st.markdown("### TGTRANSCO Bot")
    st.markdown(
        "<div style='color:#8b8fa8;font-size:15px;margin-bottom:40px;'>"
        "Choose a mode to get started.</div>",
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2, gap="large")

    with col1:
        st.markdown("""
        <div class="mode-card">
            <div class="mode-icon">💬</div>
            <div class="mode-title">Query</div>
            <div class="mode-desc">Ask questions about your indexed documents using AI-powered search.</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Enter Query Mode", key="home_query", use_container_width=True, type="primary"):
            from pipeline import query_models_ready
            st.session_state["mode"] = "query"
            if query_models_ready():
                st.session_state["models_ready"] = True
                st.session_state["page"] = "chat"
            else:
                st.session_state["models_ready"] = False
                st.session_state["page"] = "loading"
            st.rerun()

    with col2:
        st.markdown("""
        <div class="mode-card">
            <div class="mode-icon">⬆️</div>
            <div class="mode-title">Ingest</div>
            <div class="mode-desc">Upload and index new PDF documents into the knowledge base.</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("Enter Ingest Mode", key="home_ingest", use_container_width=True, type="secondary"):
            from modules.ingestion import ingest_models_ready
            st.session_state["mode"] = "ingest"
            if ingest_models_ready():
                st.session_state["models_ready"] = True
                st.session_state["page"] = "upload"
            else:
                st.session_state["models_ready"] = False
                st.session_state["page"] = "loading"
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: LOADING
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state["page"] == "loading":

    _mode = st.session_state["mode"]
    _label = "Query" if _mode == "query" else "Ingest"
    st.markdown(f"### Initializing {_label} Models")

    t0 = time.time()

    if _mode == "query":
        from pipeline import get_embedder, get_reranker, get_llm, get_vectorstore
        with st.status("Loading models…", expanded=True) as _status:
            st.write("Loading embedding model…")
            get_embedder()
            st.write("Loading reranker…")
            get_reranker()
            st.write("Connecting to LLM…")
            get_llm()
            st.write("Preparing vector store…")
            get_vectorstore()
            _elapsed = round(time.time() - t0, 1)
            _status.update(label=f"Ready — {_elapsed}s", state="complete")
    else:
        from modules.ingestion import get_converter, get_chunker
        from pipeline import get_embedder, get_vectorstore
        with st.status("Loading models…", expanded=True) as _status:
            st.write("Loading document converter…")
            get_converter()
            st.write("Loading document chunker…")
            get_chunker()
            st.write("Loading embedding model…")
            get_embedder()
            st.write("Preparing vector store…")
            get_vectorstore()
            _elapsed = round(time.time() - t0, 1)
            _status.update(label=f"Ready — {_elapsed}s", state="complete")

    st.session_state["models_ready"] = True
    st.session_state["model_load_time"] = _elapsed
    st.session_state["page"] = "chat" if _mode == "query" else "upload"
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: CHAT
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state["page"] == "chat":

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
                safe_content = html.escape(msg["content"])
                st.markdown(f'<div class="user-msg">{safe_content}</div>',
                            unsafe_allow_html=True)
            else:
                elapsed_html = ""
                if msg.get("elapsed"):
                    elapsed_html = f'<div style="font-size:11px;color:#9e9ea8;margin-top:8px;">⏱ {msg["elapsed"]}s</div>'

                safe_content = html.escape(msg["content"]).replace("\n", "<br>")
                st.markdown(
                    f'<div class="bot-msg">{safe_content}{elapsed_html}</div>',
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
                                disabled=task_running,
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

    # Phase 1: capture question, lock UI, rerun so all buttons render disabled.
    if question := st.chat_input(
        "Ask a question about your documents...",
        disabled=(chunk_count == 0 or task_running),
    ):
        st.session_state["messages"].append({"role": "user", "content": question})
        st.session_state["pending_query"] = question
        st.rerun()

    # Phase 2: execute the queued query (all widgets already rendered disabled).
    if st.session_state["pending_query"] is not None:
        question = st.session_state["pending_query"]
        st.session_state["pending_query"] = None

        try:
            with st.spinner("Searching documents..."):
                sources, token_iter, t_start = query_stream(question)

            answer = st.write_stream(token_iter)
            elapsed = round(time.time() - t_start, 1)

            logger.info(f"QUERY_OK | elapsed={elapsed}s | sources={len(sources)} | q={question!r}")
            st.session_state["messages"].append({
                "role":    "assistant",
                "content": answer,
                "sources": sources,
                "elapsed": elapsed,
            })
        except Exception as e:
            logger.error(f"QUERY_ERROR | q={question!r} | error={e}")
            st.session_state["messages"].append({
                "role":    "assistant",
                "content": f"Error: {str(e)}",
                "sources": [],
                "elapsed": None,
            })

        st.session_state["task_in_progress"] = False
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DOCUMENTS
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state["page"] == "docs":

    st.markdown("### Documents")

    if st.session_state["pdf_viewer_open"] and st.session_state["pdf_viewer_path"]:
        _pdf_viewer_modal()

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
                        if pdf.exists():
                            st.session_state.update({
                                "pdf_viewer_open":     True,
                                "pdf_viewer_path":     pdf_path,
                                "pdf_viewer_filename": pdf.name,
                                "pdf_viewer_page":     1,
                                "pdf_viewer_ref_page": 1,
                                "pdf_viewer_total":    get_pdf_page_count(pdf_path),
                            })
                        else:
                            st.toast(f"File not found: {pdf.name}", icon="⚠️")
                        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: UPLOAD & INGEST
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state["page"] == "upload":

    st.markdown("### Upload & Ingest")

    if st.session_state["pdf_viewer_open"] and st.session_state["pdf_viewer_path"]:
        _pdf_viewer_modal()

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

                    results.append({"name": fname, "status": "success",
                                    "added": added, "replaced": replaced})
                    total_chunks += added
            except Exception as e:
                logger.error(f"INGEST_ERROR | file={fname!r} | error={e}")
                upload_failures.append(FailedFileRecord(fname, str(e)))
                results.append({"name": fname, "status": "error", "message": str(e)})

        st.session_state["upload_ingest_results"] = results
        st.session_state["upload_ingest_total"] = total_chunks
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
        if st.button("⚡ Ingest All", type="primary",
                     disabled=st.session_state["awaiting_duplicate_confirm"]):
            pending = [{"name": f.name, "size": f.size, "bytes": f.read()} for f in uploaded]
            duplicates = find_existing_filenames([f["name"] for f in pending])
            st.session_state["upload_ingest_results"] = []
            st.session_state["upload_ingest_total"] = 0
            st.session_state["pending_upload_files"] = pending
            if duplicates:
                st.session_state["duplicate_files"] = duplicates
                st.session_state["awaiting_duplicate_confirm"] = True
            else:
                st.session_state["ingest_upload_triggered"] = True
                st.session_state["task_in_progress"] = True
            st.rerun()

    # ── Duplicate confirmation prompt ─────────────────────────────────────────
    if st.session_state["awaiting_duplicate_confirm"]:
        dups = st.session_state["duplicate_files"]
        dup_list = "\n".join(f"- **{n}**" for n in dups)
        st.warning(
            f"The following document{'s' if len(dups) > 1 else ''} "
            f"{'are' if len(dups) > 1 else 'is'} already in the knowledge base:\n\n"
            f"{dup_list}\n\n"
            f"Re-ingesting will **permanently delete** the existing chunks from ChromaDB "
            f"before adding new ones. This cannot be undone. Do you want to proceed?"
        )
        col_yes, col_no, _ = st.columns([1, 1, 4])
        with col_yes:
            if st.button("Yes, re-ingest", type="primary", use_container_width=True):
                st.session_state["awaiting_duplicate_confirm"] = False
                st.session_state["duplicate_files"] = []
                st.session_state["ingest_upload_triggered"] = True
                st.session_state["task_in_progress"] = True
                st.rerun()
        with col_no:
            if st.button("Cancel", use_container_width=True):
                st.session_state["awaiting_duplicate_confirm"] = False
                st.session_state["duplicate_files"] = []
                st.session_state["pending_upload_files"] = []
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
        "<small style='color:#757575'>Ingest all PDFs already present in the mounted "
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
    st.markdown('<div class="section-label">Indexed Documents</div>', unsafe_allow_html=True)

    all_pdfs_ingest = get_all_pdfs()
    if not all_pdfs_ingest:
        st.markdown("<small style='color:#757575'>No PDFs found on disk yet.</small>",
                    unsafe_allow_html=True)
    else:
        doc_search = st.text_input("🔍  Filter documents...", placeholder="Type to filter...",
                                   key="ingest_doc_search")
        for folder, paths in sorted(all_pdfs_ingest.items()):
            filtered = [p for p in paths
                        if not doc_search or doc_search.lower() in pathlib.Path(p).name.lower()]
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
                    if st.button("Open", key=f"ingest_open_{pdf_path}"):
                        if pdf.exists():
                            st.session_state.update({
                                "pdf_viewer_open":     True,
                                "pdf_viewer_path":     pdf_path,
                                "pdf_viewer_filename": pdf.name,
                                "pdf_viewer_page":     1,
                                "pdf_viewer_ref_page": 1,
                                "pdf_viewer_total":    get_pdf_page_count(pdf_path),
                            })
                        else:
                            st.toast(f"File not found: {pdf.name}", icon="⚠️")
                        st.rerun()

    st.markdown(
        "<small style='color:#757575;'>To delete chunks for a specific document or clear "
        "the entire knowledge base, use the <strong>🗑️ Delete</strong> page in the sidebar.</small>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: CHUNKS
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state["page"] == "chunks":

    if st.session_state["chunk_viewer_open"] and st.session_state["chunk_viewer_data"]:
        _chunk_reader_modal()

    st.markdown("### Chunks")

    all_chunks = get_all_chunks()

    if not all_chunks:
        st.info("No chunks in the knowledge base yet. Ingest some documents first.")
        if st.button("🔄 Refresh", key="chunks_refresh_empty"):
            get_all_chunks.clear()
            st.rerun()
    else:
        # Controls row
        col_search, col_file, col_count, col_refresh = st.columns([3, 2, 1, 1])
        with col_search:
            search = st.text_input("🔍  Search chunks...", placeholder="Filter by text or filename...",
                                   key="chunk_search", label_visibility="collapsed")
        with col_file:
            filenames = sorted({c["filename"] for c in all_chunks})
            file_filter = st.selectbox("File", ["All files"] + filenames,
                                       key="chunk_file_filter", label_visibility="collapsed")
        with col_count:
            st.markdown(
                f"<div style='font-size:12px;color:#757575;padding-top:8px;text-align:right;'>"
                f"{len(all_chunks):,} chunks</div>",
                unsafe_allow_html=True,
            )
        with col_refresh:
            if st.button("🔄 Refresh", key="chunks_refresh", use_container_width=True,
                         help="Re-fetch chunks from ChromaDB"):
                get_all_chunks.clear()
                st.rerun()

        # Filter
        filtered_chunks = all_chunks
        if file_filter != "All files":
            filtered_chunks = [c for c in filtered_chunks if c["filename"] == file_filter]
        if search:
            q = search.lower()
            filtered_chunks = [
                c for c in filtered_chunks
                if q in c["text"].lower() or q in c["filename"].lower()
            ]

        if not filtered_chunks:
            st.info("No chunks match your filter.")
        else:
            st.caption(f"Showing {len(filtered_chunks):,} chunk{'s' if len(filtered_chunks) != 1 else ''}")

            COLS = 4
            for row_start in range(0, len(filtered_chunks), COLS):
                row_chunks = filtered_chunks[row_start:row_start + COLS]
                cols = st.columns(COLS)
                for col_idx, chunk in enumerate(row_chunks):
                    with cols[col_idx]:
                        short_fname = (chunk["filename"][:28] + "…") if len(chunk["filename"]) > 28 else chunk["filename"]
                        preview = chunk["text"][:200].replace("\n", " ")
                        st.markdown(
                            f'<div class="chunk-card">'
                            f'<div class="chunk-card-meta">📄 {html.escape(short_fname)} · p.{chunk["page"]}</div>'
                            f'<div class="chunk-card-body">{html.escape(preview)}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        if st.button("Open", key=f"chunk_{chunk['id']}", use_container_width=True):
                            st.session_state["chunk_viewer_open"] = True
                            st.session_state["chunk_viewer_data"] = chunk
                            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DELETE
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state["page"] == "delete":

    st.markdown("### Delete Chunks")

    # ── Single-file deletion ──────────────────────────────────────────────────
    st.markdown('<div class="section-label">Delete by Document</div>', unsafe_allow_html=True)
    st.markdown(
        "<small style='color:#757575'>Select a document to permanently remove all its "
        "indexed chunks from the knowledge base. The PDF file on disk is not affected.</small>",
        unsafe_allow_html=True,
    )

    file_counts = get_file_chunk_counts()

    if not file_counts:
        st.info("No indexed documents found.")
    else:
        del_search = st.text_input(
            "🔍  Search documents...", placeholder="Type to filter by filename...",
            key="delete_search",
        )

        # Group by folder, mirroring the Documents page layout
        by_folder: dict[str, list[str]] = {}
        for fname, info in file_counts.items():
            by_folder.setdefault(info["folder"] or "Unknown", []).append(fname)

        any_shown = False
        for folder in sorted(by_folder):
            fnames = sorted(by_folder[folder])
            if del_search:
                fnames = [f for f in fnames if del_search.lower() in f.lower()]
            if not fnames:
                continue
            any_shown = True
            st.markdown(
                f'<div class="folder-header">📂 {folder} ({len(fnames)})</div>',
                unsafe_allow_html=True,
            )
            for fname in fnames:
                count = file_counts[fname]["count"]
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.markdown(
                        f'<div class="doc-card">'
                        f'<div class="doc-name">📄 {html.escape(fname)}</div>'
                        f'<div class="doc-meta">{count:,} chunk{"s" if count != 1 else ""} indexed</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                with col2:
                    if st.button("Delete", key=f"del_{fname}", use_container_width=True,
                                 disabled=st.session_state["awaiting_file_delete_confirm"]):
                        st.session_state["pending_delete_file"]      = fname
                        st.session_state["awaiting_file_delete_confirm"] = True
                        st.session_state["delete_result"]            = None
                        st.rerun()

        if not any_shown:
            st.info("No documents match your filter.")

    # ── Confirmation banner ───────────────────────────────────────────────────
    if st.session_state["awaiting_file_delete_confirm"]:
        fname  = st.session_state["pending_delete_file"]
        count  = file_counts.get(fname, {}).get("count", "?")
        st.warning(
            f"Delete all **{count:,} chunk{'s' if count != 1 else ''}** "
            f"for **{fname}**? This cannot be undone."
        )
        col_yes, col_no, _ = st.columns([1, 1, 4])
        with col_yes:
            if st.button("Yes, delete", type="primary", use_container_width=True):
                try:
                    removed = delete_file_chunks(fname)
                    get_file_chunk_counts.clear()
                    get_all_chunks.clear()
                    logger.warning(f"FILE_CHUNKS_DELETED | file={fname!r} | chunks={removed}")
                    st.session_state["delete_result"] = ("success", fname, removed)
                except Exception as e:
                    st.session_state["delete_result"] = ("error", fname, str(e))
                st.session_state["awaiting_file_delete_confirm"] = False
                st.session_state["pending_delete_file"]          = None
                st.rerun()
        with col_no:
            if st.button("Cancel", use_container_width=True):
                st.session_state["awaiting_file_delete_confirm"] = False
                st.session_state["pending_delete_file"]          = None
                st.rerun()

    # Show result from last deletion
    if st.session_state["delete_result"]:
        kind, fname, detail = st.session_state["delete_result"]
        if kind == "success":
            st.success(f"✓ Deleted {detail:,} chunk{'s' if detail != 1 else ''} for **{fname}**.")
        else:
            st.error(f"✗ Failed to delete chunks for **{fname}**: {detail}")

    # ── Full DB clear ─────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown('<div class="section-label">Danger Zone</div>', unsafe_allow_html=True)
    st.markdown(
        "<small style='color:#757575'>Permanently deletes <strong>all</strong> indexed chunks "
        "from the knowledge base. PDFs on disk are not affected.</small>",
        unsafe_allow_html=True,
    )
    confirm_all = st.checkbox("I understand this will erase all indexed data")
    if st.button("🗑️ Clear Entire Knowledge Base", type="secondary", disabled=not confirm_all):
        try:
            col_db = chroma_client.get_or_create_collection(COLLECTION_NAME)
            total_before = col_db.count()
            chroma_client.delete_collection(COLLECTION_NAME)
            chroma_client.create_collection(COLLECTION_NAME)
            from pipeline import reset_vectorstore
            reset_vectorstore()
            get_file_chunk_counts.clear()
            get_all_chunks.clear()
            logger.warning(f"DB_CLEARED | chunks_removed={total_before}")

            st.session_state["delete_result"] = None
            st.success(f"Knowledge base cleared — {total_before:,} chunks removed.")
            st.rerun()
        except Exception as e:
            logger.error(f"DB_CLEAR_ERROR | error={e}")
            st.error(f"Failed to clear database: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: EVALUATE  (enabled via defaults.ENABLE_EVALUATION or ENABLE_EVALUATION env var)
# ══════════════════════════════════════════════════════════════════════════════
elif ENABLE_EVALUATION and st.session_state["page"] == "evaluate":
    from modules.evaluation import (
        extract_source_info, structural_metrics,
        generate_questions, retrieval_score, composite_score, score_grade,
        MIN_CHUNK_CHARS as _MIN_CHUNK_CHARS,
    )
    from pipeline import get_vectorstore, get_reranker, get_llm

    _RETRIEVAL_K   = int(os.getenv("RETRIEVAL_K",   defaults.RETRIEVAL_K))
    _RERANKER_TOP_N = int(os.getenv("RERANKER_TOP_N", defaults.RERANKER_TOP_N))

    st.markdown("### 🔬 Chunk Evaluator")
    st.markdown(
        "<div style='color:#8b8fa8;font-size:15px;margin-bottom:24px;'>"
        "Score chunking quality for any indexed document using structural "
        "heuristics and LLM-based synthetic Q&amp;A.</div>",
        unsafe_allow_html=True,
    )

    _file_counts = get_file_chunk_counts()
    if not _file_counts:
        st.info("No documents indexed yet. Ingest some PDFs first.")
        st.stop()

    _col_sel, _col_q, _col_btn = st.columns([3, 2, 1])
    with _col_sel:
        _doc_names   = sorted(_file_counts.keys())
        _selected_doc = st.selectbox("Document", _doc_names)
    with _col_q:
        _n_questions = st.slider("Synthetic questions", min_value=3, max_value=10, value=5)
    with _col_btn:
        st.markdown("<div style='padding-top:28px;'></div>", unsafe_allow_html=True)
        _run = st.button("▶ Run", type="primary", use_container_width=True)

    if _run and _selected_doc:
        # ── 1. Fetch indexed chunks for the selected file ─────────────────
        with st.spinner("Fetching indexed chunks…"):
            _col_db = chroma_client.get_or_create_collection(COLLECTION_NAME)
            _db_result = _col_db.get(
                where={"filename": _selected_doc},
                include=["documents", "metadatas"],
            )
            _chunks = [
                {"text": doc or "", **(meta or {})}
                for doc, meta in zip(_db_result["documents"], _db_result["metadatas"])
            ]

        # ── 2. Locate source PDF ──────────────────────────────────────────
        _pdf_path = None
        for _c in _chunks:
            _p = _c.get("full_path", "")
            if _p and pathlib.Path(_p).exists():
                _pdf_path = _p
                break

        # ── 3. Extract source text ────────────────────────────────────────
        if _pdf_path:
            with st.spinner("Extracting text from source PDF…"):
                _source_text, _table_count, _heading_count = extract_source_info(_pdf_path)
        else:
            st.warning("Original PDF not found on disk — question generation will use chunk text.")
            _source_text  = " ".join(c["text"] for c in _chunks)
            _table_count  = 0
            _heading_count = 0

        # ── 4. Structural metrics ─────────────────────────────────────────
        with st.spinner("Computing structural metrics…"):
            _struct = structural_metrics(_chunks, _table_count, _heading_count)

        # ── 5. Generate synthetic questions ───────────────────────────────
        with st.spinner(f"Generating {_n_questions} synthetic questions via LLM…"):
            _llm       = get_llm()
            _questions = generate_questions(_source_text, _n_questions, _llm)

        if not _questions:
            st.warning("LLM did not return any questions — retrieval score will be 0.")
            _ret = {"hit_rate": 0.0, "mrr": 0.0, "per_question": []}
        else:
            # ── 6. Retrieval evaluation ───────────────────────────────────
            with st.spinner(f"Evaluating retrieval for {len(_questions)} questions…"):
                _k     = min(_RETRIEVAL_K, _struct["total_chunks"])
                _top_n = min(_RERANKER_TOP_N, _k)
                _ret   = retrieval_score(
                    _questions,
                    get_vectorstore(),
                    get_reranker(),
                    _selected_doc,
                    _llm,
                    k=_k,
                    top_n=_top_n,
                )

        _score, _breakdown = composite_score(_struct, _ret)

        st.session_state["eval_results"] = {
            "doc":       _selected_doc,
            "struct":    _struct,
            "ret":       _ret,
            "score":     _score,
            "breakdown": _breakdown,
        }

    # ── Results display ───────────────────────────────────────────────────────
    _res = st.session_state.get("eval_results")
    if _res and _res["doc"] == _selected_doc:
        _struct    = _res["struct"]
        _ret       = _res["ret"]
        _score     = _res["score"]
        _breakdown = _res["breakdown"]
        _grade, _grade_color = score_grade(_score)

        st.markdown("---")

        # ── Composite score card ──────────────────────────────────────────
        _sc1, _sc2, _sc3 = st.columns([1, 1, 2])

        with _sc1:
            st.markdown(f"""
            <div style="text-align:center; padding:20px; background:#f5f6ff;
                        border:2px solid {_grade_color}; border-radius:12px;">
                <div style="font-size:52px; font-weight:700; color:{_grade_color};
                            line-height:1;">{_score}</div>
                <div style="font-size:13px; color:#9e9ea8; margin:4px 0;">out of 100</div>
                <div style="font-size:28px; font-weight:600; color:{_grade_color};">{_grade}</div>
            </div>""", unsafe_allow_html=True)

        with _sc2:
            st.markdown(f"""
            <div style="padding:20px; background:#f5f6ff; border:1px solid #c5cae9;
                        border-radius:12px; height:100%;">
                <div style="font-size:12px; color:#9e9ea8; font-weight:600;
                            letter-spacing:.06em; text-transform:uppercase;
                            margin-bottom:12px;">Component Scores</div>
                <div style="font-size:15px; margin-bottom:8px;">
                    Structural &nbsp;
                    <strong>{_breakdown['structural_score']}/100</strong>
                    <span style="color:#9e9ea8; font-size:11px;"> (40%)</span>
                </div>
                <div style="font-size:15px;">
                    Retrieval &nbsp;
                    <strong>{_breakdown['retrieval_score']}/100</strong>
                    <span style="color:#9e9ea8; font-size:11px;"> (60%)</span>
                </div>
            </div>""", unsafe_allow_html=True)

        with _sc3:
            _tc_display = (
                f"{_struct['table_coverage']*100:.0f}%"
                if _struct.get("table_coverage") is not None
                else "N/A"
            )
            st.markdown(f"""
            <div style="padding:20px; background:#f5f6ff; border:1px solid #c5cae9;
                        border-radius:12px; height:100%;">
                <div style="font-size:12px; color:#9e9ea8; font-weight:600;
                            letter-spacing:.06em; text-transform:uppercase;
                            margin-bottom:12px;">Summary — {_res['doc']}</div>
                <div style="font-size:13px; line-height:2.2; color:#1a1b2e;">
                    <div>Chunks indexed: <strong>{_struct['total_chunks']:,}</strong></div>
                    <div>Avg chunk size: <strong>{_struct['avg_chars']:,.0f} chars</strong></div>
                    <div>Hit rate: <strong>{_ret['hit_rate']*100:.0f}%</strong></div>
                    <div>MRR: <strong>{_ret['mrr']:.3f}</strong></div>
                    <div>Table coverage: <strong>{_tc_display}</strong></div>
                    <div>Heading rate: <strong>{_struct['heading_rate']*100:.0f}%</strong></div>
                </div>
            </div>""", unsafe_allow_html=True)

        st.markdown("---")

        # ── Detailed metric tables ────────────────────────────────────────
        _m1, _m2 = st.columns(2)

        with _m1:
            st.markdown("**Structural Metrics**")
            _tc_str = (
                f"{_struct['table_coverage']*100:.1f}%  "
                f"({_struct['chunks_with_tables']} chunks / {_struct['source_tables']} source tables)"
                if _struct.get("table_coverage") is not None
                else f"N/A — no tables detected in source"
            )
            st.markdown(f"""
| Metric | Value |
|--------|-------|
| Total chunks | {_struct['total_chunks']:,} |
| Avg chunk size | {_struct['avg_chars']:,.0f} chars |
| Std deviation | {_struct['std_chars']:,.0f} chars |
| Min / Max | {_struct['min_chars']:,} / {_struct['max_chars']:,} chars |
| Short chunks (<{_MIN_CHUNK_CHARS} chars) | {_struct['short_chunk_ratio']*100:.1f}% ({_struct['total_chunks'] - round(_struct['total_chunks']*(1-_struct['short_chunk_ratio'])):,} chunks) |
| Chunks with heading metadata | {_struct['heading_rate']*100:.0f}% ({_struct['chunks_with_headings']:,} / {_struct['total_chunks']:,}) |
| Table coverage | {_tc_str} |
""")

        with _m2:
            st.markdown("**Retrieval Metrics**")
            st.markdown(f"""
| Metric | Value |
|--------|-------|
| Hit rate | {_ret['hit_rate']*100:.0f}% |
| MRR | {_ret['mrr']:.3f} |
| Questions evaluated | {len(_ret['per_question'])} |
| Answered | {sum(1 for r in _ret['per_question'] if r['hit'])} |
| Not answered | {sum(1 for r in _ret['per_question'] if not r['hit'])} |
""")

        # ── Per-question breakdown ────────────────────────────────────────
        if _ret["per_question"]:
            st.markdown("---")

            _total_sec = _ret.get("total_seconds", 0)
            _total_min = int(_total_sec // 60)
            _total_rem = _total_sec % 60
            st.markdown(
                f"**Question-by-question results** "
                f"<span style='color:#9e9ea8; font-size:13px;'>"
                f"— total time: {_total_min}m {_total_rem:.1f}s</span>",
                unsafe_allow_html=True,
            )

            for _i, _qr in enumerate(_ret["per_question"], 1):
                _icon  = "✅" if _qr["hit"] else "❌"
                _err   = _qr.get("error", "")
                _secs  = _qr.get("elapsed_seconds", 0)
                _label = (
                    f"{_icon} Q{_i}: {_qr['question']}  ({_secs}s)"
                    + (f" — error: {_err}" if _err else "")
                )
                with st.expander(_label, expanded=False):
                    _ans = _qr.get("answer", "")
                    if _ans:
                        st.markdown("**LLM answer:**")
                        st.markdown(_ans)
                        st.markdown("---")
                    _ctx = _qr.get("context", "")
                    if _ctx:
                        st.markdown("**Retrieved chunks used:**")
                        st.text(_ctx[:2000] + ("…" if len(_ctx) > 2000 else ""))
                    else:
                        st.caption("No chunks retrieved for this question.")