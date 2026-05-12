import os
import re
import hashlib
import pathlib
import time
import chromadb
import defaults
from log_config import setup_logger

logger = setup_logger("ingestion")

PDF_DIR         = os.getenv("PDF_DIR",            defaults.PDF_DIR)
CHUNK_SIZE      = int(os.getenv("CHUNK_SIZE",     defaults.CHUNK_SIZE))
COLLECTION_NAME = os.getenv("COLLECTION_NAME",    defaults.COLLECTION_NAME)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL",    defaults.EMBEDDING_MODEL)
MIN_CHUNK_CHARS = int(os.getenv("MIN_CHUNK_CHARS", defaults.MIN_CHUNK_CHARS))

_BARE_NUMBER_RE = re.compile(r"^\d+$")

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


def _make_ocr_converter():
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.base_models import InputFormat

    logger.debug("SINGLETON_INIT | component=OcrConverter")
    try:
        pipeline_options                    = PdfPipelineOptions()
        pipeline_options.do_ocr             = True
        pipeline_options.do_table_structure = True
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        logger.info("SINGLETON_READY | component=OcrConverter")
        return converter
    except Exception:
        logger.exception("SINGLETON_FAIL | component=OcrConverter")
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

_converter     = None
_ocr_converter = None
_chunker       = None


def _get_converter():
    global _converter
    if _converter is None:
        _converter = _make_converter()
    return _converter


def _get_ocr_converter():
    global _ocr_converter
    if _ocr_converter is None:
        _ocr_converter = _make_ocr_converter()
    return _ocr_converter


def _get_chunker():
    global _chunker
    if _chunker is None:
        _chunker = _make_chunker()
    return _chunker


def get_converter():
    return _get_converter()


def get_chunker():
    return _get_chunker()


def ingest_models_ready() -> bool:
    return _converter is not None and _chunker is not None


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
    """Deterministic chunk ID — enables true upserts instead of delete-then-insert."""
    return hashlib.sha256(f"{filename}::{index}".encode()).hexdigest()


def _clean_chunk(text: str, filename: str) -> str:
    """
    Strip header/footer artifacts from a chunk.
    Removes lines that are bare page numbers or match the document's filename stem.
    Both patterns are derived from the file being ingested — nothing is hardcoded.
    """
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


def _pdfplumber_chunks(pdf_path: str, filename: str, folder: str) -> tuple[list, list, list]:
    """
    Extract text page-by-page via pdfplumber and split into chunks.
    Used as a final fallback when both Docling passes (native + OCR) fail.
    Tables are linearised row-by-row, which is imperfect but searchable.
    """
    import pdfplumber
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter  = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE * 4,  # 4 chars ≈ 1 token
        chunk_overlap=100,
    )
    texts, metadatas, ids = [], [], []
    chunk_idx = 0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if not page_text.strip():
                    continue
                for split in splitter.split_text(page_text):
                    text = _clean_chunk(split, filename)
                    if len(text) < MIN_CHUNK_CHARS:
                        continue
                    texts.append(text)
                    metadatas.append({
                        "filename":  filename,
                        "full_path": pdf_path,
                        "folder":    folder,
                        "headings":  "",
                        "page":      page.page_number,
                        "chunk_idx": chunk_idx,
                    })
                    ids.append(_chunk_id(filename, chunk_idx))
                    chunk_idx += 1
    except Exception:
        logger.exception(f"PDFPLUMBER_FAIL | file={filename!r}")

    logger.info(f"PDFPLUMBER_CHUNKS | file={filename!r} | chunks={len(texts)}")
    return texts, metadatas, ids


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
        result = _get_converter().convert(pdf_path)
        md     = result.document.export_to_markdown().strip()
        logger.info(f"DOCLING_CONVERT_OK | file={filename!r} | elapsed={round(time.time()-t0,3)}s | chars={len(md)}")
    except Exception:
        logger.exception(f"DOCLING_CONVERT_FAIL | file={filename!r}")
        return 0, replaced

    if _is_image_only(md):
        logger.info(f"INGEST_OCR_RETRY | file={filename!r} | reason=image_only_native_pass")
        try:
            t0     = time.time()
            result = _get_ocr_converter().convert(pdf_path)
            md     = result.document.export_to_markdown().strip()
            logger.info(f"DOCLING_OCR_OK | file={filename!r} | elapsed={round(time.time()-t0,3)}s | chars={len(md)}")
        except Exception:
            logger.exception(f"DOCLING_OCR_FAIL | file={filename!r}")
            return 0, replaced

        if _is_image_only(md):
            logger.info(f"INGEST_PDFPLUMBER_FALLBACK | file={filename!r} | reason=image_only_after_ocr")
            texts, metadatas, ids = _pdfplumber_chunks(pdf_path, filename, folder)
            if not texts:
                logger.error(f"INGEST_UNREADABLE | file={filename!r} | reason=all_methods_failed")
                return 0, replaced
            logger.debug(f"VECTORSTORE_WRITE_START | file={filename!r} | chunks={len(texts)}")
            try:
                t0          = time.time()
                vectorstore = get_vectorstore()
                vectorstore.add_texts(texts, metadatas=metadatas, ids=ids)
                logger.info(f"VECTORSTORE_WRITE_OK | file={filename!r} | elapsed={round(time.time()-t0,3)}s | chunks={len(texts)}")
            except Exception:
                logger.exception(f"VECTORSTORE_WRITE_FAIL | file={filename!r} | chunks={len(texts)}")
                return 0, replaced
            logger.info(f"INGEST_DONE | file={filename!r} | chunks_added={len(texts)} | chunks_replaced={replaced} | method=pdfplumber")
            return len(texts), replaced

    # --- Chunk ---
    logger.debug(f"DOCLING_CHUNK_START | file={filename!r}")
    try:
        t0     = time.time()
        chunks = list(_get_chunker().chunk(result.document))
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
        text = _clean_chunk(chunk.text, filename)
        if len(text) < MIN_CHUNK_CHARS:
            logger.debug(f"CHUNK_SKIP_SHORT | file={filename!r} | chunk_idx={i} | chars={len(text)}")
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
