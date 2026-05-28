from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, asc, or_, func
from typing import Optional, List
from datetime import datetime

from app.database import get_db
from app.models import (
    Transcript,
    Tag,
    Participant,
    ActionItem,
    Decision,
    transcript_tags,
    transcript_participants,
)
from app.templating import templates

router = APIRouter()


def _get_common_context(db: Session):
    tag_rows = (
        db.query(Tag, func.count(transcript_tags.c.transcript_id).label("usage"))
        .outerjoin(transcript_tags, Tag.id == transcript_tags.c.tag_id)
        .group_by(Tag.id)
        .order_by(func.count(transcript_tags.c.transcript_id).desc(), Tag.name)
        .all()
    )
    participant_rows = (
        db.query(Participant, func.count(transcript_participants.c.transcript_id).label("usage"))
        .outerjoin(transcript_participants, Participant.id == transcript_participants.c.participant_id)
        .group_by(Participant.id)
        .order_by(func.count(transcript_participants.c.transcript_id).desc(), Participant.name)
        .all()
    )

    return {
        "all_tags": [
            {"name": t.name, "color": t.color, "count": int(usage or 0)}
            for t, usage in tag_rows
        ],
        "all_participants": [
            {"name": p.name, "count": int(usage or 0)}
            for p, usage in participant_rows
        ],
        "counts": {
            "action_items": db.query(func.count(ActionItem.id)).scalar() or 0,
            "decisions": db.query(func.count(Decision.id)).scalar() or 0,
        },
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    from app.routers.api import get_stats
    stats = await get_stats(db)
    recent = db.query(Transcript).options(
        joinedload(Transcript.participants),
        joinedload(Transcript.tags),
    ).order_by(desc(Transcript.created_at)).limit(6).all()

    ctx = _get_common_context(db)
    ctx.update({
        "request": request,
        "stats": stats,
        "recent": recent,
    })
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/transcripts", response_class=HTMLResponse)
async def transcripts_page(
    request: Request,
    tags: Optional[str] = None,
    participants: Optional[str] = None,
    db: Session = Depends(get_db),
):
    ctx = _get_common_context(db)
    ctx["request"] = request
    ctx["active_tags"] = [t.strip() for t in (tags or "").split(",") if t.strip()]
    ctx["active_participants"] = [p.strip() for p in (participants or "").split(",") if p.strip()]
    return templates.TemplateResponse("transcripts.html", ctx)


@router.get("/tags", response_class=HTMLResponse)
async def tags_page(request: Request, db: Session = Depends(get_db)):
    tags = db.query(Tag).options(joinedload(Tag.transcripts)).order_by(Tag.name).all()
    ctx = _get_common_context(db)
    ctx.update({
        "request": request,
        "tags": tags,
    })
    return templates.TemplateResponse("tags.html", ctx)


@router.get("/transcripts/{transcript_id}", response_class=HTMLResponse)
async def transcript_detail_page(transcript_id: int, request: Request, db: Session = Depends(get_db)):
    t = db.query(Transcript).options(
        joinedload(Transcript.participants),
        joinedload(Transcript.tags),
        joinedload(Transcript.action_items),
        joinedload(Transcript.code_blocks),
        joinedload(Transcript.decisions),
        joinedload(Transcript.speaker_stats),
        joinedload(Transcript.images),
    ).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Not found")

    ctx = _get_common_context(db)
    ctx.update({
        "request": request,
        "transcript": t,
    })
    return templates.TemplateResponse("detail.html", ctx)


def _parse_optional_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_optional_bool(value: Optional[str]) -> Optional[bool]:
    if value is None or value == "":
        return None
    return value.lower() in ("1", "true", "yes", "on")


@router.get("/partials/transcript-list", response_class=HTMLResponse)
async def transcript_list_partial(
    request: Request,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    participants: Optional[str] = None,
    tags: Optional[str] = None,
    meeting_type: Optional[str] = None,
    has_action_items: Optional[str] = None,
    has_decisions: Optional[str] = None,
    has_code: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    date_from_dt = _parse_optional_date(date_from)
    date_to_dt = _parse_optional_date(date_to)
    has_action_items_b = _parse_optional_bool(has_action_items)
    has_decisions_b = _parse_optional_bool(has_decisions)
    has_code_b = _parse_optional_bool(has_code)

    query = db.query(Transcript).options(
        joinedload(Transcript.participants),
        joinedload(Transcript.tags),
        joinedload(Transcript.action_items),
        joinedload(Transcript.decisions),
        joinedload(Transcript.code_blocks),
    )

    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Transcript.title.ilike(like),
                Transcript.content_clean.ilike(like),
                Transcript.summary.ilike(like),
            )
        )

    if date_from_dt:
        query = query.filter(Transcript.meeting_date >= date_from_dt)
    if date_to_dt:
        query = query.filter(Transcript.meeting_date <= date_to_dt)
    if status:
        query = query.filter(Transcript.status == status)
    if meeting_type:
        query = query.filter(Transcript.meeting_type == meeting_type)

    if participants:
        names = [n.strip() for n in participants.split(",") if n.strip()]
        query = query.filter(Transcript.participants.any(Participant.name.in_(names)))

    if tags:
        tagnames = [t.strip() for t in tags.split(",") if t.strip()]
        query = query.filter(Transcript.tags.any(Tag.name.in_(tagnames)))

    if has_action_items_b is not None:
        if has_action_items_b:
            query = query.filter(Transcript.action_items.any())
        else:
            query = query.filter(~Transcript.action_items.any())

    if has_decisions_b is not None:
        if has_decisions_b:
            query = query.filter(Transcript.decisions.any())
        else:
            query = query.filter(~Transcript.decisions.any())

    if has_code_b is not None:
        if has_code_b:
            query = query.filter(Transcript.code_blocks.any())
        else:
            query = query.filter(~Transcript.code_blocks.any())

    sort_col = getattr(Transcript, sort_by, Transcript.created_at)
    if sort_order == "asc":
        query = query.order_by(asc(sort_col))
    else:
        query = query.order_by(desc(sort_col))

    transcripts = query.offset(skip).limit(limit).all()

    def _build_item(t):
        return {
            "id": t.id,
            "title": t.title or t.filename,
            "filename": t.filename,
            "meeting_date": t.meeting_date,
            "duration_minutes": t.duration_minutes,
            "word_count": t.word_count,
            "sentiment": t.sentiment,
            "meeting_type": t.meeting_type,
            "status": t.status,
            "created_at": t.created_at,
            "participants": [{"id": p.id, "name": p.name, "email": p.email} for p in t.participants],
            "tags": [{"id": tag.id, "name": tag.name, "color": tag.color, "is_auto": tag.is_auto} for tag in t.tags],
            "action_item_count": len([a for a in t.action_items if not a.completed]),
            "decision_count": len(t.decisions),
            "code_block_count": len(t.code_blocks),
            "summary": t.summary,
        }

    return templates.TemplateResponse("partials/transcript_list.html", {
        "request": request,
        "transcripts": [_build_item(t) for t in transcripts],
    })


