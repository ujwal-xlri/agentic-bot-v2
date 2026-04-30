import os
import sys
import defaults
from log_config import setup_logger

logger = setup_logger("pipeline")

from langchain_ollama import OllamaLLM
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
import chromadb

# ── Config from environment ───────────────────────────────────────────────────
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL",   defaults.OLLAMA_MODEL)
OLLAMA_HOST    = os.getenv("OLLAMA_HOST",    defaults.OLLAMA_HOST)
OLLAMA_PORT    = os.getenv("OLLAMA_PORT",    defaults.OLLAMA_PORT)
CHROMA_HOST    = os.getenv("CHROMA_HOST",    defaults.CHROMA_HOST)
CHROMA_PORT    = int(os.getenv("CHROMA_PORT", defaults.CHROMA_PORT))
PDF_DIR        = os.getenv("PDF_DIR",        defaults.PDF_DIR)
COLLECTION     = os.getenv("COLLECTION_NAME", defaults.COLLECTION_NAME)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", defaults.EMBEDDING_MODEL)

# ── Models (initialised once, reused by app.py via import) ───────────────────
logger.info(f"PIPELINE_INIT | loading embedding model={EMBEDDING_MODEL!r}")
embedder = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

logger.info(f"PIPELINE_INIT | connecting to Ollama model={OLLAMA_MODEL!r}")
llm = OllamaLLM(
    model=OLLAMA_MODEL,
    base_url=f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"
)

logger.info(f"PIPELINE_INIT | connecting to ChromaDB host={CHROMA_HOST}:{CHROMA_PORT}")
chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)


# ── Helpers ───────────────────────────────────────────────────────────────────
_vectorstore = Chroma(
    client=chroma_client,
    collection_name=COLLECTION,
    embedding_function=embedder
)

def get_vectorstore():
    return _vectorstore

from modules.ingestion import ingest, ingest_folder  # noqa: F401
from modules.query import query  # noqa: F401


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