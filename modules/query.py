import time

from langchain_classic.chains.retrieval_qa.base import RetrievalQA
from log_config import setup_logger

logger = setup_logger("query")


def query(question: str) -> dict:
    """
    Run a RAG query. Returns dict with 'answer', 'sources', and 'elapsed'.
    Sources is a list of dicts: {filename, page_number, full_path, folder}.
    """
    from pipeline import llm, get_vectorstore

    vectorstore = get_vectorstore()
    retriever   = vectorstore.as_retriever(search_kwargs={"k": 8})

    qa = RetrievalQA.from_chain_type(
        llm=llm,
        retriever=retriever,
        return_source_documents=True
    )

    logger.info(f"QUERY_START | q={question!r}")
    start  = time.time()
    result = qa.invoke({"query": question})
    elapsed = round(time.time() - start, 1)

    seen    = set()
    sources = []
    for doc in result.get("source_documents", []):
        m   = doc.metadata
        key = (m.get("filename", ""), m.get("page_number", ""))
        if key not in seen:
            seen.add(key)
            sources.append({
                "filename":    m.get("filename",    "unknown"),
                "page_number": m.get("page_number", "?"),
                "full_path":   m.get("full_path",   ""),
                "folder":      m.get("folder",      ""),
            })

    logger.info(f"QUERY_DONE | elapsed={elapsed}s | sources={len(sources)}")
    return {
        "answer":  result["result"],
        "sources": sources,
        "elapsed": elapsed,
    }
