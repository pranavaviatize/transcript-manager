from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse, FileResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, asc, or_, and_, text
from typing import List, Optional
from datetime import datetime
import os
import io
import logging
import uuid

from app.database import get_db, SessionLocal
from app.models import Transcript, Participant, Tag, ActionItem, CodeBlock, Decision, SpeakerStat, Image
from app.schemas import (
    TranscriptListItem, TranscriptDetail, TranscriptUpdate,
    TagOut, TagCreate, TagUpdate, ParticipantOut, ActionItemOut, StatsOut, ImageOut,
)
from app.services.file_processor import process_transcript, save_upload
from app.services.ai_enricher import enrich_transcript
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


async def _enrich_in_background(transcript_id: int):
    """Run AI enrichment with its own DB session — request session is closed by now."""
    db = SessionLocal()
    try:
        transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
        if not transcript:
            return

        known_tags = [name for (name,) in db.query(Tag.name).all()]
        known_participants = [name for (name,) in db.query(Participant.name).all()]
        result = await enrich_transcript(
            transcript.content_clean,
            known_tags=known_tags,
            known_participants=known_participants,
        )
        if not result:
            transcript.status = "failed"
            db.commit()
            return

        transcript.title = result.get("title") or transcript.title or transcript.filename
        transcript.summary = result.get("summary", "")
        transcript.sentiment = result.get("sentiment", "neutral")
        transcript.meeting_type = result.get("meeting_type", "general")

        if result.get("meeting_date"):
            try:
                transcript.meeting_date = datetime.strptime(result["meeting_date"], "%Y-%m-%d")
            except ValueError:
                pass
        if result.get("duration_minutes"):
            try:
                transcript.duration_minutes = int(result["duration_minutes"])
            except (TypeError, ValueError):
                pass

        for pname in result.get("participants", []):
            p = db.query(Participant).filter(Participant.name == pname).first()
            if not p:
                p = Participant(name=pname)
                db.add(p)
                db.flush()
            if p not in transcript.participants:
                transcript.participants.append(p)

        tag_palette = ["#e8a444", "#5fa676", "#b489d4", "#6b9bd4", "#d56b6b", "#d4a04a", "#8eb8a6", "#c98ab0"]
        for i, tname in enumerate(result.get("tags", [])):
            tag = db.query(Tag).filter(Tag.name == tname).first()
            if not tag:
                tag = Tag(name=tname, color=tag_palette[i % len(tag_palette)], is_auto=True)
                db.add(tag)
                db.flush()
            if tag not in transcript.tags:
                transcript.tags.append(tag)

        for item in result.get("action_items", []):
            db.add(ActionItem(
                transcript_id=transcript.id,
                text=item["text"],
                assignee=item.get("assignee"),
                priority=item.get("priority", "normal"),
            ))

        for d in result.get("decisions", []):
            db.add(Decision(
                transcript_id=transcript.id,
                text=d["text"],
                category=d.get("category", "general"),
            ))

        for cb in result.get("code_blocks", []):
            db.add(CodeBlock(
                transcript_id=transcript.id,
                language=cb.get("language", "text"),
                code=cb["code"],
                context=cb.get("context", ""),
            ))

        transcript.status = "completed"
        db.commit()
    except Exception as e:
        logger.exception("Background enrichment failed for transcript %s: %s", transcript_id, e)
        try:
            t = db.query(Transcript).filter(Transcript.id == transcript_id).first()
            if t:
                t.status = "failed"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _build_list_item(t: Transcript) -> dict:
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
    }


