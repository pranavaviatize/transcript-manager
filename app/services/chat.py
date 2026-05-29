"""Chat orchestration: condense follow-ups, build grounded prompts, and stream answers.

Generation and condensation both go through OpenRouter (same key as enrichment).
The system prompt forces grounding: answer only from the retrieved context, cite
transcripts as N°<id>, and admit when the archive doesn't contain the answer.
"""
import json
import logging
from typing import AsyncIterator, List, Optional

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are Transcript Hub's assistant. Answer the user's question using ONLY the "
    "context below, which is drawn from the team's meeting transcripts.\n"
    "- Cite the transcripts you rely on inline as N°<id> (e.g. \"N°002\").\n"
    "- If the answer is not in the context, say you couldn't find it in the archive — "
    "never invent facts, names, or dates.\n"
    "- Be concise and specific; prefer concrete details (who, what, when, decisions)."
)

_MAX_HISTORY_TURNS = 6


def _headers(settings: Settings) -> dict:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://transcript-hub.local",
        "X-Title": "Transcript Hub",
    }


def build_context(items: List[dict]) -> str:
    """Render retrieved items into a labelled, citable context block."""
    blocks = []
    for it in items:
        md = it.get("meeting_date")
        date = md.strftime("%Y-%m-%d") if md else "unknown date"
        label = f"[N°{it['transcript_id']:03d} · {it['title']} · {date}]"
        blocks.append(f"{label}\n{it['content']}")
    return "\n\n".join(blocks)


def build_messages(question: str, context: str, history: Optional[List[dict]] = None) -> List[dict]:
    """Assemble the chat messages: system + recent history + grounded final turn."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in (history or [])[-_MAX_HISTORY_TURNS:]:
        role, content = turn.get("role"), turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    if context:
        user = f"Context from the transcript archive:\n\n{context}\n\n---\nQuestion: {question}"
    else:
        user = (
            f"Question: {question}\n\n"
            "(No matching transcripts were found in the archive for this question.)"
        )
    messages.append({"role": "user", "content": user})
    return messages


async def condense_question(history: Optional[List[dict]], question: str) -> str:
    """Rewrite a follow-up into a standalone question for retrieval. No-op without history."""
    settings = get_settings()
    if not history or not settings.openrouter_api_key:
        return question

    convo = "\n".join(
        f"{t['role']}: {t['content']}"
        for t in history[-_MAX_HISTORY_TURNS:]
        if t.get("role") and t.get("content")
    )
    prompt = (
        "Given the conversation so far, rewrite the user's final message as a standalone "
        "question that can be understood without the conversation. Keep it faithful and "
        "concise. Return ONLY the rewritten question.\n\n"
        f"Conversation:\n{convo}\n\nFinal message: {question}\n\nStandalone question:"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                OPENROUTER_CHAT_URL,
                headers=_headers(settings),
                json={
                    "model": settings.rerank_model,  # cheap/fast model is fine here
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            rewritten = resp.json()["choices"][0]["message"]["content"].strip()
        return rewritten or question
    except Exception:
        logger.exception("condense_question failed; using original question")
        return question


async def stream_answer(messages: List[dict]) -> AsyncIterator[str]:
    """Yield answer tokens from a streamed OpenRouter chat completion."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        yield "AI is not configured (missing OpenRouter API key)."
        return

    model = settings.chat_model or settings.ai_model
    payload = {"model": model, "messages": messages, "stream": True, "temperature": 0.2}

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None)) as client:
        async with client.stream(
            "POST", OPENROUTER_CHAT_URL, headers=_headers(settings), json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or line.startswith(":"):
                    # blank separator or SSE comment (e.g. ": OPENROUTER PROCESSING")
                    continue
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                token = chunk["choices"][0].get("delta", {}).get("content")
                if token:
                    yield token
