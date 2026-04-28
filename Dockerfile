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
    # OCR engine + English language pack
    tesseract-ocr \
    tesseract-ocr-eng \
    # OpenCV / rendering deps
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    # PDF rasterisation (required by Docling for OCR page rendering)
    poppler-utils \
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

# Batch 1 — PyTorch CPU (must be installed before anything that depends on it)
RUN python3.11 -m pip install --no-cache-dir \
    torch \
    torchvision \
    --index-url https://download.pytorch.org/whl/cpu

# Batch 2 — Transformers (pinned before sentence-transformers pulls a newer one)
RUN python3.11 -m pip install --no-cache-dir \
    transformers==4.44.2

# Batch 3 — Sentence-transformers (embedding model used by HybridChunker)
RUN python3.11 -m pip install --no-cache-dir \
    sentence-transformers==3.3.1

# Batch 4 — Vector store
RUN python3.11 -m pip install --no-cache-dir \
    chromadb==0.5.23

# Batch 5 — LangChain stack
RUN python3.11 -m pip install --no-cache-dir \
    langchain-core==1.3.0 \
    langchain-text-splitters==1.1.2 \
    langchain==1.2.15 \
    langchain-community==0.4.1 \
    langchain-classic==1.0.4 \
    langchain-ollama==1.1.0 \
    langchain-chroma==1.1.0 \
    langchain-huggingface==1.2.2

# Batch 6 — Docling (heavy; isolated so cache-busting it doesn't re-run LangChain)
RUN python3.11 -m pip install --no-cache-dir \
    docling==2.15.0

# Batch 7 — PDF tooling + UI
RUN python3.11 -m pip install --no-cache-dir \
    pdfplumber==0.11.4 \
    streamlit==1.40.2 \
    python-dotenv==1.0.1

# ─────────────────────────────────────────────────────────────────────────────
# Pre-download Docling models at build time
# Avoids runtime downloads and ensures OCR works without internet access.
# Adds ~1-2 GB to the image but eliminates cold-start model fetching.
# ─────────────────────────────────────────────────────────────────────────────
RUN python3.11 -c "\
import os; \
os.environ['DOCLING_ARTIFACTS_PATH'] = '/app/.docling'; \
from docling.document_converter import DocumentConverter, PdfFormatOption; \
from docling.datamodel.pipeline_options import PdfPipelineOptions; \
from docling.datamodel.base_models import InputFormat; \
print('Downloading layout + table structure models...'); \
PdfPipelineOptions(); \
print('Downloading OCR models...'); \
opts = PdfPipelineOptions(); \
opts.do_ocr = True; \
opts.do_table_structure = True; \
DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}); \
print('All Docling models downloaded successfully.'); \
"

# Pin the model cache path so the runtime picks up what was baked in
ENV DOCLING_ARTIFACTS_PATH=/app/.docling

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