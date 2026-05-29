"""Unit tests for the chat/RAG building blocks (no network required)."""
import sqlite3

from app.services.chunker import chunk_transcript
from app.services.retrieval import quote_fts, rrf_fuse, route


def test_chunker_is_deterministic_and_overlaps():
    text = "\n".join(f"Speaker {i % 2}: word{i} more words to fill the chunk" for i in range(200))
    a = chunk_transcript(text, target_words=60, overlap_words=10)
    b = chunk_transcript(text, target_words=60, overlap_words=10)
    assert a == b  # deterministic
    assert len(a) > 1  # actually split
    assert [c["chunk_index"] for c in a] == list(range(len(a)))  # sequential indices
    # consecutive windows overlap (next start is before previous end)
    assert a[1]["start_word"] < a[0]["end_word"]
    assert all(c["content"] for c in a)  # no empty chunks


def test_chunker_handles_empty_input():
    assert chunk_transcript("") == []
    assert chunk_transcript("   \n   ") == []


def test_quote_fts_balances_quotes_on_adversarial_input():
    samples = ['what\'s the "auth" plan?', 'NOT a (real) query OR else*', '""', '   ', 'plain words']
    for q in samples:
        out = quote_fts(q)
        assert isinstance(out, str)
        assert out.count('"') % 2 == 0  # balanced quotes => valid MATCH expression
    assert quote_fts("") == ""


def test_quote_fts_never_raises_fts5_syntax_error():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
    conn.execute("INSERT INTO t(body) VALUES ('the auth plan and payment retry logic')")
    for q in ['what\'s the "auth" plan?', 'payment OR retry', 'NEAR x', '*(bad', 'a AND b', '"unbalanced']:
        match = quote_fts(q)
        # Would raise "fts5: syntax error" if the sanitiser failed:
        conn.execute("SELECT rowid FROM t WHERE t MATCH ?", (match,)).fetchall()
    conn.close()


def test_rrf_fuse_orders_by_fused_score():
    lexical = [1, 2, 3]
    dense = [2, 4, 1]
    fused = rrf_fuse([lexical, dense])
    ids = [i for i, _ in fused]
    assert ids[0] == 2  # ranked highly by both lists -> wins
    assert set(ids) == {1, 2, 3, 4}
    scores = [s for _, s in fused]
    assert scores == sorted(scores, reverse=True)


def test_route_classifies_aggregate_vs_pointed():
    assert route("list all action items") == "aggregate"
    assert route("how many decisions were made") == "aggregate"
    assert route("what tasks are assigned to me") == "aggregate"
    assert route("what did we decide about the database?") == "pointed"
    assert route("summarize the kickoff meeting") == "pointed"
