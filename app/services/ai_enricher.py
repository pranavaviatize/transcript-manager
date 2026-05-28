import asyncio
import json
import re
from collections import Counter
from typing import Optional

import httpx

from app.config import Settings, get_settings

WORDS_PER_CHUNK = 200000

EXTRACTION_PROMPT = """You are analyzing a meeting transcript for a software development team. Extract structured information and return ONLY valid JSON — no markdown, no code blocks, no extra text.

Transcript:
{transcript}

Return this exact JSON structure:
{{
  "title": "Concise descriptive title (max 80 chars)",
  "summary": "2-3 sentence summary of key discussion points",
  "meeting_type": "One of: standup, code-review, architecture, planning, retro, 1-1, interview, demo, bug-triage, general",
  "participants": ["Name1", "Name2"],
  "tags": ["topic1", "topic2", "topic3"],
  "meeting_date": "YYYY-MM-DD or null",
  "duration_minutes": number or null,
  "sentiment": "positive|neutral|negative",
  "action_items": [
    {{"text": "Clear actionable task", "assignee": "Name or null", "priority": "low|normal|high|urgent"}}
  ],
  "decisions": [
    {{"text": "Decision made in meeting", "category": "architecture|process|product|tech-stack|timeline|general"}}
  ],
  "code_blocks": [
    {{"language": "python|javascript|typescript|sql|yaml|json|bash|other", "code": "the actual code snippet", "context": "what this code is for"}}
  ]
}}

Rules:
- meeting_type: Infer from content. Standups have updates/blockers. Code reviews discuss PRs. Architecture discusses system design. Planning has sprint/tasks. Retros have what-went-well. 1-1s are personal. Demos show features.
- tags: Use lowercase, hyphenated. Include: frontend, backend, api, database, deployment, bug, feature, refactor, performance, security, testing, ui-ux, devops, mobile, ai-ml, infrastructure, documentation, meeting-type (standup, retro, etc.)
- action_items: Only concrete tasks with clear owners when possible. Skip vague "follow up" without specifics.
- decisions: Capture architectural choices, process changes, tech stack decisions, timeline commitments.
- code_blocks: Extract any code snippets discussed (functions, configs, SQL queries, CLI commands). Include the language if obvious.
- participants: Infer from speaker labels and mentions. Use first names for frequent collaborators, full names for clarity.
- If transcript is short or unclear, use "general" for meeting_type and minimal tags.
"""


async def enrich_transcript(text: str) -> Optional[dict]:
    settings = get_settings()
    if not settings.openrouter_api_key:
        return None

    words = text.split()

    if len(words) <= WORDS_PER_CHUNK:
        return await _enrich_chunk(text, settings)

    chunks = [
        " ".join(words[i : i + WORDS_PER_CHUNK])
        for i in range(0, len(words), WORDS_PER_CHUNK)
    ]
    results = await asyncio.gather(*[_enrich_chunk(c, settings) for c in chunks])
    results = [r for r in results if r]

    if not results:
        return None
    if len(results) == 1:
        return results[0]

    return _merge_chunks(results)


async def _enrich_chunk(text: str, settings: Settings) -> Optional[dict]:
    prompt = EXTRACTION_PROMPT.format(transcript=text)

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://transcript-hub.local",
                    "X-Title": "Transcript Hub",
                },
                json={
                    "model": settings.ai_model,
                    "messages": [
                        {"role": "system", "content": "You are a precise meeting transcript analyzer for software teams. Return only valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 16000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]

            content = re.sub(r"^```json\s*", "", content)
            content = re.sub(r"```\s*$", "", content)
            content = content.strip()

            result = json.loads(content)

            result["participants"] = list(set(p.strip() for p in result.get("participants", []) if p.strip()))
            result["tags"] = list(set(t.strip().lower().replace(" ", "-") for t in result.get("tags", []) if t.strip()))
            result["meeting_type"] = result.get("meeting_type", "general").lower().replace(" ", "-")

            action_items = []
            for item in result.get("action_items", []):
                if isinstance(item, dict) and item.get("text"):
                    action_items.append({
                        "text": item["text"],
                        "assignee": item.get("assignee") or None,
                        "priority": item.get("priority", "normal"),
                    })
            result["action_items"] = action_items

            decisions = []
            for d in result.get("decisions", []):
                if isinstance(d, dict) and d.get("text"):
                    decisions.append({
                        "text": d["text"],
                        "category": d.get("category", "general"),
                    })
            result["decisions"] = decisions

            code_blocks = []
            for cb in result.get("code_blocks", []):
                if isinstance(cb, dict) and cb.get("code"):
                    code_blocks.append({
                        "language": cb.get("language") or "text",
                        "code": cb["code"],
                        "context": cb.get("context", ""),
                    })
            result["code_blocks"] = code_blocks

            return result
        except Exception as e:
            print(f"AI enrichment failed: {e}")
            return None


def _merge_chunks(results: list[dict]) -> dict:
    merged = {
        "title": results[0].get("title", ""),
        "summary": " ".join(r["summary"] for r in results if r.get("summary")),
        "meeting_type": Counter(r.get("meeting_type", "general") for r in results).most_common(1)[0][0],
        "meeting_date": next((r.get("meeting_date") for r in results if r.get("meeting_date")), None),
        "sentiment": Counter(r.get("sentiment", "neutral") for r in results).most_common(1)[0][0],
        "participants": [],
        "tags": sorted({t for r in results for t in r.get("tags", [])}),
        "action_items": [],
        "decisions": [],
        "code_blocks": [],
    }

    durations = [r["duration_minutes"] for r in results if r.get("duration_minutes")]
    merged["duration_minutes"] = sum(durations) if durations else None

    seen_participants = set()
    for r in results:
        for p in r.get("participants", []):
            key = p.lower()
            if key not in seen_participants:
                seen_participants.add(key)
                merged["participants"].append(p)

    seen_actions = set()
    for r in results:
        for item in r.get("action_items", []):
            key = item["text"].lower().strip()
            if key not in seen_actions:
                seen_actions.add(key)
                merged["action_items"].append(item)

    seen_decisions = set()
    for r in results:
        for d in r.get("decisions", []):
            key = d["text"].lower().strip()
            if key not in seen_decisions:
                seen_decisions.add(key)
                merged["decisions"].append(d)

    for r in results:
        merged["code_blocks"].extend(r.get("code_blocks", []))

    return merged
