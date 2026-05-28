from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, asc, or_
from typing import Optional, List
from datetime import datetime

from app.database import get_db
from app.models import Transcript, Tag, Participant
from app.templating import templates

router = APIRouter()


def _get_common_context(db: Session):
    tags = db.query(Tag).order_by(Tag.name).all()
    participants = db.query(Participant).order_by(Participant.name).all()
    return {
        "all_tags": tags,
        "all_participants": participants,
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
async def transcripts_page(request: Request, db: Session = Depends(get_db)):
    ctx = _get_common_context(db)
    ctx["request"] = request
    return templates.TemplateResponse("transcripts.html", ctx)


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
