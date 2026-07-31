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


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    title: str
    session_date: date | None
    status: str
    created_at: datetime


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: str
    attempts: int


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
