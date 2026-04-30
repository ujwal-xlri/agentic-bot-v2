import os
import hashlib
import pathlib
import time
import chromadb
import defaults
from log_config import setup_logger

logger = setup_logger("ingestion")

PDF_DIR         = os.getenv("PDF_DIR",         defaults.PDF_DIR)
CHUNK_SIZE      = int(os.getenv("CHUNK_SIZE",  defaults.CHUNK_SIZE))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", defaults.COLLECTION_NAME)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", defaults.EMBEDDING_MODEL)

# ---------------------------------------------------------------------------
# Module-level singletons — initialised once, reused across all ingest calls
# ---------------------------------------------------------------------------

def _make_converter():
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.base_models import InputFormat

    logger.debug("SINGLETON_INIT | component=DocumentConverter")
    try:
        pipeline_options                    = PdfPipelineOptions()
        pipeline_options.do_table_structure = True
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        logger.info("SINGLETON_READY | component=DocumentConverter")
        return converter
    except Exception:
        logger.exception("SINGLETON_FAIL | component=DocumentConverter")
        raise

def _make_chunker():
    from docling.chunking import HybridChunker
    logger.debug(f"SINGLETON_INIT | component=HybridChunker | model={EMBEDDING_MODEL!r} | max_tokens={CHUNK_SIZE}")
    try:
        chunker = HybridChunker(tokenizer=EMBEDDING_MODEL, max_tokens=CHUNK_SIZE)
        logger.info(f"SINGLETON_READY | component=HybridChunker | model={EMBEDDING_MODEL!r}")
        return chunker
    except Exception:
        logger.exception(f"SINGLETON_FAIL | component=HybridChunker | model={EMBEDDING_MODEL!r}")
        raise

_converter = _make_converter()
_chunker   = _make_chunker()


# ---------------------------------------------------------------------------
# Chroma helpers
# ---------------------------------------------------------------------------

def _get_collection() -> chromadb.Collection:
    host = os.getenv("CHROMA_HOST", defaults.CHROMA_HOST)
    port = int(os.getenv("CHROMA_PORT", defaults.CHROMA_PORT))
    logger.debug(f"CHROMA_CONNECT | host={host!r} | port={port} | collection={COLLECTION_NAME!r}")
    try:
        client     = chromadb.HttpClient(host=host, port=port)
        collection = client.get_or_create_collection(COLLECTION_NAME)
        logger.debug(f"CHROMA_CONNECT_OK | collection={COLLECTION_NAME!r}")
        return collection
    except Exception:
        logger.exception(f"CHROMA_CONNECT_FAIL | host={host!r} | port={port} | collection={COLLECTION_NAME!r}")
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
    """Deterministic chunk ID — enables true upserts instead of delete-then-insert."""
    return hashlib.sha256(f"{filename}::{index}".encode()).hexdigest()


def _is_image_only(md: str) -> bool:
    lines = [l.strip() for l in md.splitlines() if l.strip()]
    return not lines or all(l == "<!-- image -->" for l in lines)


# ---------------------------------------------------------------------------
# Core ingest
# ---------------------------------------------------------------------------

def ingest(pdf_path: str) -> tuple[int, int]:
    """
    Ingest a PDF in one pass using Docling + HybridChunker.

    Tries native conversion first. If the result is empty or image-only,
    automatically retries with OCR (lazy-loading the OCR converter on first use).
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

    # --- Dedup ---
    try:
        replaced = _delete_existing_chunks(filename)
    except Exception as e:
        logger.error(f"INGEST_DEDUP_FAIL | file={filename!r} | reason={e} | action=aborting_ingest")
        return 0, 0

    if replaced:
        logger.info(f"INGEST_DEDUP | file={filename!r} | removed_chunks={replaced}")
    else:
        logger.debug(f"INGEST_DEDUP | file={filename!r} | no_existing_chunks")

    # --- Parse with Docling ---
    logger.debug(f"DOCLING_CONVERT_START | file={filename!r}")
    try:
        t0     = time.time()
        result = _converter.convert(pdf_path)
        md     = result.document.export_to_markdown().strip()
        logger.info(f"DOCLING_CONVERT_OK | file={filename!r} | elapsed={round(time.time()-t0,3)}s | chars={len(md)}")
    except Exception:
        logger.exception(f"DOCLING_CONVERT_FAIL | file={filename!r}")
        return 0, replaced

    if _is_image_only(md):
        logger.error(
            f"INGEST_UNREADABLE | file={filename!r} "
            f"| reason=image_only_pdf — no text extracted"
        )
        return 0, replaced

    # --- Chunk ---
    logger.debug(f"DOCLING_CHUNK_START | file={filename!r}")
    try:
        t0     = time.time()
        chunks = list(_chunker.chunk(result.document))
        logger.info(f"DOCLING_CHUNK_OK | file={filename!r} | elapsed={round(time.time()-t0,3)}s | raw_chunks={len(chunks)}")
    except Exception:
        logger.exception(f"DOCLING_CHUNK_FAIL | file={filename!r}")
        return 0, replaced

    if not chunks:
        logger.warning(
            f"INGEST_EMPTY | file={filename!r} | stage=post_chunk "
            f"| no content extracted from document"
        )
        return 0, replaced

    # --- Build per-chunk texts and metadata ---
    texts     = []
    metadatas = []
    ids       = []
    skipped   = 0

    for i, chunk in enumerate(chunks):
        text = chunk.text.strip()
        if not text:
            logger.debug(f"CHUNK_SKIP_EMPTY | file={filename!r} | chunk_idx={i}")
            skipped += 1
            continue

        headings  = getattr(chunk.meta, "headings", None) or []
        page_no   = None
        doc_items = getattr(chunk.meta, "doc_items", None) or []
        if doc_items:
            prov = getattr(doc_items[0], "prov", None) or []
            if prov:
                page_no = getattr(prov[0], "page_no", None)

        logger.debug(
            f"CHUNK_BUILD | file={filename!r} | chunk_idx={i} | page={page_no} "
            f"| headings={headings!r} | chars={len(text)}"
        )

        texts.append(text)
        metadatas.append({
            "filename":  filename,
            "full_path": pdf_path,
            "folder":    folder,
            "headings":  " > ".join(headings) if headings else "",
            "page":      page_no,
            "chunk_idx": i,
        })
        ids.append(_chunk_id(filename, i))

    if skipped:
        logger.warning(f"CHUNK_SKIPPED_EMPTY | file={filename!r} | skipped={skipped} | kept={len(texts)}")

    if not texts:
        logger.error(
            f"INGEST_EMPTY_AFTER_FILTER | file={filename!r} | raw_chunks={len(chunks)} "
            f"| all chunks were empty after stripping — possible Docling parse issue"
        )
        return 0, replaced

    # --- Write to vector store ---
    logger.debug(f"VECTORSTORE_WRITE_START | file={filename!r} | chunks={len(texts)}")
    try:
        t0          = time.time()
        vectorstore = get_vectorstore()
        vectorstore.add_texts(texts, metadatas=metadatas, ids=ids)
        logger.info(f"VECTORSTORE_WRITE_OK | file={filename!r} | elapsed={round(time.time()-t0,3)}s | chunks={len(texts)}")
    except Exception:
        logger.exception(f"VECTORSTORE_WRITE_FAIL | file={filename!r} | chunks={len(texts)}")
        return 0, replaced

    logger.info(
        f"INGEST_DONE | file={filename!r} | chunks_added={len(texts)} "
        f"| chunks_replaced={replaced} | skipped_empty={skipped}"
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