# ---------------------------------------------------------------------------
# Extract index pages: /action-items, /decisions, /code-snippets
# ---------------------------------------------------------------------------


@router.get("/action-items", response_class=HTMLResponse)
async def action_items_page(request: Request, db: Session = Depends(get_db)):
    assignees = [
        a[0]
        for a in db.query(ActionItem.assignee)
        .filter(ActionItem.assignee.isnot(None), ActionItem.assignee != "")
        .distinct()
        .order_by(ActionItem.assignee)
        .all()
    ]
    ctx = _get_common_context(db)
    ctx.update({
        "request": request,
        "assignees": assignees,
    })
    return templates.TemplateResponse("action_items.html", ctx)


@router.get("/decisions", response_class=HTMLResponse)
async def decisions_page(request: Request, db: Session = Depends(get_db)):
    categories = [
        c[0]
        for c in db.query(Decision.category)
        .filter(Decision.category.isnot(None), Decision.category != "")
        .distinct()
        .order_by(Decision.category)
        .all()
    ]
    ctx = _get_common_context(db)
    ctx.update({
        "request": request,
        "categories": categories,
    })
    return templates.TemplateResponse("decisions.html", ctx)


def _has_active_filter(*values) -> bool:
    return any(v is not None and v != "" for v in values)


# ---------------------------------------------------------------------------
# Inline edit/delete: single-row + edit-form partials
# ---------------------------------------------------------------------------


