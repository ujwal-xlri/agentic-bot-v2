import os
import re
import base64
import zlib
import json
import hashlib
import pathlib
import time
import requests
import chromadb
import defaults

from log_config import setup_logger

logger = setup_logger("ingestion")

PDF_DIR              = os.getenv("PDF_DIR",              defaults.PDF_DIR)
CHUNK_SIZE           = int(os.getenv("CHUNK_SIZE",       defaults.CHUNK_SIZE))
COLLECTION_NAME      = os.getenv("COLLECTION_NAME",      defaults.COLLECTION_NAME)
MIN_CHUNK_CHARS      = int(os.getenv("MIN_CHUNK_CHARS",  defaults.MIN_CHUNK_CHARS))
UNSTRUCTURED_API_URL = os.getenv("UNSTRUCTURED_API_URL", defaults.UNSTRUCTURED_API_URL)

_BARE_NUMBER_RE = re.compile(r"^\d+$")


def ingest_models_ready() -> bool:
    """True when the embedder + vectorstore singletons are already warm."""
    from pipeline import _embedder, _vectorstore
    return _embedder is not None and _vectorstore is not None


# ---------------------------------------------------------------------------
# Chroma helpers
# ---------------------------------------------------------------------------

def _get_collection() -> chromadb.Collection:
    from pipeline import get_chroma_client
    logger.debug(f"CHROMA_CONNECT | collection={COLLECTION_NAME!r}")
    try:
        collection = get_chroma_client().get_or_create_collection(COLLECTION_NAME)
        logger.debug(f"CHROMA_CONNECT_OK | collection={COLLECTION_NAME!r}")
        return collection
    except Exception:
        logger.exception(f"CHROMA_CONNECT_FAIL | collection={COLLECTION_NAME!r}")
        raise


def _delete_existing_chunks(filename: str) -> int:
    """Delete all indexed chunks for *filename*. Returns number of chunks removed."""
    logger.debug(f"DEDUP_QUERY | file={filename!r}")
    try:
        col     = _get_collection()
        results = col.get(where={"filename": filename})
        ids     = results.get("ids", [])
    except Exception:
        logger.exception(f"DEDUP_QUERY_FAIL | file={filename!r}")
        raise

    if not ids:
        logger.debug(f"DEDUP_NO_EXISTING | file={filename!r}")
        return 0

    logger.debug(f"DEDUP_DELETE | file={filename!r} | chunk_count={len(ids)}")
    try:
        col.delete(ids=ids)
        logger.debug(f"DEDUP_DELETE_OK | file={filename!r} | deleted={len(ids)}")
    except Exception:
        logger.exception(f"DEDUP_DELETE_FAIL | file={filename!r} | chunk_count={len(ids)}")
        raise

    return len(ids)


def _chunk_id(filename: str, index: int) -> str:
    return hashlib.sha256(f"{filename}::{index}".encode()).hexdigest()


def _clean_chunk(text: str, filename: str) -> str:
    """Strip bare page numbers and filename-stem lines from a chunk."""
    stem      = pathlib.Path(filename).stem
    stem_norm = re.sub(r"[\s_\-]+", " ", stem).strip().lower()

    cleaned = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            cleaned.append(line)
            continue
        if _BARE_NUMBER_RE.match(s):
            continue
        if re.sub(r"[\s_\-]+", " ", s).strip().lower() == stem_norm:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


# ---------------------------------------------------------------------------
# Unstructured API-based parsing
# ---------------------------------------------------------------------------

