import os
import time

from langchain_classic.chains.question_answering import load_qa_chain
import defaults
from log_config import setup_logger

logger = setup_logger("query")

RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", defaults.RETRIEVAL_K))


def query(question: str) -> dict:
    """
    Run a RAG query. Returns dict with 'answer', 'sources', and 'elapsed'.
    Sources is a list of dicts: {filename, page_number, full_path, folder}.
    """
    from pipeline import llm, get_vectorstore

    vectorstore = get_vectorstore()
    retriever   = vectorstore.as_retriever(search_kwargs={"k": RETRIEVAL_K})

    logger.info(f"QUERY_START | q={question!r}")
    total_start = time.time()

    # Stage 1: embed question + similarity search
    t0   = time.time()
    docs = retriever.invoke(question)
    logger.info(f"QUERY_RETRIEVAL | elapsed={round(time.time() - t0, 3)}s | chunks={len(docs)}")

    # Stage 2: LLM inference
    t1     = time.time()
    chain  = load_qa_chain(llm, chain_type="stuff")
    result = chain.invoke({"input_documents": docs, "question": question})
    logger.info(f"QUERY_LLM | elapsed={round(time.time() - t1, 3)}s")

    elapsed = round(time.time() - total_start, 1)

    seen    = set()
    sources = []
    for doc in docs:
        m   = doc.metadata
        key = (m.get("filename", ""), m.get("page", ""))
        if key not in seen:
            seen.add(key)
            sources.append({
                "filename":    m.get("filename", "unknown"),
                "page_number": m.get("page", "?"),
                "full_path":   m.get("full_path",   ""),
                "folder":      m.get("folder",      ""),
            })

    logger.info(f"QUERY_DONE | elapsed={elapsed}s | sources={len(sources)}")
    return {
        "answer":  result["output_text"],
        "sources": sources,
        "elapsed": elapsed,
    }