@router.post("/api/transcripts/upload")
async def upload_transcript(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    storage_path = save_upload(content, file.filename, settings.upload_dir)
    processed = process_transcript(text, file.filename)

    transcript = Transcript(
        filename=file.filename,
        storage_path=storage_path,
        content_raw=processed["content_raw"],
        content_clean=processed["content_clean"],
        word_count=processed["word_count"],
        meeting_date=processed["meeting_date"],
        status="processing",
    )
    db.add(transcript)
    db.commit()
    db.refresh(transcript)

    for stat in processed.get("speaker_stats", []):
        db.add(SpeakerStat(
            transcript_id=transcript.id,
            speaker_name=stat["speaker_name"],
            word_count=stat["word_count"],
            estimated_minutes=stat["estimated_minutes"],
        ))
    db.commit()

    transcript_id = transcript.id
    background_tasks.add_task(_enrich_in_background, transcript_id)

    return {"id": transcript_id, "status": "processing"}


@router.post("/api/transcripts/paste")
async def paste_transcript(
    background_tasks: BackgroundTasks,
    text: str = Form(...),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    filename = f"pasted_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    storage_path = save_upload(text.encode("utf-8"), filename, settings.upload_dir)

    processed = process_transcript(text, filename)

    transcript = Transcript(
        filename=filename,
        storage_path=storage_path,
        content_raw=processed["content_raw"],
        content_clean=processed["content_clean"],
        word_count=processed["word_count"],
        meeting_date=processed["meeting_date"],
        status="processing",
    )
    db.add(transcript)
    db.commit()
    db.refresh(transcript)

    for stat in processed.get("speaker_stats", []):
        db.add(SpeakerStat(
            transcript_id=transcript.id,
            speaker_name=stat["speaker_name"],
            word_count=stat["word_count"],
            estimated_minutes=stat["estimated_minutes"],
        ))
    db.commit()

    transcript_id = transcript.id
    background_tasks.add_task(_enrich_in_background, transcript_id)

    return {"id": transcript_id, "status": "processing"}


@router.post("/api/transcripts/{transcript_id}/enrich")
async def enrich_transcript_endpoint(transcript_id: int, db: Session = Depends(get_db)):
    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not transcript:
        raise HTTPException(status_code=404, detail="Not found")

    transcript.status = "processing"
    db.commit()

    known_tags = [name for (name,) in db.query(Tag.name).all()]
    known_participants = [name for (name,) in db.query(Participant.name).all()]
    result = await enrich_transcript(
        transcript.content_clean,
        known_tags=known_tags,
        known_participants=known_participants,
    )
    if not result:
        transcript.status = "failed"
        db.commit()
        return {"status": "failed"}

    transcript.title = result.get("title", transcript.title) or transcript.filename
    transcript.summary = result.get("summary", "")
    transcript.sentiment = result.get("sentiment", "neutral")
    transcript.meeting_type = result.get("meeting_type", "general")

    if result.get("meeting_date"):
        try:
            transcript.meeting_date = datetime.strptime(result["meeting_date"], "%Y-%m-%d")
        except ValueError:
            pass
    if result.get("duration_minutes"):
        transcript.duration_minutes = int(result["duration_minutes"])

    # Participants
    for pname in result.get("participants", []):
        p = db.query(Participant).filter(Participant.name == pname).first()
        if not p:
            p = Participant(name=pname)
            db.add(p)
            db.flush()
        if p not in transcript.participants:
            transcript.participants.append(p)

    # Tags
    for tname in result.get("tags", []):
        tag = db.query(Tag).filter(Tag.name == tname).first()
        if not tag:
            tag = Tag(name=tname, is_auto=True)
            db.add(tag)
            db.flush()
        if tag not in transcript.tags:
            transcript.tags.append(tag)

    # Action items
    for item in result.get("action_items", []):
        ai = ActionItem(
            transcript_id=transcript.id,
            text=item["text"],
            assignee=item.get("assignee"),
            priority=item.get("priority", "normal"),
        )
        db.add(ai)

    # Decisions
    for d in result.get("decisions", []):
        dec = Decision(
            transcript_id=transcript.id,
            text=d["text"],
            category=d.get("category", "general"),
        )
        db.add(dec)

    # Code blocks from AI (supplement file processor extraction)
    for cb in result.get("code_blocks", []):
        block = CodeBlock(
            transcript_id=transcript.id,
            language=cb.get("language", "text"),
            code=cb["code"],
            context=cb.get("context", ""),
        )
        db.add(block)

    transcript.status = "completed"
    db.commit()

    return {"status": "completed"}


@router.get("/api/transcripts")
async def list_transcripts(
    search: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    participants: Optional[str] = None,
    tags: Optional[str] = None,
    meeting_type: Optional[str] = None,
    has_action_items: Optional[bool] = None,
    has_decisions: Optional[bool] = None,
    has_code: Optional[bool] = None,
    status: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
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

    if date_from:
        query = query.filter(Transcript.meeting_date >= date_from)
    if date_to:
        query = query.filter(Transcript.meeting_date <= date_to)
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

    if has_action_items is not None:
        if has_action_items:
            query = query.filter(Transcript.action_items.any())
        else:
            query = query.filter(~Transcript.action_items.any())

    if has_decisions is not None:
        if has_decisions:
            query = query.filter(Transcript.decisions.any())
        else:
            query = query.filter(~Transcript.decisions.any())

    if has_code is not None:
        if has_code:
            query = query.filter(Transcript.code_blocks.any())
        else:
            query = query.filter(~Transcript.code_blocks.any())

    sort_col = getattr(Transcript, sort_by, Transcript.created_at)
    if sort_order == "asc":
        query = query.order_by(asc(sort_col))
    else:
        query = query.order_by(desc(sort_col))

    transcripts = query.offset(skip).limit(limit).all()
    return [_build_list_item(t) for t in transcripts]


@router.get("/api/transcripts/{transcript_id}", response_model=TranscriptDetail)
async def get_transcript(transcript_id: int, db: Session = Depends(get_db)):
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
    return t


@router.patch("/api/transcripts/{transcript_id}")
async def update_transcript(
    transcript_id: int,
    update: TranscriptUpdate,
    db: Session = Depends(get_db),
):
    t = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    if update.title is not None:
        t.title = update.title
    if update.summary is not None:
        t.summary = update.summary
    if update.meeting_date is not None:
        t.meeting_date = update.meeting_date
    if update.duration_minutes is not None:
        t.duration_minutes = update.duration_minutes
    if update.sentiment is not None:
        t.sentiment = update.sentiment
    if update.meeting_type is not None:
        t.meeting_type = update.meeting_type
    db.commit()
    return {"ok": True}


@router.delete("/api/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: int, db: Session = Depends(get_db)):
    t = db.query(Transcript).options(joinedload(Transcript.images)).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    if os.path.exists(t.storage_path):
        os.remove(t.storage_path)
    for img in t.images:
        if os.path.exists(img.storage_path):
            os.remove(img.storage_path)
    db.delete(t)
    db.commit()
    return {"ok": True}


ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp", "image/svg+xml"}
IMAGE_EXT_FOR_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


@router.post("/api/transcripts/{transcript_id}/images", response_model=List[ImageOut])
async def upload_images(
    transcript_id: int,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    t = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Not found")

    image_dir = os.path.join("data", "images", str(transcript_id))
    os.makedirs(image_dir, exist_ok=True)

    saved: List[Image] = []
    for f in files:
        content_type = (f.content_type or "").lower()
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported image type: {content_type or 'unknown'}")
        ext = IMAGE_EXT_FOR_TYPE[content_type]
        name = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(image_dir, name)
        with open(path, "wb") as out:
            out.write(await f.read())
        img = Image(
            transcript_id=transcript_id,
            storage_path=path,
            original_filename=f.filename or name,
            content_type=content_type,
        )
        db.add(img)
        saved.append(img)
    db.commit()
    for img in saved:
        db.refresh(img)
    return saved


@router.get("/api/images/{image_id}/file")
async def serve_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(Image).filter(Image.id == image_id).first()
    if not img or not os.path.exists(img.storage_path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(img.storage_path, media_type=img.content_type, filename=img.original_filename)


@router.delete("/api/images/{image_id}")
async def delete_image(image_id: int, db: Session = Depends(get_db)):
    img = db.query(Image).filter(Image.id == image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Not found")
    if os.path.exists(img.storage_path):
        os.remove(img.storage_path)
    db.delete(img)
    db.commit()
    return {"ok": True}


@router.post("/api/transcripts/{transcript_id}/tags/{tag_name}")
async def add_tag_to_transcript(transcript_id: int, tag_name: str, db: Session = Depends(get_db)):
    t = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    tag = db.query(Tag).filter(Tag.name == tag_name).first()
    if not tag:
        tag = Tag(name=tag_name, is_auto=False)
        db.add(tag)
        db.flush()
    if tag not in t.tags:
        t.tags.append(tag)
    db.commit()
    return {"ok": True}


@router.delete("/api/transcripts/{transcript_id}/tags/{tag_id}")
async def remove_tag_from_transcript(transcript_id: int, tag_id: int, db: Session = Depends(get_db)):
    t = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if tag and tag in t.tags:
        t.tags.remove(tag)
    db.commit()
    return {"ok": True}


@router.patch("/api/action-items/{action_item_id}")
async def toggle_action_item(action_item_id: int, db: Session = Depends(get_db)):
    ai = db.query(ActionItem).filter(ActionItem.id == action_item_id).first()
    if not ai:
        raise HTTPException(status_code=404, detail="Not found")
    ai.completed = not ai.completed
    db.commit()
    return {"completed": ai.completed}


TAG_PALETTE = ["#e8a444", "#5fa676", "#b489d4", "#6b9bd4", "#d56b6b", "#d4a04a", "#8eb8a6", "#c98ab0"]


def _normalize_tag_name(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


@router.get("/api/tags", response_model=List[TagOut])
async def list_tags(db: Session = Depends(get_db)):
    return db.query(Tag).order_by(Tag.name).all()


@router.post("/api/tags", response_model=TagOut)
async def create_tag(
    name: str = Form(...),
    color: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    name = _normalize_tag_name(name)
    if not name:
        raise HTTPException(status_code=400, detail="Tag name required")
    existing = db.query(Tag).filter(Tag.name == name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Tag already exists")
    if not color:
        color = TAG_PALETTE[db.query(func.count(Tag.id)).scalar() % len(TAG_PALETTE)]
    tag = Tag(name=name, color=color, is_auto=False)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@router.patch("/api/tags/{tag_id}", response_model=TagOut)
async def update_tag(
    tag_id: int,
    name: Optional[str] = Form(None),
    color: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Not found")
    if name is not None:
        new_name = _normalize_tag_name(name)
        if not new_name:
            raise HTTPException(status_code=400, detail="Tag name required")
        if new_name != tag.name:
            clash = db.query(Tag).filter(Tag.name == new_name, Tag.id != tag_id).first()
            if clash:
                raise HTTPException(status_code=409, detail="Another tag with this name already exists")
            tag.name = new_name
    if color is not None and color != "":
        tag.color = color
    db.commit()
    db.refresh(tag)
    return tag


@router.delete("/api/tags/{tag_id}")
async def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(tag)
    db.commit()
    return {"ok": True}


@router.get("/api/participants", response_model=List[ParticipantOut])
async def list_participants(db: Session = Depends(get_db)):
    return db.query(Participant).order_by(Participant.name).all()


@router.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)):
    total = db.query(func.count(Transcript.id)).scalar()
    this_week = db.query(func.count(Transcript.id)).filter(
        Transcript.created_at >= func.datetime("now", "-7 days")
    ).scalar()
    pending_actions = db.query(func.count(ActionItem.id)).filter(ActionItem.completed == False).scalar()
    total_code = db.query(func.count(CodeBlock.id)).scalar()
    total_decisions = db.query(func.count(Decision.id)).scalar()

    type_breakdown = {}
    rows = db.query(Transcript.meeting_type, func.count(Transcript.id)).group_by(Transcript.meeting_type).all()
    for mt, count in rows:
        type_breakdown[mt or "general"] = count

    return {
        "total_transcripts": total,
        "this_week": this_week,
        "pending_action_items": pending_actions,
        "total_code_blocks": total_code,
        "total_decisions": total_decisions,
        "meeting_type_breakdown": type_breakdown,
    }


@router.get("/api/transcripts/{transcript_id}/export.md")
async def export_markdown(transcript_id: int, db: Session = Depends(get_db)):
    t = db.query(Transcript).options(
        joinedload(Transcript.participants),
        joinedload(Transcript.tags),
        joinedload(Transcript.action_items),
        joinedload(Transcript.code_blocks),
        joinedload(Transcript.decisions),
        joinedload(Transcript.speaker_stats),
    ).filter(Transcript.id == transcript_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Not found")

    lines = []
    lines.append(f"# {t.title or t.filename}")
    lines.append("")
    lines.append(f"**Date:** {t.meeting_date.strftime('%Y-%m-%d') if t.meeting_date else 'Unknown'}")
    lines.append(f"**Type:** {t.meeting_type}")
    lines.append(f"**Duration:** {t.duration_minutes} min" if t.duration_minutes else "**Duration:** Unknown")
    lines.append(f"**Participants:** {', '.join(p.name for p in t.participants)}")
    lines.append(f"**Tags:** {', '.join(tag.name for tag in t.tags)}")
    lines.append("")

    if t.summary:
        lines.append("## Summary")
        lines.append(t.summary)
        lines.append("")

    if t.decisions:
        lines.append("## Decisions")
        for d in t.decisions:
            lines.append(f"- [{d.category}] {d.text}")
        lines.append("")

    if t.action_items:
        lines.append("## Action Items")
        for ai in t.action_items:
            check = "[x]" if ai.completed else "[ ]"
            assignee = f" (@{ai.assignee})" if ai.assignee else ""
            lines.append(f"- {check} {ai.text}{assignee}")
        lines.append("")

    if t.code_blocks:
        lines.append("## Code Snippets")
        for cb in t.code_blocks:
            lang = cb.language or ""
            lines.append(f"### {cb.context or 'Snippet'}")
            lines.append(f"```{lang}")
            lines.append(cb.code)
            lines.append("```")
            lines.append("")

    lines.append("## Transcript")
    lines.append(t.content_clean)

    md = "\n".join(lines)
    filename = f"{t.title or 'transcript'}.md".replace(" ", "_")

    return StreamingResponse(
        io.StringIO(md),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
