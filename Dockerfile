# ─────────────────────────────────────────────────────────────────────────────
# Base image
# ─────────────────────────────────────────────────────────────────────────────
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ─────────────────────────────────────────────────────────────────────────────
# System packages
# ─────────────────────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    curl \
    wget \
    zstd \
    git \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────────────────────
# Python — make 3.11 the default
# ─────────────────────────────────────────────────────────────────────────────
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python  python  /usr/bin/python3.11 1 \
    && curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 \
    && python3.11 -m pip install --upgrade pip

# ─────────────────────────────────────────────────────────────────────────────
# Ollama
# ─────────────────────────────────────────────────────────────────────────────
RUN curl -fsSL https://ollama.com/install.sh | sh

# ─────────────────────────────────────────────────────────────────────────────
# Python dependencies  (ordered: heaviest / most constrained first)
# ─────────────────────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .

# Batch 1 — PyTorch CPU (must install before sentence-transformers and langchain-huggingface
# so pip selects the CPU wheel rather than the default CUDA wheel)
RUN python3.11 -m pip install --no-cache-dir \
    "torch==2.4.0+cpu" \
    --index-url https://download.pytorch.org/whl/cpu

# Batch 2 — Transformers (pinned so sentence-transformers doesn't pull an incompatible version)
RUN python3.11 -m pip install --no-cache-dir \
    transformers==4.44.2

# Batch 3 — Sentence-transformers (embedding + reranker models)
RUN python3.11 -m pip install --no-cache-dir \
    sentence-transformers==3.3.1

# Batch 4 — Vector store
RUN python3.11 -m pip install --no-cache-dir \
    chromadb==0.5.23

# Batch 5 — LangChain (only packages imported by the app)
RUN python3.11 -m pip install --no-cache-dir \
    langchain-core==1.3.0 \
    langchain-ollama==1.1.0 \
    langchain-chroma==1.1.0 \
    langchain-huggingface==1.2.2

# Batch 6 — PDF tooling + UI
# PDF parsing is handled by the unstructured-api sidecar — only requests needed here.
RUN python3.11 -m pip install --no-cache-dir \
    requests \
    pymupdf \
    openpyxl==3.1.5 \
    streamlit==1.40.2 \
    python-dotenv==1.0.1

# ─────────────────────────────────────────────────────────────────────────────
# App source
# ─────────────────────────────────────────────────────────────────────────────
COPY . .

# ─────────────────────────────────────────────────────────────────────────────
# Runtime directories (volumes mount over these)
# ─────────────────────────────────────────────────────────────────────────────
RUN mkdir -p /app/pdfs/Uploads /app/chroma_data

# ─────────────────────────────────────────────────────────────────────────────
# Supervisor + entrypoint
# ─────────────────────────────────────────────────────────────────────────────
COPY supervisord.conf /etc/supervisor/conf.d/tgtransco.conf
COPY entrypoint.sh    /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8501
ENTRYPOINT ["/entrypoint.sh"]