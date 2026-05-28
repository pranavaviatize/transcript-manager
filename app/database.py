from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base
import os

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
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
