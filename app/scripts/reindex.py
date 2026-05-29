"""Backfill / re-embed transcripts into the chunk + vector + FTS indexes.

Examples:
    python -m app.scripts.reindex                # index transcripts that aren't indexed yet
    python -m app.scripts.reindex --force        # re-embed everything (e.g. after changing the embedding model)
    python -m app.scripts.reindex --delay 10     # wait 10s between transcripts (extra rate-limit safety)

Each transcript is embedded in one (batched) request and the script pauses --delay
seconds between transcripts, so even large backfills stay comfortably under API limits.
"""
import argparse
import asyncio

from app.database import SessionLocal
from app.services.indexer import reindex_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill transcript embeddings + search indexes.")
    parser.add_argument("--force", action="store_true", help="Re-embed transcripts even if already indexed.")
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to wait between transcripts to avoid API rate limits (default: 2.0).",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        total = asyncio.run(reindex_all(db, force=args.force, delay=args.delay, progress=print))
        print(f"Done. Indexed {total} transcript(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
