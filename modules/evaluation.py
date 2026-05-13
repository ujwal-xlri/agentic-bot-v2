"""Chunking quality evaluation — heuristic + LLM-based (synthetic QA).

Scoring breakdown:
  Composite (0-100) = 40% × structural_score + 60% × retrieval_score

  Structural (0-1):
    If source has tables:  0.5×(1-short_ratio) + 0.3×heading_rate + 0.2×table_coverage
    No tables in doc:      0.6×(1-short_ratio) + 0.4×heading_rate

  Retrieval (0-1):  0.6×hit_rate + 0.4×mrr
"""

import os
import re
import json
import statistics
import pathlib
import defaults

MIN_CHUNK_CHARS = int(os.getenv("MIN_CHUNK_CHARS", defaults.MIN_CHUNK_CHARS))

# ─────────────────────────────────────────────────────────────────────────────
# Source text extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_source_info(pdf_path: str) -> tuple[str, int, int]:
    """
    Extract (full_text, table_count, heading_count) from a PDF via pdfplumber.
    Returns ("", 0, 0) on failure — callers fall back to chunk text.
    """
    import pdfplumber

    full_text = ""
    table_count = 0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                full_text += text + "\n"
                tables = page.extract_tables() or []
                # Only count tables with more than a header row
                table_count += sum(1 for t in tables if t and len(t) > 1)
    except Exception:
        return "", 0, 0

    heading_count = _count_source_headings(full_text)
    return full_text.strip(), table_count, heading_count


def _count_source_headings(text: str) -> int:
    """Heuristic heading detection on plain pdfplumber text."""
    count = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 100:
            continue
        # Numbered sections: "1.", "1.2 Section", "1.2.3 Sub-section"
        if re.match(r"^\d+(\.\d+)*\.?\s+[A-Z]", line):
            count += 1
        # ALL-CAPS short lines (likely headings, not page numbers)
        elif line.isupper() and 4 <= len(line) <= 80 and not line.isdigit():
            count += 1
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Structural metrics
# ─────────────────────────────────────────────────────────────────────────────

def structural_metrics(chunks: list[dict], source_table_count: int, source_heading_count: int) -> dict:
    """
    Compute structural quality metrics from already-indexed chunks.

    chunks: list of {text, filename, page, headings, ...}
    """
    if not chunks:
        return {
            "total_chunks": 0,
            "avg_chars": 0.0, "std_chars": 0.0,
            "min_chars": 0, "max_chars": 0,
            "short_chunk_ratio": 0.0,
            "table_coverage": None,
            "heading_rate": 0.0,
            "chunks_with_tables": 0,
            "chunks_with_headings": 0,
            "source_tables": source_table_count,
            "source_headings": source_heading_count,
        }

    sizes = [len(c.get("text", "")) for c in chunks]
    short = sum(1 for s in sizes if s < MIN_CHUNK_CHARS)

    # Table detection: chunks that contain pipe-delimited markdown table rows
    chunks_with_tables = sum(1 for c in chunks if _has_table(c.get("text", "")))
    if source_table_count > 0:
        table_coverage = min(chunks_with_tables / source_table_count, 1.0)
    else:
        table_coverage = None  # N/A — no tables in source doc

    # Heading rate: % of chunks with non-empty heading metadata
    chunks_with_headings = sum(1 for c in chunks if (c.get("headings") or "").strip())
    heading_rate = chunks_with_headings / len(chunks)

    return {
        "total_chunks": len(chunks),
        "avg_chars": round(statistics.mean(sizes), 1),
        "std_chars": round(statistics.stdev(sizes) if len(sizes) > 1 else 0.0, 1),
        "min_chars": min(sizes),
        "max_chars": max(sizes),
        "short_chunk_ratio": round(short / len(chunks), 4),
        "table_coverage": round(table_coverage, 4) if table_coverage is not None else None,
        "heading_rate": round(heading_rate, 4),
        "chunks_with_tables": chunks_with_tables,
        "chunks_with_headings": chunks_with_headings,
        "source_tables": source_table_count,
        "source_headings": source_heading_count,
    }


def _has_table(text: str) -> bool:
    """Return True if text contains at least two pipe-delimited table rows."""
    table_rows = sum(
        1 for line in text.splitlines()
        if line.strip().startswith("|") and line.count("|") >= 2
    )
    return table_rows >= 2


# ─────────────────────────────────────────────────────────────────────────────
# LLM: synthetic question generation
# ─────────────────────────────────────────────────────────────────────────────

_QUESTION_PROMPT = (
    "You are a question generator for a document QA system. "
    "Given the document excerpt below, produce exactly {n} factual questions "
    "that a user might ask, each answerable from the text. "
    "Output ONLY a valid JSON array of question strings — no preamble, no explanation.\n\n"
    "Document excerpt:\n{text}\n\n"
    'Output: ["question 1", "question 2", ...]'
)


