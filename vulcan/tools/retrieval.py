"""Knowledge-base retrieval (RAG) — TF-IDF over chunked manuals/SOPs/history.

Lightweight by design (no GPU, no external services) so judges can run it
anywhere. Every returned chunk carries provenance (doc name + chunk id) so
VULCAN can cite per Section 4B / 3B-T2 of the system prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from vulcan.config import KNOWLEDGE_BASE_DIR

CHUNK_SIZE = 900          # characters per chunk (approx a manual sub-section)
CHUNK_OVERLAP = 150


@dataclass
class Chunk:
    doc_name: str
    chunk_id: str
    text: str
    doc_type: str  # manual | sop | history | failure_report | other


def _classify_doc(name: str) -> str:
    n = name.lower()
    if "manual" in n:
        return "manual"        # -> Tier 3 evidence
    if "sop" in n:
        return "sop"           # -> Tier 4 evidence
    if "history" in n or "maintenance_record" in n:
        return "history"       # -> Tier 2 evidence
    if "failure" in n:
        return "failure_report"  # -> Tier 2 evidence
    return "other"


def _split_chunks(text: str) -> list[str]:
    # Prefer splitting on blank lines / section headers; fall back to windowing.
    paras = re.split(r"\n\s*\n", text)
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 2 <= CHUNK_SIZE:
            buf = (buf + "\n\n" + p).strip()
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= CHUNK_SIZE:
                buf = p
            else:  # hard-wrap very long paragraphs
                for i in range(0, len(p), CHUNK_SIZE - CHUNK_OVERLAP):
                    chunks.append(p[i : i + CHUNK_SIZE])
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


class KnowledgeBase:
    """In-memory TF-IDF index over data/knowledge_base/*.md|*.txt."""

    def __init__(self, kb_dir: Path = KNOWLEDGE_BASE_DIR):
        self.kb_dir = kb_dir
        self.chunks: list[Chunk] = []
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None
        self.reload()

    def reload(self) -> None:
        self.chunks = []
        for path in sorted(self.kb_dir.glob("*")):
            if path.suffix.lower() not in {".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            doc_type = _classify_doc(path.name)
            for i, c in enumerate(_split_chunks(text), start=1):
                self.chunks.append(
                    Chunk(doc_name=path.name, chunk_id=f"{path.stem}#c{i:02d}",
                          text=c, doc_type=doc_type)
                )
        if self.chunks:
            self._vectorizer = TfidfVectorizer(
                stop_words="english", ngram_range=(1, 2), sublinear_tf=True
            )
            self._matrix = self._vectorizer.fit_transform(
                [c.text for c in self.chunks]
            )
            self._bm25 = BM25Okapi(
                [c.text.lower().split() for c in self.chunks]
            )
        else:
            self._vectorizer, self._matrix, self._bm25 = None, None, None

    def search(self, query: str, top_k: int = 4) -> list[dict]:
        """Hybrid retrieval: TF-IDF cosine + BM25, reciprocal-rank fusion.

        Normalized weighted score fusion (TF-IDF 0.65 / BM25 0.35): both
        score vectors are max-normalized then weight-summed, preserving
        score magnitude — a chunk 6x more similar should rank 6x stronger,
        which rank-only fusion (RRF) discards. The bigram TF-IDF retriever
        captures phrase queries ("trip limit") so it carries more weight;
        BM25 adds exact-term recall. A chunk scored zero by BOTH retrievers
        is excluded (no forced citations for irrelevant queries).
        """
        if not self.chunks or self._vectorizer is None:
            return []
        qv = self._vectorizer.transform([query])
        tfidf = cosine_similarity(qv, self._matrix).ravel()
        bm25 = self._bm25.get_scores(query.lower().split())

        tf_max = float(tfidf.max()) or 1.0
        bm_max = float(max(bm25.max(), 0.0)) or 1.0
        fused = []
        for i in range(len(self.chunks)):
            if tfidf[i] <= 0.0 and bm25[i] <= 0.0:
                continue  # irrelevant to both retrievers
            score = (0.65 * float(tfidf[i]) / tf_max
                     + 0.35 * max(float(bm25[i]), 0.0) / bm_max)
            fused.append((score, i))
        fused.sort(key=lambda x: -x[0])

        results = []
        for rrf, idx in fused[:top_k]:
            ch = self.chunks[idx]
            results.append(
                {
                    "doc_name": ch.doc_name,
                    "chunk_id": ch.chunk_id,
                    "doc_type": ch.doc_type,
                    "similarity": round(float(tfidf[idx]), 3),
                    "bm25_score": round(float(bm25[idx]), 2),
                    "fusion_score": round(rrf, 4),
                    "text": ch.text,
                }
            )
        return results


def ingest_pdf(pdf_bytes: bytes, filename: str,
               kb_dir: Path = KNOWLEDGE_BASE_DIR) -> dict:
    """Extract text from an uploaded PDF and store it as an indexable .md.

    Keeps the original filename in the header so citations remain traceable
    to the source document (Section 4B-D2).
    """
    try:
        from io import BytesIO
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(pdf_bytes))
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            txt = (page.extract_text() or "").strip()
            if txt:
                pages.append(f"## [page {i}]\n\n{txt}")
        if not pages:
            return {"status": "NO_TEXT",
                    "message": "No extractable text (scanned PDF?). "
                               "OCR is out of scope for this prototype — "
                               "supply a text/markdown version."}
        stem = Path(filename).stem
        out = kb_dir / f"{stem}.md"
        out.write_text(f"# {filename} (ingested PDF)\n\n"
                       + "\n\n".join(pages), encoding="utf-8")
        get_kb().reload()
        return {"status": "OK", "stored_as": out.name,
                "pages_with_text": len(pages)}
    except Exception as exc:
        return {"status": "ERROR", "message": str(exc)}


_kb_singleton: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    global _kb_singleton
    if _kb_singleton is None:
        _kb_singleton = KnowledgeBase()
    return _kb_singleton


def search_knowledge_base(query: str, top_k: int = 4) -> dict:
    """Tool entrypoint called by the orchestrator."""
    results = get_kb().search(query, top_k=top_k)
    return {
        "query": query,
        "n_results": len(results),
        "results": results,
        "note": "doc_type→evidence tier: manual=Tier3, sop=Tier4, "
                "history/failure_report=Tier2 (per VULCAN Section 4B-D1).",
    }
