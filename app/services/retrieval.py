"""Retrieval for chat: hybrid (FTS5 BM25 + sqlite-vec) fused with RRF, an optional
listwise LLM rerank, and a structured/aggregate path over the extracted tables.

Question shape is routed:
  - "aggregate" (action items / decisions / "list all" / a person's tasks) -> SQL over
    the ActionItem / Decision / Participant tables, which chunk-similarity handles poorly.
  - "pointed" (everything else) -> hybrid chunk retrieval + rerank.
Each path returns a uniform list of context items ({transcript_id, title, meeting_date,
content, ...}) so the chat layer can assemble + cite them identically.
"""
import json
import logging
import re
from typing import List, Optional, Sequence

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models import ActionItem, Decision, Participant, Transcript, TranscriptChunk
from app.services.embeddings import embed_query

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

try:
    from sqlite_vec import serialize_float32
except ImportError:  # pragma: no cover
    serialize_float32 = None


# --------------------------------------------------------------------------- #
# FTS5 query sanitising
# --------------------------------------------------------------------------- #
_QUOTE_FTS_RE = re.compile(r'\s+|(".*?")')


def quote_fts(query: str) -> str:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Wraps each token in double quotes so words like NOT/OR and punctuation lose their
    FTS operator meaning; multi-word queries become implicit-AND. (Adapted from
    simonw/sqlite-utils quote_fts.)
    """
    if not query or not query.strip():
        return ""
    if query.count('"') % 2:  # unbalanced quote -> close it
        query += '"'
    bits = _QUOTE_FTS_RE.split(query)
    bits = [b for b in bits if b and b != '""']
    return " ".join(b if b.startswith('"') else f'"{b}"' for b in bits)


_FTS_WORD_RE = re.compile(r"\w+", re.UNICODE)


def search_transcripts_fts(db: Session, query: str, limit: int = 20) -> List[int]:
    """BM25-ranked transcript ids from the (already-synced) transcripts_fts index.

    Uses per-token PREFIX matching ("foo"*) so the live recall box still matches
    partial typing (e.g. "paym" -> "payment") the way the old substring search did.
    Column order is (title, content_clean, summary); title/summary weighted above body.
    Returns [] for an empty/whitespace query.
    """
    tokens = _FTS_WORD_RE.findall(query or "")
    if not tokens:
        return []
    match = " ".join(f'"{tok}"*' for tok in tokens)
    rows = db.connection().exec_driver_sql(
        "SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH ? "
        "ORDER BY bm25(transcripts_fts, 10.0, 1.0, 5.0) LIMIT ?",
        (match, limit),
    ).fetchall()
    return [r[0] for r in rows]


# --------------------------------------------------------------------------- #
# Retrievers
# --------------------------------------------------------------------------- #
def lexical_search(db: Session, query: str, k: int) -> List[int]:
    """BM25-ranked chunk ids from the FTS5 index (best first)."""
    match = quote_fts(query)
    if not match:
        return []
    rows = db.connection().exec_driver_sql(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
        (match, k),
    ).fetchall()
    return [r[0] for r in rows]


async def dense_search(db: Session, query: str, k: int) -> List[int]:
    """Semantic nearest-neighbour chunk ids from sqlite-vec (best first)."""
    if serialize_float32 is None:
        return []
    vec = await embed_query(query)
    if not vec:
        return []
    rows = db.connection().exec_driver_sql(
        "SELECT chunk_id FROM vec_chunks WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (serialize_float32(vec), k),
    ).fetchall()
    return [r[0] for r in rows]


def rrf_fuse(ranked_lists: Sequence[Sequence[int]], k: int = 60) -> List[tuple]:
    """Reciprocal Rank Fusion: score(d) = sum 1/(k + rank), 1-based ranks. Sorted desc."""
    scores: dict = {}
    for ranked in ranked_lists:
        for i, doc_id in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + i + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _load_chunks(db: Session, chunk_ids: Sequence[int]) -> List[dict]:
    """Load chunk + parent-transcript metadata, preserving the order of chunk_ids."""
    if not chunk_ids:
        return []
    rows = (
        db.query(TranscriptChunk, Transcript)
        .join(Transcript, TranscriptChunk.transcript_id == Transcript.id)
        .filter(TranscriptChunk.id.in_(list(chunk_ids)))
        .all()
    )
    by_id = {
        ch.id: {
            "chunk_id": ch.id,
            "transcript_id": t.id,
            "title": t.title or t.filename,
            "meeting_date": t.meeting_date,
            "speaker": ch.speaker,
            "content": ch.content,
        }
        for ch, t in rows
    }
    return [by_id[cid] for cid in chunk_ids if cid in by_id]


# --------------------------------------------------------------------------- #
# Routing + structured/aggregate path
# --------------------------------------------------------------------------- #
_AGG_RE = re.compile(
    r"\b(all|every|each|list|how many|count|outstanding|pending|"
    r"action items?|to-?dos?|tasks?|decisions?|assigned|responsible|owns?)\b",
    re.IGNORECASE,
)


def route(query: str) -> str:
    """Classify a question as 'aggregate' (structured-table answerable) or 'pointed'."""
    return "aggregate" if _AGG_RE.search(query or "") else "pointed"


def _mentioned_participants(db: Session, query: str) -> List[str]:
    ql = (query or "").lower()
    names = [n for (n,) in db.query(Participant.name).all() if n]
    return [n for n in names if n.lower() in ql]


def structured_answer_context(db: Session, query: str, limit: int = 50) -> List[dict]:
    """Pull matching rows from the Decision / ActionItem tables as context items."""
    ql = (query or "").lower()
    mentioned = _mentioned_participants(db, query)
    items: List[dict] = []

    if "decision" in ql:
        rows = (
            db.query(Decision)
            .options(joinedload(Decision.transcript))
            .order_by(Decision.created_at.desc())
            .limit(limit)
            .all()
        )
        for d in rows:
            t = d.transcript
            items.append({
                "transcript_id": t.id,
                "title": t.title or t.filename,
                "meeting_date": t.meeting_date,
                "content": f"[decision · {d.category}] {d.text}",
            })
        return items

    # Default aggregate target: action items (optionally scoped to a named assignee).
    q = db.query(ActionItem).options(joinedload(ActionItem.transcript))
    if mentioned:
        q = q.filter(or_(*[ActionItem.assignee.ilike(f"%{n}%") for n in mentioned]))
    for ai in q.order_by(ActionItem.created_at.desc()).limit(limit).all():
        t = ai.transcript
        status = "done" if ai.completed else "open"
        assignee = f" · @{ai.assignee}" if ai.assignee else ""
        items.append({
            "transcript_id": t.id,
            "title": t.title or t.filename,
            "meeting_date": t.meeting_date,
            "content": f"[action item · {status}{assignee}] {ai.text}",
        })
    return items


# --------------------------------------------------------------------------- #
# Listwise LLM rerank
# --------------------------------------------------------------------------- #
_RERANK_SYSTEM = (
    "You are a search-result reranker. Given a query and numbered passages, order the "
    "passage ids from MOST to LEAST relevant to the query. Judge only topical relevance. "
    'Respond with ONLY JSON: {"ranking": [<id>, <id>, ...]} containing every id exactly once.'
)


async def llm_rerank(query: str, chunks: List[dict], top_k: int) -> List[dict]:
    """Reorder chunks with one cheap listwise LLM call; falls back to input order."""
    settings = get_settings()
    if not settings.openrouter_api_key or len(chunks) <= 1:
        return chunks[:top_k]

    listing = "\n".join(f"[{i + 1}] {c['content'][:500]}" for i, c in enumerate(chunks))
    user = f"Query: {query}\n\nPassages:\n{listing}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                OPENROUTER_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://transcript-hub.local",
                    "X-Title": "Transcript Hub",
                },
                json={
                    "model": settings.rerank_model,
                    "messages": [
                        {"role": "system", "content": _RERANK_SYSTEM},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        ranking = _parse_ranking(content, len(chunks))
        order = ranking + [i for i in range(len(chunks)) if i not in set(ranking)]
        return [chunks[i] for i in order][:top_k]
    except Exception:
        logger.exception("LLM rerank failed; using fused order")
        return chunks[:top_k]


def _parse_ranking(content: str, n: int) -> List[int]:
    """Extract zero-based, de-duplicated, in-range indices from the model's JSON reply."""
    content = re.sub(r"^```json\s*|\s*```$", "", content.strip())
    match = re.search(r"\{.*\}", content, re.DOTALL)
    data = json.loads(match.group(0) if match else content)
    seen, order = set(), []
    for x in data.get("ranking", []):
        try:
            i = int(x) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and i not in seen:
            seen.add(i)
            order.append(i)
    return order


# --------------------------------------------------------------------------- #
# Top-level entry point
# --------------------------------------------------------------------------- #
async def retrieve(db: Session, query: str, top_k: Optional[int] = None) -> tuple:
    """Return (mode, context_items) for a question.

    mode is 'aggregate' or 'pointed'. context_items are uniform dicts with at least
    {transcript_id, title, meeting_date, content} for the chat layer to cite.
    """
    settings = get_settings()
    top_k = top_k or settings.retrieval_top_k
    mode = route(query)

    if mode == "aggregate":
        items = structured_answer_context(db, query)
        if items:
            return mode, items
        # Nothing structured matched -> fall back to hybrid chunk retrieval.

    candidates = settings.retrieval_candidates
    lexical = lexical_search(db, query, candidates)
    dense = await dense_search(db, query, candidates)
    fused_ids = [cid for cid, _score in rrf_fuse([lexical, dense])][:candidates]
    chunks = _load_chunks(db, fused_ids)

    if settings.rerank_enabled and len(chunks) > top_k:
        chunks = await llm_rerank(query, chunks, top_k)
    else:
        chunks = chunks[:top_k]
    return "pointed", chunks