@router.get("/partials/action-items/{action_item_id}", response_class=HTMLResponse)
async def action_item_row_partial(
    action_item_id: int,
    request: Request,
    context: str = "list",
    db: Session = Depends(get_db),
):
    ai = (
        db.query(ActionItem)
        .options(joinedload(ActionItem.transcript))
        .filter(ActionItem.id == action_item_id)
        .first()
    )
    if not ai:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        "partials/action_item_row.html",
        {"request": request, "ai": ai, "context": context},
    )


@router.get("/partials/action-items/{action_item_id}/edit", response_class=HTMLResponse)
async def action_item_edit_partial(
    action_item_id: int,
    request: Request,
    context: str = "list",
    db: Session = Depends(get_db),
):
    ai = db.query(ActionItem).filter(ActionItem.id == action_item_id).first()
    if not ai:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        "partials/action_item_edit.html",
        {"request": request, "ai": ai, "context": context},
    )


@router.get("/partials/decisions/{decision_id}", response_class=HTMLResponse)
async def decision_row_partial(
    decision_id: int,
    request: Request,
    context: str = "list",
    db: Session = Depends(get_db),
):
    d = (
        db.query(Decision)
        .options(joinedload(Decision.transcript))
        .filter(Decision.id == decision_id)
        .first()
    )
    if not d:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        "partials/decision_item_row.html",
        {"request": request, "d": d, "context": context},
    )


@router.get("/partials/decisions/{decision_id}/edit", response_class=HTMLResponse)
async def decision_edit_partial(
    decision_id: int,
    request: Request,
    context: str = "list",
    db: Session = Depends(get_db),
):
    d = db.query(Decision).filter(Decision.id == decision_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        "partials/decision_item_edit.html",
        {"request": request, "d": d, "context": context},
    )


# ---------------------------------------------------------------------------
# Recall: unified search across action items, decisions, and transcripts
# ---------------------------------------------------------------------------


@router.get("/recall", response_class=HTMLResponse)
async def recall_page(request: Request, db: Session = Depends(get_db)):
    ctx = _get_common_context(db)
    ctx["request"] = request
    return templates.TemplateResponse("recall.html", ctx)


def _excerpt_around(text: str, query: str, radius: int = 90) -> str:
    """Return a short excerpt of `text` centered on the first case-insensitive match of `query`."""
    if not text or not query:
        return (text or "")[: radius * 2]
    lower = text.lower()
    pos = lower.find(query.lower())
    if pos < 0:
        return text[: radius * 2]
    start = max(0, pos - radius)
    end = min(len(text), pos + len(query) + radius)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


