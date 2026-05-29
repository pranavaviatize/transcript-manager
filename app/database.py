from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base
import logging
import os

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - vector search unavailable without the package
    sqlite_vec = None

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/db.sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    if not DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

    # Load sqlite-vec on this connection so vec0 virtual tables resolve.
    # The connect event fires for every pooled connection; extensions are per-connection.
    if sqlite_vec is None:
        return
    if not hasattr(dbapi_conn, "enable_load_extension"):
        logger.error(
            "sqlite3 was built without loadable-extension support; vector search is "
            "disabled. Install pysqlite3-binary to enable it."
        )
        return
    dbapi_conn.enable_load_extension(True)
    sqlite_vec.load(dbapi_conn)
    dbapi_conn.enable_load_extension(False)


def init_fts():
    """Initialize FTS5 virtual table for full-text search."""
    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
                title, content_clean, summary,
                content='transcripts',
                content_rowid='id'
            )
        """))
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS transcripts_fts_insert AFTER INSERT ON transcripts BEGIN
                INSERT INTO transcripts_fts(rowid, title, content_clean, summary)
                VALUES (new.id, new.title, new.content_clean, new.summary);
            END
        """))
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS transcripts_fts_update AFTER UPDATE ON transcripts BEGIN
                INSERT INTO transcripts_fts(transcripts_fts, rowid, title, content_clean, summary)
                VALUES ('delete', old.id, old.title, old.content_clean, old.summary);
                INSERT INTO transcripts_fts(rowid, title, content_clean, summary)
                VALUES (new.id, new.title, new.content_clean, new.summary);
            END
        """))
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS transcripts_fts_delete AFTER DELETE ON transcripts BEGIN
                INSERT INTO transcripts_fts(transcripts_fts, rowid, title, content_clean, summary)
                VALUES ('delete', old.id, old.title, old.content_clean, old.summary);
            END
        """))
        conn.commit()


def init_vec():
    """Create the sqlite-vec vec0 virtual table that stores chunk embeddings.

    Rows are written/removed by the indexer (app.services.indexer), keyed by
    transcript_chunks.id, partitioned by transcript_id for fast pre-filtering.
    """
    if not DATABASE_URL.startswith("sqlite") or sqlite_vec is None:
        return
    from app.config import get_settings
    dim = get_settings().embedding_dim
    with engine.connect() as conn:
        conn.execute(text(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
                chunk_id INTEGER PRIMARY KEY,
                embedding FLOAT[{dim}] distance_metric=cosine,
                transcript_id INTEGER partition key
            )
        """))
        conn.commit()


def init_chunks_fts():
    """Create the chunk-level FTS5 table for BM25 lexical search.

    Unlike transcripts_fts, this is populated directly by the indexer (no triggers),
    so a plain (content-storing) fts5 table is used to keep DELETE-by-rowid simple.
    """
    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.connect() as conn:
        conn.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content)"))
        conn.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
