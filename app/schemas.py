from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class TagOut(BaseModel):
    id: int
    name: str
    color: str
    is_auto: bool
    class Config:
        from_attributes = True


class ParticipantOut(BaseModel):
    id: int
    name: str
    email: Optional[str]
    class Config:
        from_attributes = True


class ActionItemOut(BaseModel):
    id: int
    text: str
    assignee: Optional[str]
    completed: bool
    priority: str
    class Config:
        from_attributes = True


class CodeBlockOut(BaseModel):
    id: int
    language: Optional[str]
    code: str
    context: str
    class Config:
        from_attributes = True


class DecisionOut(BaseModel):
    id: int
    text: str
    category: str
    class Config:
        from_attributes = True


class ImageOut(BaseModel):
    id: int
    original_filename: str
    content_type: str
    caption: str
    created_at: datetime
    class Config:
        from_attributes = True


class SpeakerStatOut(BaseModel):
    id: int
    speaker_name: str
    word_count: int
    estimated_minutes: float
    class Config:
        from_attributes = True


class TranscriptListItem(BaseModel):
    id: int
    title: str
    filename: str
    meeting_date: Optional[datetime]
    duration_minutes: Optional[int]
    word_count: int
    sentiment: str
    meeting_type: str
    status: str
    created_at: datetime
    participants: List[ParticipantOut]
    tags: List[TagOut]
    action_item_count: int
    decision_count: int
    code_block_count: int
    class Config:
        from_attributes = True


class TranscriptDetail(BaseModel):
    id: int
    filename: str
    content_clean: str
    title: str
    summary: str
    meeting_date: Optional[datetime]
    duration_minutes: Optional[int]
    word_count: int
    sentiment: str
    meeting_type: str
    status: str
    created_at: datetime
    updated_at: datetime
    participants: List[ParticipantOut]
    tags: List[TagOut]
    action_items: List[ActionItemOut]
    code_blocks: List[CodeBlockOut]
    decisions: List[DecisionOut]
    speaker_stats: List[SpeakerStatOut]
    images: List[ImageOut]
    class Config:
        from_attributes = True


class TranscriptUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    meeting_date: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    sentiment: Optional[str] = None
    meeting_type: Optional[str] = None


class StatsOut(BaseModel):
    total_transcripts: int
    this_week: int
    pending_action_items: int
    total_code_blocks: int
    total_decisions: int
    meeting_type_breakdown: dict