@router.get("/partials/recall-results", response_class=HTMLResponse)
async def recall_results_partial(
    request: Request,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query_text = (q or "").strip()
    if not query_text:
        return templates.TemplateResponse(
            "partials/recall_results.html",
            {
                "request": request,
                "q": "",
                "action_items": [],
                "decisions": [],
                "transcripts": [],
                "total": 0,
                "has_query": False,
            },
        )

    like = f"%{query_text}%"

    action_items = (
        db.query(ActionItem)
        .options(joinedload(ActionItem.transcript))
        .filter(ActionItem.text.ilike(like))
        .order_by(desc(ActionItem.created_at))
        .limit(20)
        .all()
    )

    decisions = (
        db.query(Decision)
        .options(joinedload(Decision.transcript))
        .filter(Decision.text.ilike(like))
        .order_by(desc(Decision.created_at))
        .limit(20)
        .all()
    )

    transcripts_raw = (
        db.query(Transcript)
        .filter(
            or_(
                Transcript.title.ilike(like),
                Transcript.summary.ilike(like),
                Transcript.content_clean.ilike(like),
            )
        )
        .order_by(desc(Transcript.meeting_date), desc(Transcript.created_at))
        .limit(20)
        .all()
    )

    transcript_hits = []
    for t in transcripts_raw:
        # Find best context: prefer summary, then content
        excerpt_source = ""
        if t.summary and query_text.lower() in t.summary.lower():
            excerpt_source = t.summary
        elif t.content_clean and query_text.lower() in t.content_clean.lower():
            excerpt_source = t.content_clean
        elif t.title and query_text.lower() in t.title.lower():
            excerpt_source = t.title
        transcript_hits.append({
            "transcript": t,
            "excerpt": _excerpt_around(excerpt_source, query_text),
        })

    total = len(action_items) + len(decisions) + len(transcript_hits)

    return templates.TemplateResponse(
        "partials/recall_results.html",
        {
            "request": request,
            "q": query_text,
            "action_items": action_items,
            "decisions": decisions,
            "transcripts": transcript_hits,
            "total": total,
            "has_query": True,
        },
    )


@router.get("/partials/action-items-list", response_class=HTMLResponse)
async def action_items_list_partial(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    assignee: Optional[str] = None,
    priority: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    query = db.query(ActionItem).options(joinedload(ActionItem.transcript))

    if search:
        like = f"%{search}%"
        query = query.filter(ActionItem.text.ilike(like))

    if status == "pending":
        query = query.filter(ActionItem.completed == False)  # noqa: E712
    elif status == "completed":
        query = query.filter(ActionItem.completed == True)  # noqa: E712

    if assignee:
        query = query.filter(ActionItem.assignee == assignee)

    if priority:
        query = query.filter(ActionItem.priority == priority)

    # sort_by may be created_at or priority (custom ordering)
    if sort_by == "priority":
        # urgent > high > normal > low
        priority_rank = {
            "urgent": 0,
            "high": 1,
            "normal": 2,
            "low": 3,
        }
        items = query.all()
        items.sort(
            key=lambda x: (priority_rank.get(x.priority, 99), x.created_at),
            reverse=(sort_order == "desc"),
        )
        items = items[skip : skip + limit]
    else:
        sort_col = getattr(ActionItem, sort_by, ActionItem.created_at)
        query = query.order_by(asc(sort_col) if sort_order == "asc" else desc(sort_col))
        items = query.offset(skip).limit(limit).all()

    return templates.TemplateResponse(
        "partials/action_items_list.html",
        {
            "request": request,
            "items": items,
            "has_active_filter": _has_active_filter(search, status, assignee, priority),
        },
    )


@router.get("/partials/decisions-list", response_class=HTMLResponse)
async def decisions_list_partial(
    request: Request,
    search: Optional[str] = None,
    category: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    query = db.query(Decision).options(joinedload(Decision.transcript))

    if search:
        like = f"%{search}%"
        query = query.filter(Decision.text.ilike(like))

    if category:
        query = query.filter(Decision.category == category)

    sort_col = getattr(Decision, sort_by, Decision.created_at)
    query = query.order_by(asc(sort_col) if sort_order == "asc" else desc(sort_col))
    items = query.offset(skip).limit(limit).all()

    return templates.TemplateResponse(
        "partials/decisions_list.html",
        {
            "request": request,
            "items": items,
            "has_active_filter": _has_active_filter(search, category),
        },
    )


