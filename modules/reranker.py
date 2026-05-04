import os
import time

import defaults
from log_config import setup_logger

logger = setup_logger("reranker")

RERANKER_TOP_N = int(os.getenv("RERANKER_TOP_N", defaults.RERANKER_TOP_N))


def rerank(question: str, docs: list) -> list:
    """
    Rerank retrieved docs using a cross-encoder model.
    Returns the top RERANKER_TOP_N docs sorted by relevance score.
    """
    from pipeline import reranker as _model

    if not docs:
        return docs

    logger.info(f"RERANK_START | candidates={len(docs)}")
    t0 = time.time()

    pairs  = [(question, doc.page_content) for doc in docs]
    scores = _model.predict(pairs)

    ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
    top    = [doc for _, doc in ranked[:RERANKER_TOP_N]]

    logger.info(
        f"RERANK_DONE | elapsed={round(time.time() - t0, 3)}s"
        f" | kept={len(top)}/{len(docs)}"
        f" | top_score={round(float(ranked[0][0]), 4) if ranked else 'n/a'}"
    )
    return top