def generate_questions(source_text: str, n: int, llm) -> list[str]:
    """Use the LLM to generate n factual questions from source_text."""
    excerpt = source_text[:3500]
    # Build prompt via concatenation — source text may contain { } which breaks .format()
    prompt = (
        _QUESTION_PROMPT
        .replace("{n}", str(n))
        .replace("{text}", excerpt)
    )
    try:
        response = llm.invoke(prompt)
        # Look for a JSON array anywhere in the response
        match = re.search(r"\[.*?\]", response, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            questions = [str(q).strip() for q in parsed if q and str(q).strip()]
            if questions:
                return questions[:n]
    except Exception:
        pass
    return []


# ─────────────────────────────────────────────────────────────────────────────
# LLM: retrieval quality evaluation
# ─────────────────────────────────────────────────────────────────────────────

_JUDGE_PROMPT = (
    "Does the CONTEXT below contain enough information to answer the QUESTION?\n"
    "Reply with only the word YES or NO — nothing else.\n\n"
    "QUESTION: {question}\n\n"
    "CONTEXT:\n{context}\n\n"
    "Answer:"
)

_ANSWER_PROMPT = (
    "Answer the QUESTION below using only the CONTEXT provided. "
    "Be concise and factual. If the context does not contain the answer, say so.\n\n"
    "QUESTION: {question}\n\n"
    "CONTEXT:\n{context}\n\n"
    "Answer:"
)


def _judge(question: str, context_text: str, llm) -> bool:
    # Use .replace() — chunk text may contain { } which breaks .format()
    prompt = (
        _JUDGE_PROMPT
        .replace("{question}", question)
        .replace("{context}", context_text)
    )
    try:
        raw = llm.invoke(prompt).strip()
        upper = raw.upper()
        if upper.startswith("YES"):
            return True
        if upper.startswith("NO"):
            return False
        head = upper[:80]
        if re.search(r'\bYES\b|"answerable"\s*:\s*true', head):
            return True
        return False
    except Exception:
        return False


def _generate_answer(question: str, context_text: str, llm) -> str:
    prompt = (
        _ANSWER_PROMPT
        .replace("{question}", question)
        .replace("{context}", context_text)
    )
    try:
        return llm.invoke(prompt).strip()
    except Exception as e:
        return f"(error generating answer: {e})"


def retrieval_score(
    questions: list[str],
    vectorstore,
    reranker,
    filename: str,
    llm,
    k: int = 5,
    top_n: int = 4,
) -> dict:
    """
    For each question: retrieve top-k chunks (filtered to filename), rerank,
    judge answerability, and generate an LLM answer.

    Returns: {hit_rate, mrr, total_seconds, per_question: [{question, hit, rank,
              answer, context, elapsed_seconds, error?}]}
    """
    import time as _time

    per_q: list[dict] = []
    total_start = _time.time()

    for q in questions:
        q_start = _time.time()
        try:
            docs = vectorstore.similarity_search(q, k=k, filter={"filename": filename})
            if not docs:
                per_q.append({
                    "question": q, "hit": False, "rank": None,
                    "answer": "No chunks retrieved for this question.",
                    "context": "",
                    "elapsed_seconds": round(_time.time() - q_start, 2),
                })
                continue

            texts = [d.page_content for d in docs]
            scores = reranker.predict([(q, t) for t in texts])
            ranked_texts = [
                t for _, t in sorted(zip(scores, texts), reverse=True)
            ][:top_n]

            context = "\n\n---\n\n".join(ranked_texts)
            answered = _judge(q, context, llm)
            answer   = _generate_answer(q, context, llm)

            per_q.append({
                "question":        q,
                "hit":             answered,
                "rank":            1 if answered else None,
                "answer":          answer,
                "context":         context,
                "elapsed_seconds": round(_time.time() - q_start, 2),
            })

        except Exception as e:
            per_q.append({
                "question": q, "hit": False, "rank": None,
                "answer": "", "context": "",
                "elapsed_seconds": round(_time.time() - q_start, 2),
                "error": str(e),
            })

    total_seconds = round(_time.time() - total_start, 2)
    n = len(per_q)
    if n == 0:
        return {"hit_rate": 0.0, "mrr": 0.0, "total_seconds": 0.0, "per_question": []}

    hit_rate = sum(1 for r in per_q if r["hit"]) / n
    mrr = sum(1.0 / r["rank"] for r in per_q if r.get("rank")) / n

    return {
        "hit_rate":      round(hit_rate, 4),
        "mrr":           round(mrr, 4),
        "total_seconds": total_seconds,
        "per_question":  per_q,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Composite score
# ─────────────────────────────────────────────────────────────────────────────

def composite_score(structural: dict, retrieval: dict) -> tuple[float, dict]:
    """
    Returns (score_0_to_100, breakdown_dict).

    Weights: 40% structural, 60% retrieval.
    """
    short_ratio = structural.get("short_chunk_ratio", 0.0)
    heading_rate = structural.get("heading_rate", 0.0)
    table_cov = structural.get("table_coverage")  # None means N/A

    quality = 1.0 - short_ratio  # penalise lots of tiny chunks

    if table_cov is not None:
        s_score = 0.5 * quality + 0.3 * heading_rate + 0.2 * table_cov
    else:
        s_score = 0.6 * quality + 0.4 * heading_rate

    s_score = max(0.0, min(1.0, s_score))

    r_score = 0.6 * retrieval.get("hit_rate", 0.0) + 0.4 * retrieval.get("mrr", 0.0)
    r_score = max(0.0, min(1.0, r_score))

    composite = (0.4 * s_score + 0.6 * r_score) * 100

    return round(composite, 1), {
        "structural_score": round(s_score * 100, 1),
        "retrieval_score": round(r_score * 100, 1),
    }


def score_grade(score: float) -> tuple[str, str]:
    """Return (letter_grade, color_hex) for a 0–100 score."""
    if score >= 90:
        return "A", "#62bc62"
    if score >= 80:
        return "B", "#74b4e8"
    if score >= 70:
        return "C", "#c67605"
    if score >= 60:
        return "D", "#e07c3e"
    return "F", "#ef4444"
