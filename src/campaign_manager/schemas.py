"""Versioned API request and response schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from campaign_manager.auth import normalize_email


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return normalize_email(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    is_instance_admin: bool


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str | None = Field(default=None, min_length=1, max_length=100)
    description: str = Field(default="", max_length=20_000)


class CampaignUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20_000)
    game_system: str = Field(default="", max_length=120)
    play_mode: str = Field(default="", max_length=40)
    vtt: str = Field(default="", max_length=160)
    character_source: str = Field(default="", max_length=160)
    notes: str = Field(default="", max_length=20_000)

    @field_validator("play_mode")
    @classmethod
    def valid_play_mode(cls, value: str) -> str:
        normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized not in {"", "in_person", "online", "hybrid"}:
            raise ValueError("Play mode must be in person, online, hybrid, or blank")
        return normalized


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str
    game_system: str
    play_mode: str
    vtt: str
    character_source: str
    notes: str
    created_at: datetime
    role: str


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    session_date: date | None = None
    description: str = Field(default="", max_length=20_000)


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    session_date: date | None = None
    description: str = Field(default="", max_length=20_000)


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    title: str
    session_date: date | None
    description: str
    status: str
    created_at: datetime
    # Which generation of findings this session is showing.
    active_analysis_run_id: uuid.UUID | None = None


class ChronicleEntryUpdate(BaseModel):
    section: str = Field(min_length=1, max_length=30)
    entry_type: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=100_000)
    position: int = Field(default=0, ge=0, le=10000)
    visibility: str = "gm"

    @field_validator("visibility")
    @classmethod
    def valid_chronicle_visibility(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"gm", "player"}:
            raise ValueError("Visibility must be gm or player")
        return normalized


class ChronicleEntryResponse(ChronicleEntryUpdate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    source_proposal_id: uuid.UUID | None
    metadata: dict[str, object] = Field(validation_alias="entry_metadata", serialization_alias="metadata")
    created_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # Set the first time a human edits the entry. Once set, the entry is canon and
    # a later analysis run will not overwrite it.
    edited_at: datetime | None = None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: str
    priority: int
    queue_position: int
    cancel_requested: bool
    attempts: int
    error: str | None
    payload: dict[str, object]
    created_at: datetime
    updated_at: datetime


class QueueJobResponse(JobResponse):
    session_id: uuid.UUID | None
    session_title: str | None
    campaign_id: uuid.UUID | None
    campaign_name: str | None


class JobPriorityUpdate(BaseModel):
    priority: int = Field(ge=-100, le=100)


class QueueMoveRequest(BaseModel):
    direction: str

    @field_validator("direction")
    @classmethod
    def valid_direction(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"up", "down"}:
            raise ValueError("Direction must be up or down")
        return normalized


class ProcessingControlUpdate(BaseModel):
    paused: bool


class ProcessingControlResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    paused: bool
    updated_at: datetime


class ComputeWorkerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str = "ollama"
    base_url: str = Field(min_length=8, max_length=500)
    capabilities: list[str] = Field(default_factory=lambda: ["analysis"], max_length=10)
    analysis_model: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=0, ge=-100, le=100)
    concurrency: int = Field(default=1, ge=1, le=32)
    enabled: bool = True

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized != "ollama":
            raise ValueError("The first compute-worker release supports Ollama endpoints")
        return normalized

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Base URL must be an HTTP or HTTPS endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Base URL cannot contain credentials, query parameters, or fragments")
        return normalized

    @field_validator("capabilities")
    @classmethod
    def valid_capabilities(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip().casefold() for item in value))
        supported = {"analysis", "transcription", "diarization", "image_generation"}
        if not normalized or any(item not in supported for item in normalized):
            raise ValueError("Choose at least one supported compute capability")
        return normalized


class ComputeWorkerUpdate(ComputeWorkerCreate):
    pass


class ComputeWorkerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider: str
    base_url: str
    capabilities: list[str]
    analysis_model: str
    priority: int
    concurrency: int
    enabled: bool
    last_status: str
    last_error: str | None
    available_models: list[str]
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ComputeWorkerTestResponse(BaseModel):
    worker: ComputeWorkerResponse
    ready: bool
    models: list[str]
    detail: str | None


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    visibility: str
    created_at: datetime
    job: JobResponse | None = None


class TextSourceCreate(BaseModel):
    kind: str
    content: str = Field(min_length=1, max_length=20_000_000)
    filename: str | None = Field(default=None, max_length=255)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"transcript", "notes"}:
            raise ValueError("Text source kind must be transcript or notes")
        return normalized


class TextSourceUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000_000)
    filename: str = Field(min_length=1, max_length=255)


class TranscriptSegmentEdit(BaseModel):
    id: int = Field(ge=0)
    text: str = Field(max_length=20_000)


class TranscriptRevisionCreate(BaseModel):
    segments: list[TranscriptSegmentEdit] = Field(max_length=20_000)


GUIDE_KINDS = {
    "instruction",
    "player_character",
    "npc",
    "location",
    "faction",
    "item",
    "creature",
    "deity",
    # Not entities, but transcription hints the guide carries for whisper.
    "pronunciation",
    "other",
}


class CampaignGuideCreate(BaseModel):
    kind: str
    canonical_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    notes: str = Field(default="", max_length=20_000)
    visibility: str = "gm"

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in GUIDE_KINDS:
            raise ValueError("Unsupported Campaign Guide kind")
        return normalized

    @field_validator("visibility")
    @classmethod
    def valid_visibility(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"gm", "player"}:
            raise ValueError("Visibility must be gm or player")
        return normalized

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(alias.strip() for alias in value if alias.strip()))


class CampaignGuideUpdate(CampaignGuideCreate):
    pass


class GuideSessionReference(BaseModel):
    """A session this entity was encountered in, derived from its sourced facts."""

    session_id: uuid.UUID
    title: str
    session_date: date | None
    fact_count: int


class CampaignGuideResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    kind: str
    canonical_name: str
    aliases: list[str]
    notes: str
    visibility: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # A guide entry is a reference for planning: what is known, and where it was
    # met. The sessions are computed from fact lineage so they cannot drift from
    # the findings that were actually approved, and are never model-authored.
    fact_count: int = 0
    sessions: list[GuideSessionReference] = Field(default_factory=list)


class CampaignGuideFactCreate(BaseModel):
    category: str = Field(default="session_detail", min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=50_000)
    status: str = "canonical"
    confidence: float | None = Field(default=None, ge=0, le=1)
    visibility: str = "gm"
    session_id: uuid.UUID | None = None


class CampaignGuideFactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    guide_entry_id: uuid.UUID
    session_id: uuid.UUID | None
    source_proposal_id: uuid.UUID | None
    category: str
    value: str
    status: str
    confidence: float | None
    visibility: str
    created_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


ANALYSIS_KINDS = {
    "session_summary", "player_character", "npc", "location", "item", "creature",
    "quest", "faction", "deity", "rule", "important_decision", "unresolved_question",
    "scene", "memorable_moment", "follow_up", "table_note",
}


class ProposalEvidence(BaseModel):
    quote: str = Field(min_length=1, max_length=20_000)
    artifact_id: uuid.UUID | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)


class AnalysisProposalCreate(BaseModel):
    kind: str
    lane: str = "story"
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=50_000)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    evidence: list[ProposalEvidence] = Field(default_factory=list, max_length=50)
    confidence: float | None = Field(default=None, ge=0, le=1)
    visibility: str = "gm"
    provider: str = Field(default="manual", max_length=80)
    model: str = Field(default="", max_length=120)
    run_metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def valid_analysis_kind(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in ANALYSIS_KINDS:
            raise ValueError("Unsupported analysis proposal kind")
        return normalized

    @field_validator("visibility")
    @classmethod
    def valid_analysis_visibility(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"gm", "player"}:
            raise ValueError("Visibility must be gm or player")
        return normalized

    @field_validator("lane")
    @classmethod
    def valid_analysis_lane(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"story", "meta"}:
            raise ValueError("Lane must be story or meta")
        return normalized

    @field_validator("aliases")
    @classmethod
    def clean_analysis_aliases(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(alias.strip() for alias in value if alias.strip()))


class AnalysisProposalUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=50_000)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    visibility: str = "gm"
    lane: str = "story"

    @field_validator("visibility")
    @classmethod
    def valid_visibility(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"gm", "player"}:
            raise ValueError("Visibility must be gm or player")
        return normalized

    @field_validator("lane")
    @classmethod
    def valid_lane(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"story", "meta"}:
            raise ValueError("Lane must be story or meta")
        return normalized


class AnalysisProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    kind: str
    lane: str
    title: str
    body: str
    aliases: list[str]
    evidence: list[ProposalEvidence]
    confidence: float | None
    visibility: str
    status: str
    provider: str
    model: str
    run_metadata: dict[str, object]
    promoted_guide_entry_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None


class AnalysisRunCreate(BaseModel):
    source_artifact_id: uuid.UUID | None = None


class AnalysisRunResponse(BaseModel):
    """One generation of findings for a session."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    source_artifact_id: uuid.UUID | None
    job_id: uuid.UUID | None
    provider: str
    model: str
    status: str
    finding_count: int
    notes: str
    created_at: datetime
    completed_at: datetime | None
    # Only the active run's findings reach the review queue, publication drafts,
    # and anything else downstream.
    is_active: bool = False


