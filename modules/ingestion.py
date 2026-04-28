import os
import pathlib

import chromadb

from log_config import setup_logger

logger = setup_logger("ingestion")

PDF_DIR = os.getenv("PDF_DIR", "/app/pdfs")

CHUNK_SIZE    = 1024
CHUNK_OVERLAP = 128

COLLECTION_NAME = "tgtransco"


def _get_collection():
    host = os.getenv("CHROMA_HOST", "localhost")
    port = int(os.getenv("CHROMA_PORT", "8000"))
    client = chromadb.HttpClient(host=host, port=port)
    return client.get_or_create_collection(COLLECTION_NAME)


def _delete_existing_chunks(filename: str) -> int:
    """Delete all indexed chunks for filename. Returns number of chunks removed."""
    col = _get_collection()
    results = col.get(where={"filename": filename})
    ids = results.get("ids", [])
    if ids:
        col.delete(ids=ids)
    return len(ids)


def ingest(pdf_path: str) -> tuple[int, int]:
    """
    Ingest a PDF in one pass using Docling.
    Removes any previously indexed chunks for this file before adding new ones.
    Returns (chunks_added, chunks_replaced).
    """
    from pipeline import get_vectorstore
    from docling.document_converter import DocumentConverter

    pdf_path = str(pathlib.Path(pdf_path).resolve())
    filename = pathlib.Path(pdf_path).name
    folder   = pathlib.Path(pdf_path).parent.name

    logger.info(f"INGEST_START | file={filename!r} | folder={folder!r}")

    replaced = _delete_existing_chunks(filename)
    if replaced:
        logger.info(f"INGEST_DEDUP | file={filename!r} | removed_chunks={replaced}")

    try:
        result = DocumentConverter().convert(pdf_path)
        text   = result.document.export_to_markdown().strip()
    except Exception as e:
        logger.error(f"INGEST_FAIL | file={filename!r} | reason={e}")
        return 0, replaced

    if not text:
        logger.warning(f"INGEST_EMPTY | file={filename!r} | no content extracted")
        return 0, replaced

    chunks = [
        text[i:i + CHUNK_SIZE]
        for i in range(0, len(text), CHUNK_SIZE - CHUNK_OVERLAP)
        if text[i:i + CHUNK_SIZE].strip()
    ]

    metadata = {"filename": filename, "full_path": pdf_path, "folder": folder}
    vectorstore = get_vectorstore()
    vectorstore.add_texts(chunks, metadatas=[metadata] * len(chunks))

    logger.info(f"INGEST_DONE | file={filename!r} | chunks={len(chunks)} | replaced={replaced}")
    return len(chunks), replaced


def ingest_folder(folder_path: str = None) -> dict:
    """
    Ingest all PDFs found recursively under folder_path.
    Returns a summary dict: {filename: {"added": int, "replaced": int}}.
    """
    folder_path = folder_path or PDF_DIR
    summary     = {}

    pdfs = list(pathlib.Path(folder_path).rglob("*.pdf"))
    if not pdfs:
        logger.warning(f"INGEST_FOLDER_EMPTY | path={folder_path!r}")
        return summary

    logger.info(f"INGEST_FOLDER_START | path={folder_path!r} | files={len(pdfs)}")
    for pdf in pdfs:
        added, replaced = ingest(str(pdf))
        summary[pdf.name] = {"added": added, "replaced": replaced}

    total = sum(v["added"] for v in summary.values())
    logger.info(f"INGEST_FOLDER_DONE | files={len(summary)} | total_chunks={total}")
    return summary
