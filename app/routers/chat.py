"""Chat endpoints: the /chat page, the POST /chat/stream SSE answer stream, and
persisted conversation history (list / load / delete)."""
import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ChatMessage, ChatSession
from app.routers.web import _get_common_context
from app.services import chat as chat_service
from app.services.retrieval import retrieve
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str
    history: List[ChatTurn] = []
    session_id: Optional[int] = None


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _dedupe_sources(items: List[dict]) -> List[dict]:
    """Distinct transcripts referenced, in first-seen order, for the citation chips."""
    seen, out = set(), []
    for it in items:
        tid = it["transcript_id"]
        if tid in seen:
            continue
        seen.add(tid)
        out.append({"id": tid, "title": it["title"]})
    return out


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, db: Session = Depends(get_db)):
    ctx = _get_common_context(db)
    ctx.update({"request": request})
    return templates.TemplateResponse("chat.html", ctx)


@router.get("/chat/sessions")
async def list_sessions(db: Session = Depends(get_db)):
    rows = (
        db.query(ChatSession)
        .order_by(desc(ChatSession.updated_at))
        .limit(100)
        .all()
    )
    return [
        {"id": s.id, "title": s.title, "updated_at": s.updated_at.isoformat() if s.updated_at else None}
        for s in rows
    ]


@router.get("/chat/sessions/{session_id}")
async def get_session(session_id: int, db: Session = Depends(get_db)):
    s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Not found")
    messages = []
    for m in s.messages:
        item = {"role": m.role, "content": m.content}
        if m.role == "assistant" and m.sources:
            try:
                item["sources"] = json.loads(m.sources)
            except json.JSONDecodeError:
                item["sources"] = []
        messages.append(item)
    return {"id": s.id, "title": s.title, "messages": messages}


@router.delete("/chat/sessions/{session_id}")
async def delete_session(session_id: int, db: Session = Depends(get_db)):
    s = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if s:
        db.delete(s)
        db.commit()
    return {"ok": True}


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, db: Session = Depends(get_db)):
    question = (req.question or "").strip()
    history = [t.model_dump() for t in req.history]
    session_id = req.session_id

    # All DB work happens within this generator while the request session is still
    # open (FastAPI keeps `db` alive until the streaming response finishes). The user
    # turn is saved before generation, so a question is never lost even if the model
    # call fails midway; the assistant turn (with sources) is saved once complete.
    async def event_gen():
        if not question:
            yield _sse({"t": "Please enter a question."})
            yield "data: [DONE]\n\n"
            return

        session = None
        try:
            if session_id:
                session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session is None:
                session = ChatSession(title=question[:80] or "New chat")
                db.add(session)
                db.commit()
                db.refresh(session)
            db.add(ChatMessage(session_id=session.id, role="user", content=question))
            db.commit()
            yield f"event: meta\ndata: {json.dumps({'session_id': session.id, 'title': session.title})}\n\n"
        except Exception:
            logger.exception("Failed to persist chat session/user message")
            db.rollback()

        full = ""
        sources: List[dict] = []
        try:
            standalone = await chat_service.condense_question(history, question)
            _mode, items = await retrieve(db, standalone)
            context = chat_service.build_context(items)
            messages = chat_service.build_messages(question, context, history)
            async for token in chat_service.stream_answer(messages):
                full += token
                yield _sse({"t": token})
            sources = _dedupe_sources(items)
            yield f"event: sources\ndata: {json.dumps({'sources': sources})}\n\n"
        except Exception:
            logger.exception("chat stream failed")
            yield _sse({"t": "\n\n_Sorry — something went wrong generating that answer._"})

        if session is not None and full:
            try:
                db.add(ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    content=full,
                    sources=json.dumps(sources),
                ))
                session.updated_at = datetime.utcnow()
                db.commit()
            except Exception:
                logger.exception("Failed to persist assistant message")
                db.rollback()

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
