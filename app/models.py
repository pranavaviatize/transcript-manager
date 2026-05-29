from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Table, Index, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

transcript_participants = Table(
    "transcript_participants",
    Base.metadata,
    Column("transcript_id", Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), primary_key=True),
    Column("participant_id", Integer, ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
)

transcript_tags = Table(
    "transcript_tags",
    Base.metadata,
    Column("transcript_id", Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    storage_path = Column(String(500), nullable=False)
    content_raw = Column(Text, default="")
    content_clean = Column(Text, default="")
    title = Column(String(500), default="")
    summary = Column(Text, default="")
    meeting_date = Column(DateTime, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    word_count = Column(Integer, default=0)
    sentiment = Column(String(20), default="neutral")
    meeting_type = Column(String(50), default="general")  # standup, code-review, architecture, planning, retro, 1-1, interview, demo
    status = Column(String(20), default="uploaded")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    participants = relationship("Participant", secondary=transcript_participants, back_populates="transcripts")
    tags = relationship("Tag", secondary=transcript_tags, back_populates="transcripts")
    action_items = relationship("ActionItem", back_populates="transcript", cascade="all, delete-orphan")
    code_blocks = relationship("CodeBlock", back_populates="transcript", cascade="all, delete-orphan")
    decisions = relationship("Decision", back_populates="transcript", cascade="all, delete-orphan")
    speaker_stats = relationship("SpeakerStat", back_populates="transcript", cascade="all, delete-orphan")
    images = relationship("Image", back_populates="transcript", cascade="all, delete-orphan", order_by="Image.created_at")
    chunks = relationship("TranscriptChunk", back_populates="transcript", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_transcripts_status", "status"),
        Index("idx_transcripts_meeting_date", "meeting_date"),
        Index("idx_transcripts_created", "created_at"),
        Index("idx_transcripts_type", "meeting_type"),
    )


class Participant(Base):
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    transcripts = relationship("Transcript", secondary=transcript_participants, back_populates="participants")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    color = Column(String(7), default="#6366f1")
    is_auto = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    transcripts = relationship("Transcript", secondary=transcript_tags, back_populates="tags")


class ActionItem(Base):
    __tablename__ = "action_items"

    id = Column(Integer, primary_key=True, index=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    text = Column(Text, nullable=False)
    assignee = Column(String(255), nullable=True)
    completed = Column(Boolean, default=False)
    priority = Column(String(20), default="normal")  # low, normal, high, urgent
    created_at = Column(DateTime, default=datetime.utcnow)
    transcript = relationship("Transcript", back_populates="action_items")


class CodeBlock(Base):
    __tablename__ = "code_blocks"

    id = Column(Integer, primary_key=True, index=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    language = Column(String(50), nullable=True)
    code = Column(Text, nullable=False)
    context = Column(Text, default="")  # surrounding text for context
    created_at = Column(DateTime, default=datetime.utcnow)
    transcript = relationship("Transcript", back_populates="code_blocks")


class Decision(Base):
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True, index=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    text = Column(Text, nullable=False)
    category = Column(String(50), default="general")  # architecture, process, product, tech-stack, timeline
    created_at = Column(DateTime, default=datetime.utcnow)
    transcript = relationship("Transcript", back_populates="decisions")


class Image(Base):
    __tablename__ = "transcript_images"

    id = Column(Integer, primary_key=True, index=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    storage_path = Column(String(500), nullable=False)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    caption = Column(String(500), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    transcript = relationship("Transcript", back_populates="images")


class SpeakerStat(Base):
    __tablename__ = "speaker_stats"

    id = Column(Integer, primary_key=True, index=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    speaker_name = Column(String(255), nullable=False)
    word_count = Column(Integer, default=0)
    estimated_minutes = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    transcript = relationship("Transcript", back_populates="speaker_stats")


class TranscriptChunk(Base):
    __tablename__ = "transcript_chunks"

    id = Column(Integer, primary_key=True, index=True)
    transcript_id = Column(Integer, ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    speaker = Column(String(255), nullable=True)
    start_word = Column(Integer, default=0)
    end_word = Column(Integer, default=0)
    content = Column(Text, nullable=False)
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    transcript = relationship("Transcript", back_populates="chunks")

    __table_args__ = (
        Index("idx_chunks_transcript", "transcript_id"),
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), default="New chat")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)  # user | assistant
    content = Column(Text, nullable=False)
    sources = Column(Text, default="")  # JSON array of {id, title} for assistant turns
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        Index("idx_chat_messages_session", "session_id"),
    )
