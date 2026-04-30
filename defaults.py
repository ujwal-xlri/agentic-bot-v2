# Default values for all configurable parameters.
# Source files use these as os.getenv() fallbacks — no hardcoded literals anywhere else.
# To override, set the corresponding environment variable (e.g., via docker-compose env_file).

# LLM
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_HOST  = "localhost"
OLLAMA_PORT  = "11434"

# Embeddings — fallback options: "BAAI/bge-small-en-v1.5", "all-MiniLM-L6-v2"
EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"

# Vector store
CHROMA_HOST     = "localhost"
CHROMA_PORT     = "8000"
COLLECTION_NAME = "tgtransco"

# Retrieval
CHUNK_SIZE  = "512"
RETRIEVAL_K = "8"

# Paths
PDF_DIR = "/app/pdfs"
LOG_DIR = "/app/logs"

# Logging
LOG_LEVEL        = "DEBUG"
LOG_BACKUP_COUNT = "30"
