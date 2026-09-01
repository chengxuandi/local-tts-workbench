from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    fish_reference_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    local_reference_audio: Mapped[str | None] = mapped_column(String(500), nullable=True)
    clone_status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    clone_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_emotion: Mapped[str] = mapped_column(Text, default="", nullable=False)
    default_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    generations: Mapped[list[Generation]] = relationship(back_populates="character")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    next_sequence: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generations: Mapped[list[Generation]] = relationship(back_populates="project")


class Generation(Base):
    __tablename__ = "generations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    character_id: Mapped[int | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    character_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    emotion_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    effective_fish_text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(40), nullable=False)
    speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    utf8_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    output_audio_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    output_metadata_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="generating", nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    project: Mapped[Project] = relationship(back_populates="generations")
    character: Mapped[Character | None] = relationship(back_populates="generations")