def _unstructured_chunks(
    pdf_path: str,
    filename: str,
    folder: str,
    strategy: str = "hi_res",
) -> tuple[list, list, list]:
    """POST the PDF to the self-hosted Unstructured API and return (texts, metadatas, ids)."""
    url = f"{UNSTRUCTURED_API_URL}/general/v0/general"
    t0  = time.time()

    try:
        with open(pdf_path, "rb") as fh:
            resp = requests.post(
                url,
                files={"files": (filename, fh, "application/pdf")},
                data={
                    "strategy":                   strategy,
                    "pdf_infer_table_structure":  "true",
                    "chunking_strategy":          "by_title",
                    "max_characters":             str(CHUNK_SIZE * 4),
                    "new_after_n_chars":          str(CHUNK_SIZE * 3),
                    "combine_text_under_n_chars": str(MIN_CHUNK_CHARS * 4),
                    "overlap":                    "200",
                    "overlap_all":                "false",
                },
                timeout=600,
            )
    except Exception:
        logger.exception(
            f"UNSTRUCTURED_API_UNREACHABLE | file={filename!r} | strategy={strategy!r}"
        )
        return [], [], []

    elapsed = round(time.time() - t0, 3)

    if resp.status_code != 200:
        logger.error(
            f"UNSTRUCTURED_API_ERROR | file={filename!r} | strategy={strategy!r} "
            f"| status={resp.status_code} | body={resp.text[:300]!r}"
        )
        return [], [], []

    try:
        elements = resp.json()
    except Exception:
        logger.exception(f"UNSTRUCTURED_API_JSON_FAIL | file={filename!r}")
        return [], [], []

    logger.info(
        f"UNSTRUCTURED_API_OK | file={filename!r} | strategy={strategy!r} "
        f"| elements={len(elements)} | elapsed={elapsed}s"
    )

    texts: list     = []
    metadatas: list = []
    ids: list       = []
    chunk_idx       = 0
    skipped         = 0

    for elem in elements:
        if elem.get("type") not in ("CompositeElement", "Table", "TableChunk"):
            continue

        elem_type = elem.get("type", "")
        meta      = elem.get("metadata", {})

        # For table elements prefer the HTML representation — it preserves
        # row/column structure so the LLM can answer structured queries.
        # Plain text concatenates all cell values into one unstructured blob.
        if elem_type in ("Table", "TableChunk") and meta.get("text_as_html"):
            raw_text = meta["text_as_html"]
        else:
            raw_text = elem.get("text", "")

        text = _clean_chunk(raw_text, filename)
        if len(text) < MIN_CHUNK_CHARS:
            skipped += 1
            continue
        page_no = meta.get("page_number")

        # Decode orig_elements (base64+zlib JSON) to extract Title headings
        headings  = []
        orig_b64  = meta.get("orig_elements", "")
        if orig_b64:
            try:
                orig_json = zlib.decompress(base64.b64decode(orig_b64)).decode()
                headings  = [
                    e["text"] for e in json.loads(orig_json)
                    if e.get("type") == "Title"
                ]
            except Exception as exc:
                logger.warning(
                    f"ORIG_ELEMENTS_DECODE_FAIL | file={filename!r} "
                    f"| chunk_idx={chunk_idx} | error={exc}"
                )

        logger.debug(
            f"CHUNK_BUILD | file={filename!r} | chunk_idx={chunk_idx} "
            f"| page={page_no} | headings={headings!r} | chars={len(text)}"
        )

        texts.append(text)
        metadatas.append({
            "filename":  filename,
            "full_path": pdf_path,
            "folder":    folder,
            "headings":  " > ".join(headings) if headings else "",
            "page":      page_no,
            "chunk_idx": chunk_idx,
        })
        ids.append(_chunk_id(filename, chunk_idx))
        chunk_idx += 1

    if skipped:
        logger.warning(f"CHUNK_SKIPPED_SHORT | file={filename!r} | skipped={skipped}")

    logger.info(
        f"UNSTRUCTURED_CHUNKS | file={filename!r} | strategy={strategy!r} "
        f"| chunks={len(texts)} | skipped={skipped}"
    )
    return texts, metadatas, ids


# ---------------------------------------------------------------------------
# Core ingest
# ---------------------------------------------------------------------------

