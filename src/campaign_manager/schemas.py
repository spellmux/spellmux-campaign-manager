"""Versioned API request and response schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

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


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str
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


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: str
    priority: int
    cancel_requested: bool
    attempts: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class QueueJobResponse(JobResponse):
    session_id: uuid.UUID | None
    session_title: str | None
    campaign_id: uuid.UUID | None
    campaign_name: str | None


class JobPriorityUpdate(BaseModel):
    priority: int = Field(ge=-100, le=100)


class ProcessingControlUpdate(BaseModel):
    paused: bool


class ProcessingControlResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    paused: bool
    updated_at: datetime


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
    "character",
    "location",
    "faction",
    "item",
    "spell",
    "quest",
    "creature",
    "deity",
    "rule",
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


ANALYSIS_KINDS = {
    "session_summary", "character", "location", "item", "spell", "creature",
    "quest", "faction", "deity", "rule", "important_decision", "unresolved_question",
}


class ProposalEvidence(BaseModel):
    quote: str = Field(min_length=1, max_length=20_000)
    artifact_id: uuid.UUID | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)


class AnalysisProposalCreate(BaseModel):
    kind: str
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

    @field_validator("aliases")
    @classmethod
    def clean_analysis_aliases(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(alias.strip() for alias in value if alias.strip()))


class AnalysisProposalUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=50_000)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    visibility: str = "gm"

    @field_validator("visibility")
    @classmethod
    def valid_visibility(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"gm", "player"}:
            raise ValueError("Visibility must be gm or player")
        return normalized


class AnalysisProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    kind: str
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
