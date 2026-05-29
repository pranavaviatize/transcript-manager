"""Index transcripts into the chunk table + sqlite-vec vectors + FTS5 lexical table.

index_transcript() is idempotent (delete-then-insert per transcript), so it is safe to
call on every enrichment and to re-run in bulk via app.scripts.reindex.
"""
import asyncio
import logging
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.models import Transcript, TranscriptChunk
from app.services.chunker import chunk_transcript
from app.services.embeddings import embed_texts

logger = logging.getLogger(__name__)

try:
    from sqlite_vec import serialize_float32
except ImportError:  # pragma: no cover
    serialize_float32 = None


def _delete_existing(db: Session, transcript_id: int) -> None:
    """Remove a transcript's rows from all three indexes (chunk table, vec0, FTS)."""
    chunk_ids = [
        r[0]
        for r in db.query(TranscriptChunk.id)
        .filter(TranscriptChunk.transcript_id == transcript_id)
        .all()
    ]
    db.query(TranscriptChunk).filter(
        TranscriptChunk.transcript_id == transcript_id
    ).delete(synchronize_session=False)

    conn = db.connection()
    # vec_chunks is partitioned by transcript_id, so this is a single fast delete.
    conn.exec_driver_sql("DELETE FROM vec_chunks WHERE transcript_id = ?", (transcript_id,))
    for cid in chunk_ids:
        conn.exec_driver_sql("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))


async def index_transcript(db: Session, transcript_id: int) -> int:
    """(Re)index a single transcript. Returns the number of chunks written."""
    t = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        return 0

    chunks = chunk_transcript(t.content_clean or "")
    if not chunks:
        _delete_existing(db, transcript_id)
        db.commit()
        return 0

    # Embed first; if this fails we abort BEFORE touching the indexes (no partial state).
    embeddings = await embed_texts([c["content"] for c in chunks])
    if not embeddings:
        logger.warning("No embeddings returned for transcript %s; skipping vector index", transcript_id)
    elif len(embeddings) != len(chunks):
        raise RuntimeError(
            f"Embedding count {len(embeddings)} != chunk count {len(chunks)} for transcript {transcript_id}"
        )

    _delete_existing(db, transcript_id)

    objs = []
    for c in chunks:
        obj = TranscriptChunk(transcript_id=transcript_id, **c)
        db.add(obj)
        objs.append(obj)
    db.flush()  # assign primary keys

    conn = db.connection()
    if embeddings and serialize_float32 is not None:
        for obj, emb in zip(objs, embeddings):
            conn.exec_driver_sql(
                "INSERT INTO vec_chunks(chunk_id, embedding, transcript_id) VALUES (?, ?, ?)",
                (obj.id, serialize_float32(emb), transcript_id),
            )
    for obj in objs:
        conn.exec_driver_sql(
            "INSERT INTO chunks_fts(rowid, content) VALUES (?, ?)",
            (obj.id, obj.content),
        )

    db.commit()
    return len(objs)


def _is_indexed(db: Session, transcript_id: int) -> bool:
    return (
        db.query(TranscriptChunk.id)
        .filter(TranscriptChunk.transcript_id == transcript_id)
        .first()
        is not None
    )


async def reindex_all(
    db: Session,
    force: bool = False,
    delay: float = 2.0,
    progress: Optional[Callable[[str], None]] = None,
) -> int:
    """Backfill every transcript. Pauses `delay` seconds between transcripts to stay
    well under API rate limits. With force=False, transcripts already indexed are skipped."""
    say = progress or (lambda _msg: None)
    ids = [r[0] for r in db.query(Transcript.id).order_by(Transcript.id).all()]
    total = len(ids)
    indexed = 0
    for i, tid in enumerate(ids):
        if not force and _is_indexed(db, tid):
            say(f"[{i + 1}/{total}] transcript {tid}: already indexed, skipping")
            continue
        try:
            n = await index_transcript(db, tid)
            indexed += 1
            say(f"[{i + 1}/{total}] transcript {tid}: indexed {n} chunk(s)")
        except Exception as e:  # keep going; one bad doc shouldn't abort the whole backfill
            db.rollback()
            logger.exception("Failed to index transcript %s", tid)
            say(f"[{i + 1}/{total}] transcript {tid}: ERROR {e}")
        if delay and i < total - 1:
            await asyncio.sleep(delay)
    return indexed
