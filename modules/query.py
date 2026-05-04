import os
import time

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
import defaults
from log_config import setup_logger

logger = setup_logger("query")

RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", defaults.RETRIEVAL_K))

_RAG_PROMPT = PromptTemplate.from_template(
    "Use the following pieces of context to answer the question at the end. "
    "If you don't know the answer, just say that you don't know.\n\n"
    "Context:\n{context}\n\n"
    "Question: {question}\n\n"
    "Helpful Answer:"
)


def _format_docs(docs: list) -> str:
    return "\n\n".join(doc.page_content for doc in docs)


def _extract_sources(docs: list) -> list:
    seen = set()
    sources = []
    for doc in docs:
        m = doc.metadata
        key = (m.get("filename", ""), m.get("page", ""))
        if key not in seen:
            seen.add(key)
            sources.append({
                "filename":    m.get("filename", "unknown"),
                "page_number": m.get("page", "?"),
                "full_path":   m.get("full_path", ""),
                "folder":      m.get("folder", ""),
            })
    return sources


def _retrieve_and_rerank(question: str):
    from pipeline import get_vectorstore
    vectorstore = get_vectorstore()
    retriever = vectorstore.as_retriever(search_kwargs={"k": RETRIEVAL_K})

    logger.info(f"QUERY_START | q={question!r}")
    total_start = time.time()

    t0 = time.time()
    docs = retriever.invoke(question)
    logger.info(f"QUERY_RETRIEVAL | elapsed={round(time.time() - t0, 3)}s | chunks={len(docs)}")

    from modules.reranker import rerank
    docs = rerank(question, docs)

    return docs, total_start


def query(question: str) -> dict:
    """
    Run a RAG query. Returns dict with 'answer', 'sources', and 'elapsed'.
    Sources is a list of dicts: {filename, page_number, full_path, folder}.
    """
    from pipeline import llm
    docs, total_start = _retrieve_and_rerank(question)

    chain = _RAG_PROMPT | llm | StrOutputParser()

    t1 = time.time()
    answer = chain.invoke({"context": _format_docs(docs), "question": question})
    logger.info(f"QUERY_LLM | elapsed={round(time.time() - t1, 3)}s")

    elapsed = round(time.time() - total_start, 1)
    sources = _extract_sources(docs)
    logger.info(f"QUERY_DONE | elapsed={elapsed}s | sources={len(sources)}")
    return {"answer": answer, "sources": sources, "elapsed": elapsed}


def query_stream(question: str):
    """
    Retrieve + rerank synchronously, then return a streaming token iterator.
    Returns (sources, token_iterator, start_time) so the caller can measure
    total elapsed (retrieval + rerank + LLM generation) after consuming the stream.
    """
    from pipeline import llm
    docs, total_start = _retrieve_and_rerank(question)

    chain = _RAG_PROMPT | llm | StrOutputParser()
    sources = _extract_sources(docs)
    token_iter = chain.stream({"context": _format_docs(docs), "question": question})

    return sources, token_iter, total_start