class PublicationCreate(BaseModel):
    target_path: str | None = Field(default=None, max_length=500)


class PublicationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=2_000_000)
    target_path: str = Field(min_length=1, max_length=500)


class PublicationPublish(BaseModel):
    confirm_overwrite: bool = False


class PublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    revision: int
    title: str
    content: str
    target_path: str
    source_proposal_ids: list[str]
    status: str
    published_commit: str | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


class SpeakerProfileCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=20_000)


class SpeakerProfileUpdate(SpeakerProfileCreate):
    pass


class SpeakerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    display_name: str
    notes: str
    created_at: datetime


class SpeakerCharacterAssignmentCreate(BaseModel):
    speaker_profile_id: uuid.UUID
    guide_entry_id: uuid.UUID
    session_id: uuid.UUID | None = None
    is_primary: bool = False
    notes: str = Field(default="", max_length=20_000)


class SpeakerCharacterAssignmentUpdate(BaseModel):
    guide_entry_id: uuid.UUID
    session_id: uuid.UUID | None = None
    is_primary: bool = False
    notes: str = Field(default="", max_length=20_000)


class SpeakerCharacterAssignmentResponse(BaseModel):
    id: uuid.UUID
    speaker_profile_id: uuid.UUID
    speaker_name: str
    guide_entry_id: uuid.UUID
    character_name: str
    session_id: uuid.UUID | None
    session_title: str | None
    is_primary: bool
    notes: str
    created_at: datetime


class SpeakerVoiceprintResponse(BaseModel):
    id: uuid.UUID
    speaker_profile_id: uuid.UUID
    speaker_name: str
    embedding_model: str
    dimensions: int
    sample_count: int
    sample_seconds: float
    source_session_ids: list[str]
    updated_at: datetime


class SpeakerReviewCreate(BaseModel):
    cluster_label: str = Field(min_length=1, max_length=80)
    start_seconds: int = Field(ge=0)
    end_seconds: int = Field(gt=0)
    speaker_profile_id: uuid.UUID | None = None
    disposition: str = "confirmed"
    approved_reference: bool = False
    notes: str = Field(default="", max_length=20_000)

    @field_validator("disposition")
    @classmethod
    def valid_disposition(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {
            "confirmed", "uncertain", "crosstalk", "noise",
            "music", "background_music", "featured_song",
        }:
            raise ValueError("Unsupported speaker review disposition")
        return normalized


class SpeakerReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    cluster_label: str
    start_seconds: int
    end_seconds: int
    speaker_profile_id: uuid.UUID | None
    speaker_name: str | None = None
    disposition: str
    approved_reference: bool
    notes: str
    created_at: datetime
    updated_at: datetime
