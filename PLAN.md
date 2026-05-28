# Transcript Hub — Project Plan

## Overview
A self-hosted web service for uploading, organizing, and discovering Google Meet (and other) transcripts. AI automatically extracts metadata, tags, summaries, and action items.

## User Stories (Think Like the User)

### Upload
- "I just finished 4 meetings. I want to drag all 4 transcript files in at once and have them organized automatically."
- "I copy-pasted a transcript from an email — I should be able to paste it directly, not just upload files."
- "I want to see upload progress and know if something failed."

### Organization
- "I don't want to tag manually. The AI should figure out this was a 'product review' with 'Alice' and 'Bob'."
- "But sometimes the AI is wrong — I should be able to edit tags, title, participants after."
- "I want to add my own custom tags too."

### Discovery / Filtering
- "Show me all meetings from last week."
- "Show me all meetings with Alice."
- "Show me all 'urgent' or 'action-required' meetings."
- "Search for 'pricing discussion' across all transcripts."
- "I remember a meeting in March about the API redesign — find it."
- "Filter by: date range, participants, tags, has action items, duration."

### Reading
- "Don't show me raw VTT with timestamps every 3 seconds. Show me clean paragraphs by speaker."
- "Highlight action items."
- "Let me copy a clean version."

### Export
- "Export this transcript as clean Markdown."
- "Export all action items from this week as a todo list."

## Tech Stack

| Layer | Choice |
|-------|--------|
| Backend | FastAPI (Python 3.12) |
| Frontend | Jinja2 + HTMX + Pico CSS |
| Database | SQLite (sqlalchemy + alembic) |
| File Storage | Local filesystem `/data/transcripts/` |
| AI | OpenRouter API (async HTTPX) |
| Task Queue | Python `asyncio` background tasks (no Redis needed for single-user) |
| Reverse Proxy | Caddy (auto HTTPS) |
| Deployment | Docker Compose |

## Database Schema

### transcripts
- id (PK)
- filename (original)
- storage_path
- content_raw
- content_clean
- title (AI-generated, editable)
- summary (AI-generated)
- meeting_date (extracted or file date)
- duration_minutes (extracted)
- word_count
- status: uploaded | processing | completed | failed
- created_at, updated_at

### participants
- id (PK)
- name
- email (if extractable)
- created_at

### transcript_participants (M2M)
- transcript_id
- participant_id

### tags
- id (PK)
- name (unique)
- color (auto-assigned)
- is_auto (AI-created vs user-created)
- created_at

### transcript_tags (M2M)
- transcript_id
- tag_id

### action_items
- id (PK)
- transcript_id
- text
- assignee (participant name, optional)
- completed (bool)
- created_at

## API Endpoints

### Upload
- `POST /api/transcripts/upload` — multipart file upload, returns transcript ID
- `POST /api/transcripts/paste` — paste raw text directly

### Browse / Search
- `GET /api/transcripts` — list with filters (date_from, date_to, participants, tags, search_q, has_action_items)
- `GET /api/transcripts/{id}` — full detail
- `GET /api/transcripts/{id}/raw` — original file download

### Management
- `PATCH /api/transcripts/{id}` — edit title, tags, participants
- `DELETE /api/transcripts/{id}` — soft or hard delete

### Tags & Participants
- `GET /api/tags` — all tags
- `GET /api/participants` — all participants

### Export
- `GET /api/transcripts/{id}/export.md` — markdown export
- `GET /api/transcripts/{id}/action-items` — action items as checklist

## UI Views

### Layout
- Sidebar: logo, upload button, nav (All Transcripts, Tags, Participants, Action Items)
- Main: content area
- Top bar: search, filters, sort

### Pages
1. **Dashboard** (`/`)
   - Stats: total transcripts, this week, action items pending
   - Recent uploads
   - Tag cloud
   - Quick search

2. **Transcript List** (`/transcripts`)
   - Filter bar: date range picker, participant dropdown, tag pills, search input
   - Sort: date, title, word count
   - Cards or table view toggle
   - Each card: title, date, participants (avatars/names), tags, snippet, action item count
   - Bulk actions: delete, retag

3. **Transcript Detail** (`/transcripts/{id}`)
   - Header: title (editable inline), date, duration, participants
   - Tags (editable, add/remove)
   - Summary box
   - Action items checklist (toggle complete)
   - Clean transcript view (speaker paragraphs)
   - Raw view toggle
   - Export dropdown

4. **Upload Modal/Page**
   - Drag & drop zone
   - Or paste text area
   - Progress indicator
   - Processing status

## AI Prompt Strategy

### Extraction Prompt
```
Analyze this meeting transcript and return JSON:
{
  "title": "short descriptive title",
  "summary": "2-3 sentence summary",
  "participants": ["Name", "Name"],
  "tags": ["topic1", "topic2", "topic3"],
  "meeting_date": "YYYY-MM-DD or null",
  "duration_minutes": number or null,
  "action_items": [
    {"text": "...", "assignee": "Name or null"}
  ],
  "sentiment": "positive|neutral|negative"
}
```

Use a cheap/fast model (e.g., `google/gemini-flash-1.5` or `anthropic/claude-3-haiku`) for this.

## File Processing

### Supported Formats
- `.txt` — Google Meet default export
- `.vtt` — WebVTT subtitles
- `.srt` — SubRip subtitles
- `.docx` — Word (optional, via python-docx)
- Raw paste — plain text

### Cleaning Pipeline
1. Detect format from extension/content
2. Strip timestamps and metadata lines
3. Deduplicate speaker labels
4. Group by speaker into paragraphs
5. Remove filler words ("um", "uh") — optional

## Deployment

### Docker Compose
- `app` container: FastAPI + Uvicorn
- `caddy` container: reverse proxy + HTTPS
- Shared volume: `/data` for SQLite + transcripts

### Environment Variables
- `OPENROUTER_API_KEY`
- `DATABASE_URL` (default: sqlite:///data/db.sqlite)
- `UPLOAD_DIR` (default: /data/transcripts)
- `AI_MODEL` (default: google/gemini-flash-1.5)

## Future Ideas (Not in V1)
- Full-text search with SQLite FTS5
- Calendar integration (auto-detect meeting from calendar invite)
- Speaker diarization confidence
- Email digest of weekly action items
- Multi-user / auth
- Transcript comparison / diff
