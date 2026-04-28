FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ── System packages ──────────────────────────────────────────────────────────
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
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# ── Make python3.11 the default ───────────────────────────────────────────────
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

# ── Install pip for python3.11 specifically ───────────────────────────────────
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 \
    && python3.11 -m pip install --upgrade pip

# ── Ollama ────────────────────────────────────────────────────────────────────
RUN curl -fsSL https://ollama.com/install.sh | sh

WORKDIR /app

COPY requirements.txt .

# Batch 1 — PyTorch + torchvision CPU (must be installed together)
RUN python3.11 -m pip install --no-cache-dir \
    torch torchvision \
    --index-url https://download.pytorch.org/whl/cpu

# Batch 2 — Pin transformers to a stable version, then sentence-transformers
RUN python3.11 -m pip install --no-cache-dir transformers==4.44.2
RUN python3.11 -m pip install --no-cache-dir sentence-transformers==3.3.1

# Batch 3 — Vector DB
RUN python3.11 -m pip install --no-cache-dir chromadb==0.5.23

# Batch 4 — LangChain core
RUN python3.11 -m pip install --no-cache-dir \
    langchain-core==1.3.0 \
    langchain-text-splitters==1.1.2

# Batch 5 — LangChain main + community
RUN python3.11 -m pip install --no-cache-dir \
    langchain==1.2.15 \
    langchain-community==0.4.1 \
    langchain-classic==1.0.4

# Batch 6 — LangChain integrations
RUN python3.11 -m pip install --no-cache-dir \
    langchain-ollama==1.1.0 \
    langchain-chroma==1.1.0 \
    langchain-huggingface==1.2.2

# Batch 7 — Docling (heavy, isolated)
RUN python3.11 -m pip install --no-cache-dir docling==2.15.0

# Batch 8 — PDF + UI
RUN python3.11 -m pip install --no-cache-dir \
    pdfplumber==0.11.4 \
    streamlit==1.40.2 \
    python-dotenv==1.0.1

# ── App source ────────────────────────────────────────────────────────────────
COPY . .

# ── Directories (volumes will mount over these at runtime) ────────────────────
RUN mkdir -p /app/pdfs/Uploads /app/chroma_data

# ── Supervisord config ────────────────────────────────────────────────────────
COPY supervisord.conf /etc/supervisor/conf.d/tgtransco.conf

# ── Entrypoint ────────────────────────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["/entrypoint.sh"]