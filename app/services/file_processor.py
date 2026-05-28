import re
import os
from datetime import datetime
from pathlib import Path


def detect_format(content: str, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".vtt":
        return "vtt"
    if ext == ".srt":
        return "srt"
    if ext in (".md", ".markdown"):
        return "md"
    if ext in (".txt",):
        return "txt"
    if "WEBVTT" in content[:1000]:
        return "vtt"
    if re.search(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}", content):
        return "vtt"
    # Detect markdown by common patterns
    if re.search(r"^#{1,6}\s+", content, re.MULTILINE) or re.search(r"```", content):
        return "md"
    return "txt"


def clean_vtt(content: str) -> str:
    lines = content.splitlines()
    result = []
    current_speaker = None
    current_text = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}", line) or "-->" in line:
            continue
        if re.match(r"^\d+$", line):
            continue

        speaker_match = re.match(r"<v\s+([^>]+)>(.*)", line)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            text = speaker_match.group(2).strip()
        else:
            colon_match = re.match(r"^([^:]+):\s*(.*)", line)
            if colon_match:
                speaker = colon_match.group(1).strip()
                text = colon_match.group(2).strip()
            else:
                speaker = None
                text = line

        if speaker and speaker != current_speaker:
            if current_text:
                prefix = f"{current_speaker}: " if current_speaker else ""
                result.append(prefix + " ".join(current_text))
            current_speaker = speaker
            current_text = [text]
        else:
            current_text.append(text)

    if current_text:
        prefix = f"{current_speaker}: " if current_speaker else ""
        result.append(prefix + " ".join(current_text))

    return "\n\n".join(result)


def clean_srt(content: str) -> str:
    lines = content.splitlines()
    result = []
    current_text = []

    for line in lines:
        line = line.strip()
        if not line:
            if current_text:
                result.append(" ".join(current_text))
                current_text = []
            continue
        if re.match(r"^\d+$", line):
            continue
        if "-->" in line:
            continue
        current_text.append(line)

    if current_text:
        result.append(" ".join(current_text))

    return "\n\n".join(result)


def clean_txt(content: str) -> str:
    lines = content.splitlines()
    result = []
    current_speaker = None
    current_text = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        match = re.match(r"^([^\(]+)\s*(?:\(\d{2}:\d{2}\))?\s*:\s*(.*)", line)
        if match:
            speaker = match.group(1).strip()
            text = match.group(2).strip()
        else:
            speaker = None
            text = line

        if speaker and speaker != current_speaker:
            if current_text:
                prefix = f"{current_speaker}: " if current_speaker else ""
                result.append(prefix + " ".join(current_text))
            current_speaker = speaker
            current_text = [text]
        else:
            current_text.append(text)

    if current_text:
        prefix = f"{current_speaker}: " if current_speaker else ""
        result.append(prefix + " ".join(current_text))

    return "\n\n".join(result)


def clean_md(content: str) -> str:
    """Preserve markdown structure but clean up transcript artifacts."""
    # Remove Google Meet specific artifacts like timestamps in parentheses
    content = re.sub(r"\(\d{2}:\d{2}\)", "", content)
    # Normalize multiple blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


def extract_code_blocks(content: str) -> list:
    """Extract fenced code blocks from markdown."""
    blocks = []
    pattern = r"```(\w*)\n(.*?)```"
    for match in re.finditer(pattern, content, re.DOTALL):
        lang = match.group(1).strip() or None
        code = match.group(2).strip()
        # Get surrounding context (200 chars before)
        start = max(0, match.start() - 200)
        context = content[start:match.start()].strip().split("\n")[-1]
        blocks.append({"language": lang, "code": code, "context": context})
    return blocks


def extract_speaker_stats(content: str) -> list:
    """Estimate word counts per speaker from clean transcript.

    Only counts lines that look like natural speaker labels:
    - Not markdown headers (## Name)
    - Not bold markers (**Name**)
    - Not bullet points (- Name)
    - Not metadata fields (Meeting Date, Duration, etc.)
    """
    stats = {}
    speaker_pattern = re.compile(r"^([^:\n]+):\s*(.*)$", re.MULTILINE)
    for match in speaker_pattern.finditer(content):
        speaker = match.group(1).strip()
        text = match.group(2).strip()

        # Skip markdown artifacts
        if speaker.startswith("#") or speaker.startswith("**") or speaker.startswith("-"):
            continue
        if speaker.startswith("[") and speaker.endswith("]"):
            continue
        # Skip common metadata field names
        if speaker.lower() in ("meeting date", "duration", "attendees", "discussion", "decisions", "action items"):
            continue
        # Skip if speaker looks like a code keyword
        if speaker.lower() in ("try", "except", "if", "else", "for", "while", "def", "class", "import", "from"):
            continue
        if len(speaker) > 30 or len(speaker) < 2:
            continue
        if not re.match(r"^[A-Za-z][A-Za-z\s\-'\.]+$", speaker):
            continue

        if speaker not in stats:
            stats[speaker] = 0
        stats[speaker] += len(text.split())

    result = []
    for speaker, word_count in stats.items():
        # Rough estimate: 130 words per minute
        result.append({
            "speaker_name": speaker,
            "word_count": word_count,
            "estimated_minutes": round(word_count / 130, 1),
        })
    return result


def process_transcript(content: str, filename: str) -> dict:
    fmt = detect_format(content, filename)
    raw = content

    if fmt == "vtt":
        clean = clean_vtt(content)
    elif fmt == "srt":
        clean = clean_srt(content)
    elif fmt == "md":
        clean = clean_md(content)
    else:
        clean = clean_txt(content)

    word_count = len(clean.split())
    code_blocks = extract_code_blocks(clean) if fmt == "md" else []
    speaker_stats = extract_speaker_stats(clean)

    # Try to extract date from filename
    date_match = re.search(r"(\d{4}[-_]\d{2}[-_]\d{2})", filename)
    meeting_date = None
    if date_match:
        try:
            meeting_date = datetime.strptime(date_match.group(1).replace("_", "-"), "%Y-%m-%d")
        except ValueError:
            pass

    return {
        "content_raw": raw,
        "content_clean": clean,
        "word_count": word_count,
        "meeting_date": meeting_date,
        "format": fmt,
        "code_blocks": code_blocks,
        "speaker_stats": speaker_stats,
    }


def save_upload(file_bytes: bytes, filename: str, upload_dir: str) -> str:
    os.makedirs(upload_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^\w\-.]", "_", filename)
    storage_name = f"{timestamp}_{safe_name}"
    path = os.path.join(upload_dir, storage_name)
    with open(path, "wb") as f:
        f.write(file_bytes)
    return path
