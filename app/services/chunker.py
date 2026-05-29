"""Split a cleaned transcript into overlapping, speaker-aware chunks for indexing.

Pure and deterministic: the same input always yields the same chunks, so re-running
the indexer is idempotent. Chunks are sized by word count (a cheap proxy for tokens)
and carry the dominant speaker plus word offsets back into the source text.
"""
import re
from typing import List, Optional

# Matches a leading "Speaker Name:" label at the start of a line (e.g. "Pranav S: ...").
# Kept conservative (<=48 chars, must start with a letter) to avoid eating prose colons.
_SPEAKER_RE = re.compile(r"^\s*([A-Za-z][\w .,'\-]{0,48}?):\s+(.*)$")

DEFAULT_TARGET_WORDS = 300
DEFAULT_OVERLAP_WORDS = 45


def _split_turns(text: str) -> List[tuple]:
    """Return a list of (speaker_or_None, line_text) preserving speaker continuity."""
    turns = []
    current_speaker: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _SPEAKER_RE.match(line)
        if m:
            current_speaker = m.group(1).strip()
            body = m.group(2).strip()
            if body:
                turns.append((current_speaker, body))
        else:
            turns.append((current_speaker, line))
    return turns


def _render(window: List[tuple]) -> str:
    """Reconstruct readable text from (word, speaker) pairs, re-inserting speaker labels
    whenever the speaker changes inside the window."""
    parts: List[str] = []
    buf: List[str] = []
    last_speaker = object()  # sentinel distinct from any real speaker/None
    for word, speaker in window:
        if speaker != last_speaker:
            if buf:
                parts.append(" ".join(buf))
                buf = []
            if speaker:
                parts.append(f"\n{speaker}: ")
            last_speaker = speaker
        buf.append(word)
    if buf:
        parts.append(" ".join(buf))
    return "".join(parts).strip()


def chunk_transcript(
    content_clean: str,
    target_words: int = DEFAULT_TARGET_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
) -> List[dict]:
    """Chunk a transcript into overlapping windows.

    Returns a list of dicts shaped for the TranscriptChunk model:
    {chunk_index, speaker, start_word, end_word, content, token_count}.
    """
    text = (content_clean or "").strip()
    if not text:
        return []

    # Flatten to a list of (word, speaker) so windows can span speaker turns.
    words: List[tuple] = []
    for speaker, line in _split_turns(text):
        for w in line.split():
            words.append((w, speaker))
    if not words:
        return []

    target_words = max(50, int(target_words))
    overlap_words = max(0, min(int(overlap_words), target_words // 2))
    step = max(1, target_words - overlap_words)

    chunks: List[dict] = []
    n = len(words)
    i = 0
    idx = 0
    while i < n:
        window = words[i : i + target_words]
        speakers = [s for _, s in window if s]
        dominant = max(set(speakers), key=speakers.count) if speakers else None
        chunks.append(
            {
                "chunk_index": idx,
                "speaker": dominant,
                "start_word": i,
                "end_word": i + len(window),
                "content": _render(window),
                "token_count": len(window),
            }
        )
        idx += 1
        if i + target_words >= n:
            break
        i += step
    return chunks