def ingest(pdf_path: str) -> tuple[int, int]:
    """
    Ingest a PDF via the Unstructured API sidecar (hi_res → fast fallback).
    Removes any previously indexed chunks for this file before adding new ones.
    Returns (chunks_added, chunks_replaced).
    """
    from pipeline import get_vectorstore

    pdf_path = str(pathlib.Path(pdf_path).resolve())
    filename = pathlib.Path(pdf_path).name
    folder   = pathlib.Path(pdf_path).parent.name

    logger.info(f"INGEST_START | file={filename!r} | folder={folder!r} | path={pdf_path!r}")

    if not pathlib.Path(pdf_path).exists():
        logger.error(f"INGEST_FILE_NOT_FOUND | file={filename!r} | path={pdf_path!r}")
        return 0, 0

    # --- Parse first, dedup only if we have chunks to replace with ---
    # Stage 1: hi_res
    texts, metadatas, ids = _unstructured_chunks(pdf_path, filename, folder, strategy="hi_res")

    # Stage 2: fast fallback
    if not texts:
        logger.info(f"INGEST_FALLBACK_FAST | file={filename!r} | reason=hi_res_empty_or_failed")
        texts, metadatas, ids = _unstructured_chunks(pdf_path, filename, folder, strategy="fast")

    if not texts:
        logger.error(f"INGEST_UNREADABLE | file={filename!r} | reason=all_strategies_failed")
        return 0, 0

    # --- Dedup (safe to delete now that we have replacement chunks) ---
    try:
        replaced = _delete_existing_chunks(filename)
    except Exception as e:
        logger.error(f"INGEST_DEDUP_FAIL | file={filename!r} | reason={e}")
        return 0, 0

    if replaced:
        logger.info(f"INGEST_DEDUP | file={filename!r} | removed_chunks={replaced}")

    # --- Write to vector store ---
    logger.debug(f"VECTORSTORE_WRITE_START | file={filename!r} | chunks={len(texts)}")
    try:
        t0          = time.time()
        vectorstore = get_vectorstore()
        vectorstore.add_texts(texts, metadatas=metadatas, ids=ids)
        logger.info(
            f"VECTORSTORE_WRITE_OK | file={filename!r} "
            f"| elapsed={round(time.time()-t0, 3)}s | chunks={len(texts)}"
        )
    except Exception:
        logger.exception(f"VECTORSTORE_WRITE_FAIL | file={filename!r} | chunks={len(texts)}")
        return 0, replaced

    logger.info(
        f"INGEST_DONE | file={filename!r} | chunks_added={len(texts)} "
        f"| chunks_replaced={replaced}"
    )
    return len(texts), replaced


# ---------------------------------------------------------------------------
# Folder ingest
# ---------------------------------------------------------------------------

def ingest_folder(folder_path: str = None) -> dict:
    """
    Ingest all PDFs found recursively under *folder_path*.
    Returns a summary dict: {filename: {"added": int, "replaced": int}}.
    """
    folder_path = folder_path or PDF_DIR
    summary: dict[str, dict] = {}
    failed:  list[str]       = []

    if not pathlib.Path(folder_path).exists():
        logger.error(f"INGEST_FOLDER_NOT_FOUND | path={folder_path!r}")
        return summary

    pdfs = list(pathlib.Path(folder_path).rglob("*.pdf"))
    if not pdfs:
        logger.warning(f"INGEST_FOLDER_EMPTY | path={folder_path!r} | no PDF files found")
        return summary

    logger.info(f"INGEST_FOLDER_START | path={folder_path!r} | files={len(pdfs)}")

    for idx, pdf in enumerate(pdfs, start=1):
        logger.info(f"INGEST_FOLDER_PROGRESS | file={pdf.name!r} | {idx}/{len(pdfs)}")
        try:
            added, replaced = ingest(str(pdf))
            summary[pdf.name] = {"added": added, "replaced": replaced}
            if added == 0:
                logger.warning(
                    f"INGEST_FOLDER_FILE_ZERO_CHUNKS | file={pdf.name!r} "
                    f"| replaced={replaced} | file may be empty or unreadable"
                )
        except Exception as e:
            logger.exception(f"INGEST_FOLDER_FILE_FAIL | file={pdf.name!r}")
            failed.append(pdf.name)
            summary[pdf.name] = {"added": 0, "replaced": 0, "error": str(e)}

    total = sum(v["added"] for v in summary.values())
    logger.info(
        f"INGEST_FOLDER_DONE | path={folder_path!r} | files_attempted={len(pdfs)} "
        f"| files_ok={len(pdfs) - len(failed)} | files_failed={len(failed)} "
        f"| total_chunks={total}"
    )
    if failed:
        logger.error(f"INGEST_FOLDER_FAILURES | files={failed}")

    return summary
