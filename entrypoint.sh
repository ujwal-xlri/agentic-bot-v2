#!/bin/bash
set -e

OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.1:8b}"
OLLAMA_URL="http://localhost:11434"
CHROMA_URL="http://localhost:8000"

log() { echo "[entrypoint] $(date '+%H:%M:%S') — $*"; }

# ── Start supervisord in background (starts Ollama + ChromaDB) ────────────────
log "Starting supervisord (Ollama + ChromaDB)..."
/usr/bin/supervisord -c /etc/supervisor/conf.d/tgtransco.conf &
SUPERVISORD_PID=$!

# ── Wait for Ollama ───────────────────────────────────────────────────────────
log "Waiting for Ollama to be ready..."
for i in $(seq 1 60); do
    if curl -sf "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
        log "Ollama is ready."
        break
    fi
    if [ "$i" -eq 60 ]; then
        log "ERROR: Ollama did not start in time. Exiting."
        exit 1
    fi
    sleep 2
done

# ── Pull model if not already present ────────────────────────────────────────
log "Checking for model: ${OLLAMA_MODEL}..."
if ! ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL}"; then
    log "Model not found. Pulling ${OLLAMA_MODEL} — this may take a few minutes on first run..."
    ollama pull "${OLLAMA_MODEL}"
    log "Model pull complete."
else
    log "Model ${OLLAMA_MODEL} already present. Skipping pull."
fi

# ── Wait for ChromaDB (try multiple endpoints for 0.5.x compatibility) ────────
log "Waiting for ChromaDB to be ready..."
for i in $(seq 1 60); do
    if curl -sf "${CHROMA_URL}/api/v2/heartbeat" > /dev/null 2>&1; then
        log "ChromaDB is ready (v2 API)."
        break
    fi
    if curl -sf "${CHROMA_URL}/api/v1/heartbeat" > /dev/null 2>&1; then
        log "ChromaDB is ready (v1 API)."
        break
    fi
    if curl -sf "${CHROMA_URL}/healthz" > /dev/null 2>&1; then
        log "ChromaDB is ready (healthz)."
        break
    fi
    if [ "$i" -eq 60 ]; then
        log "ERROR: ChromaDB did not start in time. Exiting."
        exit 1
    fi
    sleep 2
done

# ── Start Streamlit via supervisorctl ─────────────────────────────────────────
log "All services ready. Starting Streamlit..."
supervisorctl -c /etc/supervisor/conf.d/tgtransco.conf start streamlit

log "TGTRANSCO Bot is up. Open http://localhost:8501"

# ── Keep container alive ──────────────────────────────────────────────────────
wait $SUPERVISORD_PID