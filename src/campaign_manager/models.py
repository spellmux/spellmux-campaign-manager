"""Authoritative relational models."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class CampaignRole(str, Enum):
    OWNER = "owner"
    GM = "gm"
    PLAYER = "player"


class SessionStatus(str, Enum):
    CREATED = "created"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    REVIEW = "review"
    APPROVED = "approved"
    PUBLISHED = "published"
    FAILED = "failed"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(512))
    is_instance_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    user: Mapped[User] = relationship()


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    game_system: Mapped[str] = mapped_column(String(120), default="")
    play_mode: Mapped[str] = mapped_column(String(40), default="")
    vtt: Mapped[str] = mapped_column(String(160), default="")
    character_source: Mapped[str] = mapped_column(String(160), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CampaignMembership(Base):
    __tablename__ = "campaign_memberships"
    __table_args__ = (UniqueConstraint("campaign_id", "user_id", name="uq_campaign_membership_user"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    campaign: Mapped[Campaign] = relationship()
    user: Mapped[User] = relationship()


class CampaignGuideEntry(Base):
    __tablename__ = "campaign_guide_entries"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "kind",
            "canonical_name",
            name="uq_campaign_guide_kind_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    canonical_name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(20), default="gm")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CampaignGuideFact(Base):
    """A sourced, reviewable piece of lore attached to a canonical guide entry."""

    __tablename__ = "campaign_guide_facts"
    __table_args__ = (Index("ix_campaign_guide_facts_entry", "guide_entry_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    guide_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_guide_entries.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_proposals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(40), default="session_detail")
    value: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="canonical")
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    visibility: Mapped[str] = mapped_column(String(20), default="gm")
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SpeakerProfile(Base):
    __tablename__ = "speaker_profiles"
    __table_args__ = (
        UniqueConstraint("campaign_id", "display_name", name="uq_speaker_profile_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SpeakerCharacterAssignment(Base):
    __tablename__ = "speaker_character_assignments"
    __table_args__ = (
        UniqueConstraint(
            "speaker_profile_id", "guide_entry_id", "session_id",
            name="uq_speaker_character_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    speaker_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("speaker_profiles.id", ondelete="CASCADE"), index=True
    )
    guide_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_guide_entries.id", ondelete="CASCADE")
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnalysisRun(Base):
    """One analysis of one transcript, and the findings it produced.

    A session may be transcribed and analyzed more than once. Without an explicit
    run, generations of findings pile up in the same review queue and downstream
    readers cannot tell them apart: a player page would render approved findings
    from every generation at once. Selecting a run selects its transcript too,
    because the transcript is the run's input.
    """

    __tablename__ = "analysis_runs"
    __table_args__ = (Index("ix_analysis_runs_session", "session_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    # The transcript this run read; selecting the run selects this transcript.
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="running")
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AnalysisProposal(Base):
    """A machine- or human-authored session fact awaiting GM judgment."""

    __tablename__ = "analysis_proposals"
    __table_args__ = (Index("ix_analysis_proposals_session_status", "session_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    # Which analysis produced this finding; the review queue and every downstream
    # reader scope to the session's active run so generations cannot mix.
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    lane: Mapped[str] = mapped_column(String(20), default="story")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    visibility: Mapped[str] = mapped_column(String(20), default="gm")
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    provider: Mapped[str] = mapped_column(String(80), default="manual")
    model: Mapped[str] = mapped_column(String(120), default="")
    run_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    promoted_guide_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_guide_entries.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChronicleEntry(Base):
    """Editable session-facing presentation of an approved analysis finding."""

    __tablename__ = "chronicle_entries"
    __table_args__ = (
        UniqueConstraint("session_id", "source_proposal_id", name="uq_chronicle_source_proposal"),
        Index("ix_chronicle_entries_session_section", "session_id", "section", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    source_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_proposals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    section: Mapped[str] = mapped_column(String(30))
    entry_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    visibility: Mapped[str] = mapped_column(String(20), default="gm")
    entry_metadata: Mapped[dict[str, object]] = mapped_column("metadata", JSON, default=dict)
    # Set when a human edits the entry. Approving a later generation's equivalent
    # finding must not silently overwrite hand-written canon.
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    session_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default=SessionStatus.CREATED.value)
    # The analysis generation currently in use. Switching it switches which
    # findings are reviewable and which feed publication drafts.
    active_analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SpeakerReview(Base):
    __tablename__ = "speaker_reviews"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "cluster_label", "start_seconds", name="uq_speaker_review_clip"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    # Which diarization's clusters this review describes. A second diarization
    # renumbers the clusters, so a review carried over from the previous one can
    # point at a different person entirely.
    diarization_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    cluster_label: Mapped[str] = mapped_column(String(80))
    start_seconds: Mapped[int] = mapped_column(Integer)
    end_seconds: Mapped[int] = mapped_column(Integer)
    speaker_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("speaker_profiles.id", ondelete="SET NULL"), nullable=True
    )
    disposition: Mapped[str] = mapped_column(String(20), default="confirmed")
    approved_reference: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    reviewed_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    speaker_profile: Mapped[SpeakerProfile | None] = relationship()


class SpeakerVoiceprint(Base):
    """A reusable voice centroid for one campaign speaker.

    Diarization cluster labels are session-local, so identity cannot carry across
    sessions without an enrolled vector. Centroids are built from clips a GM
    approved as references, which are cleaner than a whole-cluster average that
    would include the crosstalk they excluded.
    """

    __tablename__ = "speaker_voiceprints"
    __table_args__ = (
        UniqueConstraint(
            "speaker_profile_id", "embedding_model", name="uq_speaker_voiceprint_model"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    speaker_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("speaker_profiles.id", ondelete="CASCADE"), index=True
    )
    # Vectors from different models are not comparable, so each is kept separately.
    embedding_model: Mapped[str] = mapped_column(String(160))
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    # Total approved reference audio behind the centroid; longer is more reliable.
    sample_seconds: Mapped[float] = mapped_column(default=0.0)
    source_session_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    speaker_profile: Mapped[SpeakerProfile] = relationship()


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    relative_path: Mapped[str] = mapped_column(String(500), unique=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    visibility: Mapped[str] = mapped_column(String(20), default="gm")
    # Set when a newer generation of the same kind replaces this one. Superseded
    # artifacts stay on disk and in the row set so a worse re-run can be undone,
    # but no reader picks them up.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SessionPublication(Base):
    __tablename__ = "session_publications"
    __table_args__ = (UniqueConstraint("session_id", "revision", name="uq_session_publication_revision"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    target_path: Mapped[str] = mapped_column(String(500))
    source_proposal_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    last_published_blob_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created_at", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=True
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default=JobStatus.QUEUED.value)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    queue_position: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ComputeWorker(Base):
    """An administrator-managed endpoint capable of running processing jobs."""

    __tablename__ = "compute_workers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    provider: Mapped[str] = mapped_column(String(40), default="ollama")
    base_url: Mapped[str] = mapped_column(String(500))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    analysis_model: Mapped[str] = mapped_column(String(160), default="")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    concurrency: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_status: Mapped[str] = mapped_column(String(30), default="unknown")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_models: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ProcessingControl(Base):
    __tablename__ = "processing_controls"

    kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
