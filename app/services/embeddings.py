"""Embed text via the OpenRouter embeddings endpoint.

Uses the same OpenRouter key as enrichment (app.services.ai_enricher). Requests are
batched and processed sequentially (one in-flight request at a time), and every call
retries with exponential backoff on 429 / 5xx — honoring Retry-After — so bulk
backfills don't trip API rate limits.
"""
import asyncio
import logging
from typing import List, Sequence

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
_BATCH_SIZE = 64
_MAX_RETRIES = 5
_BASE_BACKOFF = 1.0
_MAX_BACKOFF = 30.0


def _headers(settings: Settings) -> dict:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://transcript-hub.local",
        "X-Title": "Transcript Hub",
    }


async def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    """Embed a list of texts, returning one vector per input in the same order.

    Returns [] if there is no API key or no input. Raises on persistent API failure
    (callers that must not partially index should treat that as fatal for the doc).
    """
    settings = get_settings()
    if not settings.openrouter_api_key or not texts:
        return []

    out: List[List[float]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=120.0)) as client:
        for start in range(0, len(texts), _BATCH_SIZE):
            batch = list(texts[start : start + _BATCH_SIZE])
            out.extend(await _embed_batch(client, batch, settings))
    return out


async def embed_query(text: str) -> List[float]:
    """Embed a single query string; returns [] if unavailable."""
    vecs = await embed_texts([text])
    return vecs[0] if vecs else []


async def _embed_batch(client: httpx.AsyncClient, batch: List[str], settings: Settings) -> List[List[float]]:
    payload = {"model": settings.embedding_model, "input": batch}
    if settings.embedding_dim:
        payload["dimensions"] = settings.embedding_dim

    backoff = _BASE_BACKOFF
    last_resp = None
    for attempt in range(_MAX_RETRIES):
        resp = await client.post(OPENROUTER_EMBEDDINGS_URL, headers=_headers(settings), json=payload)
        last_resp = resp
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = _parse_retry_after(resp)
            wait = max(retry_after, backoff)
            logger.warning(
                "Embeddings %s; retry %d/%d after %.1fs",
                resp.status_code, attempt + 1, _MAX_RETRIES, wait,
            )
            await asyncio.sleep(wait)
            backoff = min(backoff * 2, _MAX_BACKOFF)
            continue
        resp.raise_for_status()
        data = resp.json()["data"]
        data.sort(key=lambda d: d["index"])  # guarantee input order
        return [d["embedding"] for d in data]

    # Retries exhausted — surface the last error.
    if last_resp is not None:
        last_resp.raise_for_status()
    raise RuntimeError("Embeddings request failed with no response")


def _parse_retry_after(resp: httpx.Response) -> float:
    raw = resp.headers.get("retry-after")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0
