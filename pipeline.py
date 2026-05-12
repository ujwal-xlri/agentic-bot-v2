import os
import sys
import defaults
from log_config import setup_logger

logger = setup_logger("pipeline")

from langchain_ollama import OllamaLLM
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder
import chromadb

# ── Config from environment ───────────────────────────────────────────────────
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",    defaults.OLLAMA_MODEL)
OLLAMA_HOST     = os.getenv("OLLAMA_HOST",     defaults.OLLAMA_HOST)
OLLAMA_PORT     = os.getenv("OLLAMA_PORT",     defaults.OLLAMA_PORT)
CHROMA_HOST     = os.getenv("CHROMA_HOST",     defaults.CHROMA_HOST)
CHROMA_PORT     = int(os.getenv("CHROMA_PORT", defaults.CHROMA_PORT))
PDF_DIR         = os.getenv("PDF_DIR",         defaults.PDF_DIR)
COLLECTION      = os.getenv("COLLECTION_NAME", defaults.COLLECTION_NAME)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", defaults.EMBEDDING_MODEL)
RERANKER_MODEL  = os.getenv("RERANKER_MODEL",  defaults.RERANKER_MODEL)

# ── Lazy singletons — initialised on first use ────────────────────────────────
_embedder      = None
_reranker      = None
_llm           = None
_chroma_client = None
_vectorstore   = None


def get_embedder():
    global _embedder
    if _embedder is None:
        logger.info(f"PIPELINE_INIT | loading embedding model={EMBEDDING_MODEL!r}")
        _embedder = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    return _embedder


def get_reranker():
    global _reranker
    if _reranker is None:
        logger.info(f"PIPELINE_INIT | loading reranker model={RERANKER_MODEL!r}")
        _reranker = CrossEncoder(RERANKER_MODEL)
    return _reranker


def get_llm():
    global _llm
    if _llm is None:
        logger.info(f"PIPELINE_INIT | connecting to Ollama model={OLLAMA_MODEL!r}")
        _llm = OllamaLLM(
            model=OLLAMA_MODEL,
            base_url=f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"
        )
    return _llm


def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        logger.info(f"PIPELINE_INIT | connecting to ChromaDB host={CHROMA_HOST}:{CHROMA_PORT}")
        _chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    return _chroma_client


def get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = Chroma(
            client=get_chroma_client(),
            collection_name=COLLECTION,
            embedding_function=get_embedder()
        )
    return _vectorstore


def reset_vectorstore() -> None:
    global _vectorstore
    _vectorstore = None


def query_models_ready() -> bool:
    return all(x is not None for x in [_embedder, _reranker, _llm, _vectorstore])


from modules.ingestion import ingest, ingest_folder  # noqa: F401
from modules.query import query                      # noqa: F401
from modules.reranker import rerank                  # noqa: F401


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python pipeline.py ingest <path-to-pdf>")
        print("  python pipeline.py ingest_folder [optional-folder-path]")
        print("  python pipeline.py query <your question>")
        sys.exit(1)

    if sys.argv[1] == "ingest":
        ingest(sys.argv[2])

    elif sys.argv[1] == "ingest_folder":
        folder = sys.argv[2] if len(sys.argv) > 2 else None
        summary = ingest_folder(folder)
        print("\nIngestion summary:")
        for name, count in summary.items():
            print(f"  {name}: {count} chunks")

    elif sys.argv[1] == "query":
        result = query(" ".join(sys.argv[2:]))
        print("\nAnswer:", result["answer"])
        print("\nSources:")
        for s in result["sources"]:
            print(f"  [{s['folder']}] {s['filename']} — page {s['page_number']}")
