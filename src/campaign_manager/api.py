"""HTTP API entry point."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from campaign_manager import __version__
from campaign_manager.artifacts import ingest_audio, ingest_text
from campaign_manager.auth import authenticate, current_user, issue_token, revoke_token
from campaign_manager.comparison import compare_transcripts
from campaign_manager.compute import effective_analysis_status, probe_ollama
from campaign_manager.config import Settings
from campaign_manager.database import database_session
from campaign_manager.diarization import attribute_transcript_segments, cluster_resolutions
from campaign_manager.models import (
    AnalysisProposal,
    Artifact,
    Campaign,
    CampaignGuideEntry,
    CampaignGuideFact,
    CampaignMembership,
    CampaignRole,
    ChronicleEntry,
    ComputeWorker,
    GameSession,
    Job,
    ProcessingControl,
    SessionPublication,
    SpeakerCharacterAssignment,
    SpeakerProfile,
    SpeakerReview,
    SpeakerVoiceprint,
    User,
)
from campaign_manager.permissions import require_campaign_role
from campaign_manager.publishing import (
    default_target_path,
    publish_to_otterwiki,
    render_player_draft,
    validate_target_path,
)
from campaign_manager.review import (
    create_transcript_revision,
    normalized_audio_clip,
    read_artifact,
)
from campaign_manager.schemas import (
    AnalysisProposalCreate,
    AnalysisProposalResponse,
    AnalysisProposalUpdate,
    AnalysisRunCreate,
    ArtifactResponse,
    CampaignCreate,
    CampaignGuideCreate,
    CampaignGuideFactCreate,
    CampaignGuideFactResponse,
    CampaignGuideResponse,
    CampaignGuideUpdate,
    CampaignResponse,
    CampaignUpdate,
    ChronicleEntryResponse,
    ChronicleEntryUpdate,
    ComputeWorkerCreate,
    ComputeWorkerResponse,
    ComputeWorkerTestResponse,
    ComputeWorkerUpdate,
    JobPriorityUpdate,
    JobResponse,
    LoginRequest,
    ProcessingControlResponse,
    ProcessingControlUpdate,
    PublicationCreate,
    PublicationPublish,
    PublicationResponse,
    PublicationUpdate,
    QueueJobResponse,
    QueueMoveRequest,
    SessionCreate,
    SessionResponse,
    SessionUpdate,
    SpeakerCharacterAssignmentCreate,
    SpeakerCharacterAssignmentResponse,
    SpeakerCharacterAssignmentUpdate,
    SpeakerProfileCreate,
    SpeakerProfileResponse,
    SpeakerProfileUpdate,
    SpeakerReviewCreate,
    SpeakerReviewResponse,
    SpeakerVoiceprintResponse,
    TextSourceCreate,
    TextSourceUpdate,
    TokenResponse,
    TranscriptRevisionCreate,
    UserResponse,
)

_bearer = HTTPBearer(auto_error=False)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().casefold()).strip("-")
    if not slug:
        raise HTTPException(status_code=422, detail="Campaign name cannot produce an empty slug")
    return slug[:100]


def _campaign_response(campaign: Campaign, role: str) -> CampaignResponse:
    return CampaignResponse(
        id=campaign.id,
        slug=campaign.slug,
        name=campaign.name,
        description=campaign.description,
        game_system=campaign.game_system,
        play_mode=campaign.play_mode,
        vtt=campaign.vtt,
        character_source=campaign.character_source,
        notes=campaign.notes,
        created_at=campaign.created_at,
        role=role,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_environment()
    app = FastAPI(title="Campaign Manager", version=__version__)
    static_root = Path(__file__).parent / "static"
    app.mount("/assets", StaticFiles(directory=static_root / "assets"), name="assets")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "media-src 'self' blob:; frame-ancestors 'none'"
        )
        return response

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_root / "index.html")

    @app.get("/api/v1/health", tags=["system"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "environment": resolved.environment,
        }

    @app.get("/api/v1/ready", tags=["system"])
    def readiness(database: Session = Depends(database_session)) -> dict[str, str]:
        database.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.post("/api/v1/auth/login", response_model=TokenResponse, tags=["authentication"])
    def login(request: LoginRequest, database: Session = Depends(database_session)) -> TokenResponse:
        user = authenticate(database, request.email, request.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        raw_token, token = issue_token(database, user)
        return TokenResponse(access_token=raw_token, expires_at=token.expires_at)

    @app.post("/api/v1/auth/logout", status_code=204, tags=["authentication"])
    def logout(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
        database: Session = Depends(database_session),
    ) -> None:
        if credentials is not None:
            revoke_token(database, credentials.credentials)

    @app.get("/api/v1/auth/me", response_model=UserResponse, tags=["authentication"])
    def me(user: User = Depends(current_user)) -> User:
        return user

    @app.get("/api/v1/analysis/status", tags=["analysis-review"])
    def analysis_status(
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> dict[str, object]:
        del user
        return effective_analysis_status(database, resolved)

    def require_instance_admin(user: User) -> None:
        if not user.is_instance_admin:
            raise HTTPException(status_code=403, detail="Instance administrator required")

    @app.get(
        "/api/v1/compute-workers",
        response_model=list[ComputeWorkerResponse],
        tags=["compute-workers"],
    )
    def list_compute_workers(
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[ComputeWorker]:
        require_instance_admin(user)
        return list(database.scalars(
            select(ComputeWorker).order_by(ComputeWorker.priority.desc(), ComputeWorker.name)
        ))

    @app.post(
        "/api/v1/compute-workers",
        response_model=ComputeWorkerResponse,
        status_code=201,
        tags=["compute-workers"],
    )
    def create_compute_worker(
        request: ComputeWorkerCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> ComputeWorker:
        require_instance_admin(user)
        worker = ComputeWorker(**request.model_dump(), created_by_id=user.id)
        database.add(worker)
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(status_code=409, detail="Compute worker name already exists") from exc
        database.refresh(worker)
        return worker

    def managed_compute_worker(
        database: Session, user: User, worker_id: uuid.UUID
    ) -> ComputeWorker:
        require_instance_admin(user)
        worker = database.get(ComputeWorker, worker_id)
        if worker is None:
            raise HTTPException(status_code=404, detail="Compute worker not found")
        return worker

    @app.put(
        "/api/v1/compute-workers/{worker_id}",
        response_model=ComputeWorkerResponse,
        tags=["compute-workers"],
    )
    def update_compute_worker(
        worker_id: uuid.UUID,
        request: ComputeWorkerUpdate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> ComputeWorker:
        worker = managed_compute_worker(database, user, worker_id)
        for field, value in request.model_dump().items():
            setattr(worker, field, value)
        worker.last_status = "unknown"
        worker.last_error = None
        worker.available_models = []
        worker.last_checked_at = None
        worker.updated_at = datetime.now(UTC)
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(status_code=409, detail="Compute worker name already exists") from exc
        database.refresh(worker)
        return worker

    @app.delete(
        "/api/v1/compute-workers/{worker_id}",
        status_code=204,
        tags=["compute-workers"],
    )
    def delete_compute_worker(
        worker_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> None:
        worker = managed_compute_worker(database, user, worker_id)
        database.delete(worker)
        database.commit()

    @app.post(
        "/api/v1/compute-workers/{worker_id}/test",
        response_model=ComputeWorkerTestResponse,
        tags=["compute-workers"],
    )
    def test_compute_worker(
        worker_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> ComputeWorkerTestResponse:
        worker = managed_compute_worker(database, user, worker_id)
        result = probe_ollama(worker.base_url, worker.analysis_model, timeout=10)
        worker.last_status = "ready" if result["ready"] else "unavailable"
        worker.last_error = result.get("detail")
        worker.available_models = result["models"]
        worker.last_checked_at = datetime.now(UTC)
        worker.updated_at = datetime.now(UTC)
        database.commit()
        database.refresh(worker)
        return ComputeWorkerTestResponse(
            worker=ComputeWorkerResponse.model_validate(worker),
            ready=result["ready"], models=result["models"], detail=result.get("detail"),
        )

    @app.get("/api/v1/campaigns", response_model=list[CampaignResponse], tags=["campaigns"])
    def list_campaigns(
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[CampaignResponse]:
        statement = (
            select(Campaign, CampaignMembership.role)
            .join(CampaignMembership, CampaignMembership.campaign_id == Campaign.id)
            .where(CampaignMembership.user_id == user.id)
            .order_by(Campaign.name)
        )
        return [_campaign_response(campaign, role) for campaign, role in database.execute(statement)]

    @app.post(
        "/api/v1/campaigns",
        response_model=CampaignResponse,
        status_code=201,
        tags=["campaigns"],
    )
    def create_campaign(
        request: CampaignCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> CampaignResponse:
        campaign = Campaign(
            name=request.name.strip(),
            slug=_slugify(request.slug or request.name),
            description=request.description.strip(),
            created_by_id=user.id,
        )
        database.add(campaign)
        database.flush()
        database.add(
            CampaignMembership(
                campaign_id=campaign.id,
                user_id=user.id,
                role=CampaignRole.OWNER.value,
            )
        )
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(status_code=409, detail="Campaign slug already exists") from exc
        database.refresh(campaign)
        return _campaign_response(campaign, CampaignRole.OWNER.value)

    @app.put(
        "/api/v1/campaigns/{campaign_id}",
        response_model=CampaignResponse,
        tags=["campaigns"],
    )
    def update_campaign(
        campaign_id: uuid.UUID,
        request: CampaignUpdate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> CampaignResponse:
        membership = require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        campaign = database.get(Campaign, campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        campaign.name = request.name.strip()
        campaign.description = request.description.strip()
        campaign.game_system = request.game_system.strip()
        campaign.play_mode = request.play_mode
        campaign.vtt = request.vtt.strip()
        campaign.character_source = request.character_source.strip()
        campaign.notes = request.notes.strip()
        database.commit()
        database.refresh(campaign)
        return _campaign_response(campaign, membership.role)

    @app.get(
        "/api/v1/campaigns/{campaign_id}/guide",
        response_model=list[CampaignGuideResponse],
        tags=["campaign-guide"],
    )
    def list_campaign_guide(
        campaign_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[CampaignGuideEntry]:
        membership = require_campaign_role(database, user, campaign_id)
        statement = select(CampaignGuideEntry).where(
            CampaignGuideEntry.campaign_id == campaign_id,
            CampaignGuideEntry.is_active.is_(True),
        )
        if membership.role == CampaignRole.PLAYER.value:
            statement = statement.where(CampaignGuideEntry.visibility == "player")
        return list(
            database.scalars(
                statement.order_by(CampaignGuideEntry.kind, CampaignGuideEntry.canonical_name)
            )
        )

    @app.post(
        "/api/v1/campaigns/{campaign_id}/guide",
        response_model=CampaignGuideResponse,
        status_code=201,
        tags=["campaign-guide"],
    )
    def create_campaign_guide_entry(
        campaign_id: uuid.UUID,
        request: CampaignGuideCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> CampaignGuideEntry:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        entry = CampaignGuideEntry(
            campaign_id=campaign_id,
            kind=request.kind,
            canonical_name=request.canonical_name.strip(),
            aliases=request.aliases,
            notes=request.notes.strip(),
            visibility=request.visibility,
            created_by_id=user.id,
        )
        database.add(entry)
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(
                status_code=409,
                detail="A Campaign Guide entry with that kind and name already exists",
            ) from exc
        database.refresh(entry)
        return entry

    @app.put(
        "/api/v1/campaigns/{campaign_id}/guide/{entry_id}",
        response_model=CampaignGuideResponse,
        tags=["campaign-guide"],
    )
    def update_campaign_guide_entry(
        campaign_id: uuid.UUID,
        entry_id: uuid.UUID,
        request: CampaignGuideUpdate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> CampaignGuideEntry:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        entry = database.scalar(select(CampaignGuideEntry).where(
            CampaignGuideEntry.id == entry_id,
            CampaignGuideEntry.campaign_id == campaign_id,
        ))
        if entry is None:
            raise HTTPException(status_code=404, detail="Campaign Guide entry not found")
        entry.kind = request.kind
        entry.canonical_name = request.canonical_name.strip()
        entry.aliases = request.aliases
        entry.notes = request.notes.strip()
        entry.visibility = request.visibility
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(status_code=409, detail="Campaign Guide name already exists") from exc
        database.refresh(entry)
        return entry

    @app.delete("/api/v1/campaigns/{campaign_id}/guide/{entry_id}", status_code=204)
    def delete_campaign_guide_entry(
        campaign_id: uuid.UUID,
        entry_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> None:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        entry = database.scalar(select(CampaignGuideEntry).where(
            CampaignGuideEntry.id == entry_id,
            CampaignGuideEntry.campaign_id == campaign_id,
        ))
        if entry is None:
            raise HTTPException(status_code=404, detail="Campaign Guide entry not found")
        database.delete(entry)
        database.commit()

    @app.get(
        "/api/v1/campaigns/{campaign_id}/guide/{entry_id}/facts",
        response_model=list[CampaignGuideFactResponse],
        tags=["campaign-guide"],
    )
    def list_campaign_guide_facts(
        campaign_id: uuid.UUID, entry_id: uuid.UUID,
        user: User = Depends(current_user), database: Session = Depends(database_session),
    ) -> list[CampaignGuideFact]:
        membership = require_campaign_role(database, user, campaign_id)
        entry = database.scalar(select(CampaignGuideEntry).where(
            CampaignGuideEntry.id == entry_id, CampaignGuideEntry.campaign_id == campaign_id,
            CampaignGuideEntry.is_active.is_(True),
        ))
        if entry is None:
            raise HTTPException(status_code=404, detail="Campaign Guide entry not found")
        statement = select(CampaignGuideFact).where(CampaignGuideFact.guide_entry_id == entry_id)
        if membership.role == CampaignRole.PLAYER.value:
            statement = statement.where(CampaignGuideFact.visibility == "player", CampaignGuideFact.status == "canonical")
        return list(database.scalars(statement.order_by(CampaignGuideFact.created_at)))

    @app.post(
        "/api/v1/campaigns/{campaign_id}/guide/{entry_id}/facts",
        response_model=CampaignGuideFactResponse,
        status_code=201,
        tags=["campaign-guide"],
    )
    def create_campaign_guide_fact(
        campaign_id: uuid.UUID, entry_id: uuid.UUID, request: CampaignGuideFactCreate,
        user: User = Depends(current_user), database: Session = Depends(database_session),
    ) -> CampaignGuideFact:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        entry = database.scalar(select(CampaignGuideEntry).where(
            CampaignGuideEntry.id == entry_id, CampaignGuideEntry.campaign_id == campaign_id,
        ))
        if entry is None:
            raise HTTPException(status_code=404, detail="Campaign Guide entry not found")
        if request.session_id is not None and database.scalar(select(GameSession.id).where(
            GameSession.id == request.session_id, GameSession.campaign_id == campaign_id,
        )) is None:
            raise HTTPException(status_code=422, detail="Fact session must belong to this campaign")
        fact = CampaignGuideFact(
            guide_entry_id=entry_id, session_id=request.session_id, category=request.category.strip().casefold(),
            value=request.value.strip(), status=request.status.strip().casefold(), confidence=request.confidence,
            visibility=request.visibility, created_by_id=user.id,
        )
        database.add(fact)
        database.commit()
        database.refresh(fact)
        return fact

    @app.get(
        "/api/v1/campaigns/{campaign_id}/speakers",
        response_model=list[SpeakerProfileResponse],
        tags=["speaker-review"],
    )
    def list_speaker_profiles(
        campaign_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[SpeakerProfile]:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        return list(
            database.scalars(
                select(SpeakerProfile)
                .where(SpeakerProfile.campaign_id == campaign_id)
                .order_by(SpeakerProfile.display_name)
            )
        )

    @app.get(
        "/api/v1/campaigns/{campaign_id}/voiceprints",
        response_model=list[SpeakerVoiceprintResponse],
        tags=["speaker-review"],
    )
    def list_speaker_voiceprints(
        campaign_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[SpeakerVoiceprintResponse]:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        rows = database.execute(
            select(SpeakerVoiceprint, SpeakerProfile)
            .join(SpeakerProfile, SpeakerProfile.id == SpeakerVoiceprint.speaker_profile_id)
            .where(SpeakerProfile.campaign_id == campaign_id)
            .order_by(SpeakerProfile.display_name)
        ).all()
        return [
            SpeakerVoiceprintResponse(
                id=voiceprint.id,
                speaker_profile_id=voiceprint.speaker_profile_id,
                speaker_name=profile.display_name,
                embedding_model=voiceprint.embedding_model,
                dimensions=len(voiceprint.embedding or []),
                sample_count=voiceprint.sample_count,
                sample_seconds=voiceprint.sample_seconds,
                source_session_ids=[str(value) for value in voiceprint.source_session_ids],
                updated_at=voiceprint.updated_at,
            )
            for voiceprint, profile in rows
        ]

    @app.post(
        "/api/v1/campaigns/{campaign_id}/voiceprints",
        response_model=JobResponse,
        status_code=202,
        tags=["speaker-review"],
    )
    def queue_speaker_enrollment(
        campaign_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> Job:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        approved = database.scalar(
            select(func.count(SpeakerReview.id))
            .join(GameSession, GameSession.id == SpeakerReview.session_id)
            .where(
                GameSession.campaign_id == campaign_id,
                SpeakerReview.disposition == "confirmed",
                SpeakerReview.approved_reference.is_(True),
            )
        ) or 0
        if approved == 0:
            raise HTTPException(
                status_code=409,
                detail="Approve at least one clip as a voice reference before enrolling",
            )
        active = database.scalar(select(Job.id).where(
            Job.kind == "speaker_enrollment",
            Job.status.in_({"queued", "running"}),
            Job.payload["campaign_id"].as_string() == str(campaign_id),
        ))
        if active is not None:
            raise HTTPException(status_code=409, detail="Speaker enrollment is already queued")
        job = Job(
            kind="speaker_enrollment",
            payload={"campaign_id": str(campaign_id), "requested_by_id": str(user.id)},
        )
        database.add(job)
        database.commit()
        database.refresh(job)
        return job

    @app.post(
        "/api/v1/campaigns/{campaign_id}/speakers",
        response_model=SpeakerProfileResponse,
        status_code=201,
        tags=["speaker-review"],
    )
    def create_speaker_profile(
        campaign_id: uuid.UUID,
        request: SpeakerProfileCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> SpeakerProfile:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        profile = SpeakerProfile(
            campaign_id=campaign_id,
            display_name=request.display_name.strip(),
            notes=request.notes.strip(),
            created_by_id=user.id,
        )
        database.add(profile)
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(status_code=409, detail="Speaker name already exists") from exc
        database.refresh(profile)
        return profile

    @app.put(
        "/api/v1/campaigns/{campaign_id}/speakers/{profile_id}",
        response_model=SpeakerProfileResponse,
        tags=["speaker-review"],
    )
    def update_speaker_profile(
        campaign_id: uuid.UUID,
        profile_id: uuid.UUID,
        request: SpeakerProfileUpdate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> SpeakerProfile:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        profile = database.scalar(select(SpeakerProfile).where(
            SpeakerProfile.id == profile_id,
            SpeakerProfile.campaign_id == campaign_id,
        ))
        if profile is None:
            raise HTTPException(status_code=404, detail="Speaker not found")
        profile.display_name = request.display_name.strip()
        profile.notes = request.notes.strip()
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(status_code=409, detail="Speaker name already exists") from exc
        database.refresh(profile)
        return profile

    @app.delete("/api/v1/campaigns/{campaign_id}/speakers/{profile_id}", status_code=204)
    def delete_speaker_profile(
        campaign_id: uuid.UUID,
        profile_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> None:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        profile = database.scalar(select(SpeakerProfile).where(
            SpeakerProfile.id == profile_id,
            SpeakerProfile.campaign_id == campaign_id,
        ))
        if profile is None:
            raise HTTPException(status_code=404, detail="Speaker not found")
        database.delete(profile)
        database.commit()

    def speaker_assignment_response(
        assignment: SpeakerCharacterAssignment,
        speaker: SpeakerProfile,
        character: CampaignGuideEntry,
        game_session: GameSession | None,
    ) -> SpeakerCharacterAssignmentResponse:
        return SpeakerCharacterAssignmentResponse(
            id=assignment.id,
            speaker_profile_id=assignment.speaker_profile_id,
            speaker_name=speaker.display_name,
            guide_entry_id=assignment.guide_entry_id,
            character_name=character.canonical_name,
            session_id=assignment.session_id,
            session_title=game_session.title if game_session else None,
            is_primary=assignment.is_primary,
            notes=assignment.notes,
            created_at=assignment.created_at,
        )

    def validate_speaker_character_scope(
        database: Session,
        campaign_id: uuid.UUID,
        speaker_profile_id: uuid.UUID,
        guide_entry_id: uuid.UUID,
        session_id: uuid.UUID | None,
    ) -> tuple[SpeakerProfile, CampaignGuideEntry, GameSession | None]:
        speaker = database.scalar(select(SpeakerProfile).where(
            SpeakerProfile.id == speaker_profile_id,
            SpeakerProfile.campaign_id == campaign_id,
        ))
        if speaker is None:
            raise HTTPException(status_code=404, detail="Speaker not found")
        character = database.scalar(select(CampaignGuideEntry).where(
            CampaignGuideEntry.id == guide_entry_id,
            CampaignGuideEntry.campaign_id == campaign_id,
            CampaignGuideEntry.is_active.is_(True),
        ))
        if character is None:
            raise HTTPException(status_code=404, detail="Campaign Guide entry not found")
        if character.kind not in {"player_character", "character"}:
            raise HTTPException(
                status_code=422, detail="Speakers can only be assigned to Player Characters"
            )
        game_session = None
        if session_id is not None:
            game_session = database.scalar(select(GameSession).where(
                GameSession.id == session_id,
                GameSession.campaign_id == campaign_id,
            ))
            if game_session is None:
                raise HTTPException(status_code=404, detail="Session not found")
        return speaker, character, game_session

    @app.get(
        "/api/v1/campaigns/{campaign_id}/speaker-character-assignments",
        response_model=list[SpeakerCharacterAssignmentResponse],
        tags=["speaker-review"],
    )
    def list_speaker_character_assignments(
        campaign_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[SpeakerCharacterAssignmentResponse]:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        rows = database.execute(
            select(SpeakerCharacterAssignment, SpeakerProfile, CampaignGuideEntry, GameSession)
            .join(SpeakerProfile, SpeakerProfile.id == SpeakerCharacterAssignment.speaker_profile_id)
            .join(CampaignGuideEntry, CampaignGuideEntry.id == SpeakerCharacterAssignment.guide_entry_id)
            .outerjoin(GameSession, GameSession.id == SpeakerCharacterAssignment.session_id)
            .where(SpeakerProfile.campaign_id == campaign_id)
            .order_by(SpeakerProfile.display_name, SpeakerCharacterAssignment.is_primary.desc())
        ).all()
        return [speaker_assignment_response(*row) for row in rows]

    @app.post(
        "/api/v1/campaigns/{campaign_id}/speaker-character-assignments",
        response_model=SpeakerCharacterAssignmentResponse,
        status_code=201,
        tags=["speaker-review"],
    )
    def create_speaker_character_assignment(
        campaign_id: uuid.UUID,
        request: SpeakerCharacterAssignmentCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> SpeakerCharacterAssignmentResponse:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        speaker, character, game_session = validate_speaker_character_scope(
            database, campaign_id, request.speaker_profile_id,
            request.guide_entry_id, request.session_id,
        )
        if request.is_primary:
            existing = database.scalars(select(SpeakerCharacterAssignment).where(
                SpeakerCharacterAssignment.speaker_profile_id == speaker.id,
                SpeakerCharacterAssignment.session_id == request.session_id,
            )).all()
            for assignment in existing:
                assignment.is_primary = False
        assignment = SpeakerCharacterAssignment(
            speaker_profile_id=speaker.id,
            guide_entry_id=character.id,
            session_id=request.session_id,
            is_primary=request.is_primary,
            notes=request.notes.strip(),
            created_by_id=user.id,
        )
        database.add(assignment)
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(status_code=409, detail="Speaker assignment already exists") from exc
        database.refresh(assignment)
        return speaker_assignment_response(assignment, speaker, character, game_session)

    @app.put(
        "/api/v1/campaigns/{campaign_id}/speaker-character-assignments/{assignment_id}",
        response_model=SpeakerCharacterAssignmentResponse,
        tags=["speaker-review"],
    )
    def update_speaker_character_assignment(
        campaign_id: uuid.UUID,
        assignment_id: uuid.UUID,
        request: SpeakerCharacterAssignmentUpdate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> SpeakerCharacterAssignmentResponse:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        assignment = database.scalar(
            select(SpeakerCharacterAssignment)
            .join(SpeakerProfile, SpeakerProfile.id == SpeakerCharacterAssignment.speaker_profile_id)
            .where(
                SpeakerCharacterAssignment.id == assignment_id,
                SpeakerProfile.campaign_id == campaign_id,
            )
        )
        if assignment is None:
            raise HTTPException(status_code=404, detail="Speaker assignment not found")
        speaker, character, game_session = validate_speaker_character_scope(
            database, campaign_id, assignment.speaker_profile_id,
            request.guide_entry_id, request.session_id,
        )
        if request.is_primary:
            existing = database.scalars(select(SpeakerCharacterAssignment).where(
                SpeakerCharacterAssignment.speaker_profile_id == speaker.id,
                SpeakerCharacterAssignment.session_id == request.session_id,
                SpeakerCharacterAssignment.id != assignment.id,
            )).all()
            for other in existing:
                other.is_primary = False
        assignment.guide_entry_id = character.id
        assignment.session_id = request.session_id
        assignment.is_primary = request.is_primary
        assignment.notes = request.notes.strip()
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(status_code=409, detail="Speaker assignment already exists") from exc
        database.refresh(assignment)
        return speaker_assignment_response(assignment, speaker, character, game_session)

    @app.delete(
        "/api/v1/campaigns/{campaign_id}/speaker-character-assignments/{assignment_id}",
        status_code=204,
    )
    def delete_speaker_character_assignment(
        campaign_id: uuid.UUID,
        assignment_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> None:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        assignment = database.scalar(
            select(SpeakerCharacterAssignment)
            .join(SpeakerProfile, SpeakerProfile.id == SpeakerCharacterAssignment.speaker_profile_id)
            .where(
                SpeakerCharacterAssignment.id == assignment_id,
                SpeakerProfile.campaign_id == campaign_id,
            )
        )
        if assignment is None:
            raise HTTPException(status_code=404, detail="Speaker assignment not found")
        database.delete(assignment)
        database.commit()

    @app.get(
        "/api/v1/campaigns/{campaign_id}/sessions",
        response_model=list[SessionResponse],
        tags=["sessions"],
    )
    def list_sessions(
        campaign_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[GameSession]:
        require_campaign_role(database, user, campaign_id)
        return list(
            database.scalars(
                select(GameSession)
                .where(GameSession.campaign_id == campaign_id)
                .order_by(
                    GameSession.session_date.is_(None),
                    GameSession.session_date.asc(),
                    GameSession.title.asc(),
                )
            )
        )

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions",
        response_model=SessionResponse,
        status_code=201,
        tags=["sessions"],
    )
    def create_session(
        campaign_id: uuid.UUID,
        request: SessionCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> GameSession:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        game_session = GameSession(
            campaign_id=campaign_id,
            title=request.title.strip(),
            session_date=request.session_date,
            description=request.description.strip(),
            created_by_id=user.id,
        )
        database.add(game_session)
        database.commit()
        database.refresh(game_session)
        return game_session

    @app.put(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}",
        response_model=SessionResponse,
        tags=["sessions"],
    )
    def update_session(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        request: SessionUpdate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> GameSession:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        game_session = database.scalar(select(GameSession).where(
            GameSession.id == session_id,
            GameSession.campaign_id == campaign_id,
        ))
        if game_session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        game_session.title = request.title.strip()
        game_session.session_date = request.session_date
        game_session.description = request.description.strip()
        database.commit()
        database.refresh(game_session)
        return game_session

    def analysis_proposal(
        database: Session, campaign_id: uuid.UUID, session_id: uuid.UUID, proposal_id: uuid.UUID
    ) -> AnalysisProposal:
        proposal = database.scalar(
            select(AnalysisProposal)
            .join(GameSession, GameSession.id == AnalysisProposal.session_id)
            .where(
                AnalysisProposal.id == proposal_id,
                AnalysisProposal.session_id == session_id,
                GameSession.campaign_id == campaign_id,
            )
        )
        if proposal is None:
            raise HTTPException(status_code=404, detail="Analysis proposal not found")
        return proposal

    def chronicle_entry(
        database: Session, campaign_id: uuid.UUID, session_id: uuid.UUID, entry_id: uuid.UUID
    ) -> ChronicleEntry:
        entry = database.scalar(
            select(ChronicleEntry)
            .join(GameSession, GameSession.id == ChronicleEntry.session_id)
            .where(
                ChronicleEntry.id == entry_id,
                ChronicleEntry.session_id == session_id,
                GameSession.campaign_id == campaign_id,
            )
        )
        if entry is None:
            raise HTTPException(status_code=404, detail="Chronicle entry not found")
        return entry

    @app.get(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/chronicle",
        response_model=list[ChronicleEntryResponse],
        tags=["chronicle"],
    )
    def list_chronicle_entries(
        campaign_id: uuid.UUID, session_id: uuid.UUID,
        user: User = Depends(current_user), database: Session = Depends(database_session),
    ) -> list[ChronicleEntry]:
        membership = require_campaign_role(database, user, campaign_id, {"owner", "gm", "player"})
        if database.scalar(select(GameSession.id).where(
            GameSession.id == session_id, GameSession.campaign_id == campaign_id
        )) is None:
            raise HTTPException(status_code=404, detail="Session not found")
        statement = select(ChronicleEntry).where(ChronicleEntry.session_id == session_id)
        if membership.role not in {"owner", "gm"}:
            statement = statement.where(ChronicleEntry.visibility == "player")
        return list(database.scalars(statement.order_by(ChronicleEntry.section, ChronicleEntry.position, ChronicleEntry.created_at)))

    @app.put(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/chronicle/{entry_id}",
        response_model=ChronicleEntryResponse,
        tags=["chronicle"],
    )
    def update_chronicle_entry(
        campaign_id: uuid.UUID, session_id: uuid.UUID, entry_id: uuid.UUID,
        request: ChronicleEntryUpdate, user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> ChronicleEntry:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        entry = chronicle_entry(database, campaign_id, session_id, entry_id)
        entry.section = request.section.strip().casefold()
        entry.entry_type = request.entry_type.strip().casefold()
        entry.title = request.title.strip()
        entry.body = request.body.strip()
        entry.position = request.position
        entry.visibility = request.visibility
        database.commit()
        database.refresh(entry)
        return entry

    @app.delete(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/chronicle/{entry_id}",
        status_code=204,
        tags=["chronicle"],
    )
    def delete_chronicle_entry(
        campaign_id: uuid.UUID, session_id: uuid.UUID, entry_id: uuid.UUID,
        user: User = Depends(current_user), database: Session = Depends(database_session),
    ) -> Response:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        entry = chronicle_entry(database, campaign_id, session_id, entry_id)
        database.delete(entry)
        database.commit()
        return Response(status_code=204)

    @app.get(
        "/api/v1/campaigns/{campaign_id}/analysis-proposals",
        response_model=list[AnalysisProposalResponse],
        tags=["analysis-review"],
    )
    def list_campaign_analysis_proposals(
        campaign_id: uuid.UUID,
        proposal_status: str | None = Query(default=None, alias="status"),
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[AnalysisProposal]:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        statement = (
            select(AnalysisProposal)
            .join(GameSession, GameSession.id == AnalysisProposal.session_id)
            .where(GameSession.campaign_id == campaign_id)
            .order_by(AnalysisProposal.created_at, AnalysisProposal.kind, AnalysisProposal.title)
        )
        if proposal_status is not None:
            if proposal_status not in {"proposed", "approved", "rejected"}:
                raise HTTPException(status_code=422, detail="Unsupported proposal status")
            statement = statement.where(AnalysisProposal.status == proposal_status)
        return list(database.scalars(statement))

    @app.get(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals",
        response_model=list[AnalysisProposalResponse],
        tags=["analysis-review"],
    )
    def list_analysis_proposals(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        proposal_status: str | None = Query(default=None, alias="status"),
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[AnalysisProposal]:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        statement = (
            select(AnalysisProposal)
            .join(GameSession, GameSession.id == AnalysisProposal.session_id)
            .where(AnalysisProposal.session_id == session_id, GameSession.campaign_id == campaign_id)
            .order_by(AnalysisProposal.created_at, AnalysisProposal.kind, AnalysisProposal.title)
        )
        if proposal_status is not None:
            if proposal_status not in {"proposed", "approved", "rejected"}:
                raise HTTPException(status_code=422, detail="Unsupported proposal status")
            statement = statement.where(AnalysisProposal.status == proposal_status)
        return list(database.scalars(statement))

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals",
        response_model=AnalysisProposalResponse,
        status_code=201,
        tags=["analysis-review"],
    )
    def create_analysis_proposal(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        request: AnalysisProposalCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> AnalysisProposal:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        if database.scalar(select(GameSession.id).where(
            GameSession.id == session_id, GameSession.campaign_id == campaign_id
        )) is None:
            raise HTTPException(status_code=404, detail="Session not found")
        artifact_ids = {item.artifact_id for item in request.evidence if item.artifact_id}
        if artifact_ids:
            valid_ids = set(database.scalars(select(Artifact.id).where(
                Artifact.session_id == session_id, Artifact.id.in_(artifact_ids)
            )))
            if valid_ids != artifact_ids:
                raise HTTPException(status_code=422, detail="Evidence must reference this session's artifacts")
        proposal = AnalysisProposal(
            session_id=session_id, kind=request.kind, lane=request.lane, title=request.title.strip(),
            body=request.body.strip(), aliases=request.aliases,
            evidence=[item.model_dump(mode="json") for item in request.evidence],
            confidence=request.confidence, visibility=request.visibility,
            provider=request.provider.strip() or "manual", model=request.model.strip(),
            run_metadata=request.run_metadata, created_by_id=user.id,
        )
        database.add(proposal)
        database.commit()
        database.refresh(proposal)
        return proposal

    @app.put(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{proposal_id}",
        response_model=AnalysisProposalResponse,
        tags=["analysis-review"],
    )
    def update_analysis_proposal(
        campaign_id: uuid.UUID, session_id: uuid.UUID, proposal_id: uuid.UUID,
        request: AnalysisProposalUpdate, user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> AnalysisProposal:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        proposal = analysis_proposal(database, campaign_id, session_id, proposal_id)
        if proposal.status != "proposed":
            raise HTTPException(status_code=409, detail="Reviewed proposals cannot be edited")
        proposal.title = request.title.strip()
        proposal.body = request.body.strip()
        proposal.aliases = list(dict.fromkeys(a.strip() for a in request.aliases if a.strip()))
        proposal.visibility = request.visibility
        proposal.lane = request.lane
        database.commit()
        database.refresh(proposal)
        return proposal

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{proposal_id}/approve",
        response_model=AnalysisProposalResponse,
        tags=["analysis-review"],
    )
    def approve_analysis_proposal(
        campaign_id: uuid.UUID, session_id: uuid.UUID, proposal_id: uuid.UUID,
        user: User = Depends(current_user), database: Session = Depends(database_session),
    ) -> AnalysisProposal:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        proposal = analysis_proposal(database, campaign_id, session_id, proposal_id)
        if proposal.status != "proposed":
            raise HTTPException(status_code=409, detail="Proposal was already reviewed")
        # The guide is a dictionary of reusable entities. Quests and rules are
        # episodic and belong to threads and the table log; spells and the
        # unclassified "character" bucket were not worth entries of their own.
        guide_kinds = {
            "player_character", "npc", "monster", "location", "item",
            "creature", "faction", "deity",
        }
        if proposal.kind in guide_kinds:
            entry = database.scalar(select(CampaignGuideEntry).where(
                CampaignGuideEntry.campaign_id == campaign_id,
                CampaignGuideEntry.kind == proposal.kind,
                func.lower(CampaignGuideEntry.canonical_name) == proposal.title.casefold(),
            ))
            if entry is None:
                entry = CampaignGuideEntry(
                    campaign_id=campaign_id, kind=proposal.kind,
                    canonical_name=proposal.title, aliases=proposal.aliases,
                    notes=proposal.body, visibility=proposal.visibility, created_by_id=user.id,
                )
                database.add(entry)
                database.flush()
            proposal.promoted_guide_entry_id = entry.id
            if proposal.body.strip() and database.scalar(select(CampaignGuideFact.id).where(
                CampaignGuideFact.source_proposal_id == proposal.id
            )) is None:
                database.add(CampaignGuideFact(
                    guide_entry_id=entry.id,
                    session_id=session_id,
                    source_proposal_id=proposal.id,
                    category="session_detail",
                    value=proposal.body.strip(),
                    status="canonical",
                    confidence=proposal.confidence,
                    visibility=proposal.visibility,
                    created_by_id=user.id,
                ))
        chronicle_sections = {
            "session_summary": ("recap", "recap"),
            "scene": ("outline", "scene"),
            "memorable_moment": ("moments", "memorable_moment"),
            "player_character": ("entities", "player_character"),
            "npc": ("entities", "npc"),
            "monster": ("entities", "monster"),
            "creature": ("entities", "creature"),
            "location": ("entities", "location"),
            "item": ("entities", "item"),
            "faction": ("entities", "faction"),
            "deity": ("entities", "deity"),
            "important_decision": ("threads", "decision"),
            "quest": ("threads", "quest"),
            "unresolved_question": ("threads", "unresolved_question"),
            "follow_up": ("meta", "follow_up"),
            "rule": ("meta", "rule"),
            "table_note": ("meta", "table_note"),
        }
        section_type = chronicle_sections.get(proposal.kind)
        if section_type:
            section, entry_type = section_type
            existing = database.scalar(select(ChronicleEntry).where(
                ChronicleEntry.source_proposal_id == proposal.id
            ))
            if existing is None:
                position = database.scalar(select(func.count(ChronicleEntry.id)).where(
                    ChronicleEntry.session_id == session_id, ChronicleEntry.section == section
                )) or 0
                database.add(ChronicleEntry(
                    session_id=session_id, source_proposal_id=proposal.id,
                    section=section, entry_type=entry_type, title=proposal.title,
                    body=proposal.body, position=position, visibility=proposal.visibility,
                    entry_metadata={"aliases": proposal.aliases, "evidence": proposal.evidence},
                    created_by_id=user.id,
                ))
        proposal.status = "approved"
        proposal.reviewed_by_id = user.id
        proposal.reviewed_at = datetime.now(UTC)
        database.commit()
        database.refresh(proposal)
        return proposal

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis-proposals/{proposal_id}/reject",
        response_model=AnalysisProposalResponse,
        tags=["analysis-review"],
    )
    def reject_analysis_proposal(
        campaign_id: uuid.UUID, session_id: uuid.UUID, proposal_id: uuid.UUID,
        user: User = Depends(current_user), database: Session = Depends(database_session),
    ) -> AnalysisProposal:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        proposal = analysis_proposal(database, campaign_id, session_id, proposal_id)
        if proposal.status != "proposed":
            raise HTTPException(status_code=409, detail="Proposal was already reviewed")
        proposal.status = "rejected"
        proposal.reviewed_by_id = user.id
        proposal.reviewed_at = datetime.now(UTC)
        database.commit()
        database.refresh(proposal)
        return proposal

    def publication_record(
        database: Session, campaign_id: uuid.UUID, session_id: uuid.UUID, publication_id: uuid.UUID
    ) -> SessionPublication:
        publication = database.scalar(
            select(SessionPublication)
            .join(GameSession, GameSession.id == SessionPublication.session_id)
            .where(
                SessionPublication.id == publication_id,
                SessionPublication.session_id == session_id,
                GameSession.campaign_id == campaign_id,
            )
        )
        if publication is None:
            raise HTTPException(status_code=404, detail="Publication draft not found")
        return publication

    @app.get(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/publications",
        response_model=list[PublicationResponse], tags=["publishing"],
    )
    def list_publications(
        campaign_id: uuid.UUID, session_id: uuid.UUID,
        user: User = Depends(current_user), database: Session = Depends(database_session),
    ) -> list[SessionPublication]:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        if database.scalar(select(GameSession.id).where(
            GameSession.id == session_id, GameSession.campaign_id == campaign_id
        )) is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return list(database.scalars(select(SessionPublication).where(
            SessionPublication.session_id == session_id
        ).order_by(SessionPublication.revision.desc())))

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/publications",
        response_model=PublicationResponse, status_code=201, tags=["publishing"],
    )
    def create_publication(
        campaign_id: uuid.UUID, session_id: uuid.UUID, request: PublicationCreate,
        user: User = Depends(current_user), database: Session = Depends(database_session),
    ) -> SessionPublication:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        game_session = database.scalar(select(GameSession).where(
            GameSession.id == session_id, GameSession.campaign_id == campaign_id
        ))
        if game_session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        proposals = list(database.scalars(select(AnalysisProposal).where(
            AnalysisProposal.session_id == session_id,
            AnalysisProposal.status == "approved",
            AnalysisProposal.visibility == "player",
        ).order_by(AnalysisProposal.created_at)))
        if not proposals:
            raise HTTPException(status_code=409, detail="Approve at least one player-visible finding first")
        target = request.target_path or default_target_path(game_session)
        try:
            target = validate_target_path(target).as_posix()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        revision = (database.scalar(select(func.max(SessionPublication.revision)).where(
            SessionPublication.session_id == session_id
        )) or 0) + 1
        publication = SessionPublication(
            session_id=session_id, revision=revision, title=game_session.title,
            content=render_player_draft(game_session, proposals), target_path=target,
            source_proposal_ids=[str(item.id) for item in proposals], created_by_id=user.id,
        )
        database.add(publication)
        database.commit()
        database.refresh(publication)
        return publication

    @app.put(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/publications/{publication_id}",
        response_model=PublicationResponse, tags=["publishing"],
    )
    def update_publication(
        campaign_id: uuid.UUID, session_id: uuid.UUID, publication_id: uuid.UUID,
        request: PublicationUpdate, user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> SessionPublication:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        publication = publication_record(database, campaign_id, session_id, publication_id)
        if publication.status != "draft":
            raise HTTPException(status_code=409, detail="Published revisions are immutable; generate a new draft")
        try:
            publication.target_path = validate_target_path(request.target_path).as_posix()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        publication.title = request.title.strip()
        publication.content = request.content
        database.commit()
        database.refresh(publication)
        return publication

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/publications/{publication_id}/publish",
        response_model=PublicationResponse, tags=["publishing"],
    )
    def publish_publication(
        campaign_id: uuid.UUID, session_id: uuid.UUID, publication_id: uuid.UUID,
        request: PublicationPublish, user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> SessionPublication:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        publication = publication_record(database, campaign_id, session_id, publication_id)
        if publication.status != "draft":
            raise HTTPException(status_code=409, detail="Publication revision was already published")
        if resolved.otterwiki_repository_path is None:
            raise HTTPException(status_code=409, detail="OtterWiki publishing is not configured")
        try:
            commit, blob_hash = publish_to_otterwiki(
                resolved.otterwiki_repository_path, publication.target_path, publication.content,
                f"Publish {publication.title} from Campaign Manager",
                publication.last_published_blob_hash, request.confirm_overwrite,
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        publication.status = "published"
        publication.last_published_blob_hash = blob_hash
        publication.published_commit = commit
        publication.published_by_id = user.id
        publication.published_at = datetime.now(UTC)
        game_session = database.get(GameSession, session_id)
        game_session.status = "published"
        database.commit()
        database.refresh(publication)
        return publication

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/audio",
        response_model=ArtifactResponse,
        status_code=201,
        tags=["sessions"],
    )
    def upload_audio(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        audio: UploadFile = File(),
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> ArtifactResponse:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        game_session = database.scalar(
            select(GameSession).where(
                GameSession.id == session_id,
                GameSession.campaign_id == campaign_id,
            )
        )
        if game_session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        existing_audio = database.scalar(
            select(Artifact.id).where(
                Artifact.session_id == session_id,
                Artifact.kind == "source_audio",
            )
        )
        if existing_audio is not None:
            raise HTTPException(
                status_code=409,
                detail="This session already has source audio; create another session for a different recording",
            )
        artifact, job = ingest_audio(database, resolved, game_session, user, audio)
        return ArtifactResponse(
            id=artifact.id,
            kind=artifact.kind,
            original_filename=artifact.original_filename,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            visibility=artifact.visibility,
            created_at=artifact.created_at,
            job=JobResponse.model_validate(job),
        )

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/text",
        response_model=ArtifactResponse,
        status_code=201,
        tags=["sessions"],
    )
    def add_text_source(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        request: TextSourceCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> ArtifactResponse:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        game_session = database.scalar(
            select(GameSession).where(
                GameSession.id == session_id,
                GameSession.campaign_id == campaign_id,
            )
        )
        if game_session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        artifact = ingest_text(
            database,
            resolved,
            game_session,
            user,
            request.kind,
            request.content,
            request.filename,
        )
        return ArtifactResponse.model_validate(artifact)

    @app.get(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/jobs",
        response_model=list[JobResponse],
        tags=["sessions"],
    )
    def list_session_jobs(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[Job]:
        require_campaign_role(database, user, campaign_id)
        game_session_exists = database.scalar(
            select(GameSession.id).where(
                GameSession.id == session_id,
                GameSession.campaign_id == campaign_id,
            )
        )
        if game_session_exists is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return list(
            database.scalars(
                select(Job).where(Job.session_id == session_id).order_by(Job.created_at.desc())
            )
        )

    def manageable_job(
        database: Session, user: User, job_id: uuid.UUID
    ) -> tuple[Job, GameSession | None]:
        row = database.execute(
            select(Job, GameSession)
            .outerjoin(GameSession, GameSession.id == Job.session_id)
            .where(Job.id == job_id)
        ).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job, game_session = row
        if not user.is_instance_admin:
            if game_session is None:
                raise HTTPException(status_code=403, detail="Instance administrator required")
            require_campaign_role(database, user, game_session.campaign_id, {"owner", "gm"})
        return job, game_session

    @app.get("/api/v1/jobs", response_model=list[QueueJobResponse], tags=["processing"])
    def list_queue_jobs(
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[QueueJobResponse]:
        statement = (
            select(Job, GameSession, Campaign)
            .outerjoin(GameSession, GameSession.id == Job.session_id)
            .outerjoin(Campaign, Campaign.id == GameSession.campaign_id)
            .order_by(
                case((Job.status == "running", 0), (Job.status == "queued", 1), else_=2),
                Job.priority.desc(), Job.queue_position, Job.created_at,
            )
            .limit(500)
        )
        if not user.is_instance_admin:
            campaign_ids = list(database.scalars(select(CampaignMembership.campaign_id).where(
                CampaignMembership.user_id == user.id,
            )))
            if not campaign_ids:
                return []
            statement = statement.where(GameSession.campaign_id.in_(campaign_ids))
        rows = database.execute(statement).all()
        return [QueueJobResponse(
            id=job.id, kind=job.kind, status=job.status, priority=job.priority,
            queue_position=job.queue_position,
            cancel_requested=job.cancel_requested, attempts=job.attempts, error=job.error,
            payload=job.payload,
            created_at=job.created_at, updated_at=job.updated_at,
            session_id=game_session.id if game_session else None,
            session_title=game_session.title if game_session else None,
            campaign_id=campaign.id if campaign else None,
            campaign_name=campaign.name if campaign else None,
        ) for job, game_session, campaign in rows]

    @app.put(
        "/api/v1/jobs/{job_id}/priority",
        response_model=JobResponse,
        tags=["processing"],
    )
    def update_job_priority(
        job_id: uuid.UUID,
        request: JobPriorityUpdate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> Job:
        job, _ = manageable_job(database, user, job_id)
        if job.status != "queued":
            raise HTTPException(status_code=409, detail="Only queued jobs can be reprioritized")
        job.priority = request.priority
        job.updated_at = datetime.now(UTC)
        database.commit()
        database.refresh(job)
        return job

    @app.post(
        "/api/v1/jobs/{job_id}/move",
        response_model=JobResponse,
        tags=["processing"],
    )
    def move_queued_job(
        job_id: uuid.UUID,
        request: QueueMoveRequest,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> Job:
        job, _ = manageable_job(database, user, job_id)
        if job.status != "queued":
            raise HTTPException(status_code=409, detail="Only queued jobs can be reordered")
        ordered = list(database.scalars(select(Job).where(
            Job.status == "queued",
        ).order_by(Job.priority.desc(), Job.queue_position, Job.created_at, Job.id)))
        try:
            current_index = next(index for index, item in enumerate(ordered) if item.id == job.id)
        except StopIteration as exc:
            raise HTTPException(status_code=404, detail="Queued job not found") from exc
        target_index = current_index - 1 if request.direction == "up" else current_index + 1
        if 0 <= target_index < len(ordered):
            neighbor = ordered[target_index]
            job.priority = neighbor.priority
            ordered.pop(current_index)
            ordered.insert(target_index, job)
            now = datetime.now(UTC)
            for position, item in enumerate(ordered):
                item.queue_position = position
                item.updated_at = now
            database.commit()
            database.refresh(job)
        return job

    @app.post(
        "/api/v1/jobs/{job_id}/cancel",
        response_model=JobResponse,
        tags=["processing"],
    )
    def cancel_job(
        job_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> Job:
        job, _ = manageable_job(database, user, job_id)
        if job.status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Only active jobs can be cancelled")
        job.cancel_requested = True
        if job.status == "queued":
            job.status = "cancelled"
        job.updated_at = datetime.now(UTC)
        database.commit()
        database.refresh(job)
        return job

    @app.get(
        "/api/v1/processing-controls",
        response_model=list[ProcessingControlResponse],
        tags=["processing"],
    )
    def list_processing_controls(
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[ProcessingControl]:
        return list(database.scalars(
            select(ProcessingControl)
            .where(ProcessingControl.kind.not_like("\\_\\_%", escape="\\"))
            .order_by(ProcessingControl.kind)
        ))

    @app.get("/api/v1/processing-status", tags=["processing"])
    def processing_status(
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> dict[str, object]:
        del user
        counts = database.execute(
            select(Job.kind, Job.status, func.count(Job.id))
            .where(Job.status.in_({"queued", "running"}))
            .group_by(Job.kind, Job.status)
        ).all()
        memory_available_mb = None
        try:
            memory_line = next(
                line for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            )
            memory_available_mb = round(int(memory_line.split()[1]) / 1024)
        except (OSError, StopIteration, ValueError):
            pass
        return {
            "load_average": [round(value, 2) for value in os.getloadavg()],
            "memory_available_mb": memory_available_mb,
            "active": [
                {"kind": kind, "status": status_value, "count": count}
                for kind, status_value, count in counts
            ],
        }

    def set_processing_control(
        database: Session, user: User, kind: str, paused: bool
    ) -> ProcessingControl:
        if not user.is_instance_admin:
            raise HTTPException(status_code=403, detail="Instance administrator required")
        supported = {"transcription", "diarization", "analysis", "image_generation"}
        if kind not in supported:
            raise HTTPException(status_code=404, detail="Processing control not found")
        control = database.get(ProcessingControl, kind)
        if control is None:
            control = ProcessingControl(kind=kind)
            database.add(control)
        control.paused = paused
        control.updated_by_id = user.id
        control.updated_at = datetime.now(UTC)
        database.commit()
        database.refresh(control)
        return control

    @app.put(
        "/api/v1/processing-controls/{kind}",
        response_model=ProcessingControlResponse,
        tags=["processing"],
    )
    def update_processing_control(
        kind: str,
        request: ProcessingControlUpdate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> ProcessingControl:
        return set_processing_control(database, user, kind, request.paused)

    @app.put(
        "/api/v1/processing-controls/actions/game-session-mode",
        response_model=list[ProcessingControlResponse],
        tags=["processing"],
    )
    def game_session_mode(
        request: ProcessingControlUpdate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[ProcessingControl]:
        controls = [
            set_processing_control(database, user, kind, request.paused)
            for kind in ("transcription", "diarization", "analysis", "image_generation")
        ]
        return controls

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/analysis",
        response_model=JobResponse,
        status_code=202,
        tags=["analysis-review"],
    )
    def queue_session_analysis(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        request: AnalysisRunCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> Job:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        sources = list(database.scalars(
            select(Artifact)
            .join(GameSession, GameSession.id == Artifact.session_id)
            .where(
                Artifact.session_id == session_id,
                GameSession.campaign_id == campaign_id,
                Artifact.kind.in_({"corrected_transcript", "raw_transcript", "source_transcript", "source_notes"}),
            )
            .order_by(Artifact.created_at.desc())
        ))
        if request.source_artifact_id is not None:
            source = next((item for item in sources if item.id == request.source_artifact_id), None)
            if source is None:
                raise HTTPException(status_code=404, detail="Analysis source not found")
        else:
            priority = {"corrected_transcript": 0, "raw_transcript": 1, "source_transcript": 2, "source_notes": 3}
            source = min(sources, key=lambda item: priority[item.kind], default=None)
        if source is None:
            raise HTTPException(status_code=409, detail="Add a transcript or notes before analysis")
        diarization_active = database.scalar(select(Job.id).where(
            Job.session_id == session_id,
            Job.kind == "diarization",
            Job.status.in_({"queued", "running"}),
        ))
        if diarization_active is not None:
            raise HTTPException(
                status_code=409,
                detail="Diarization is still queued or running; analysis can start after it completes",
            )
        active = database.scalar(select(Job.id).where(
            Job.session_id == session_id,
            Job.kind == "analysis",
            Job.status.in_({"queued", "running"}),
        ))
        if active is not None:
            raise HTTPException(status_code=409, detail="Session analysis is already queued or running")
        job = Job(
            session_id=session_id,
            artifact_id=source.id,
            kind="analysis",
            payload={"requested_by_id": str(user.id), "source_artifact_id": str(source.id)},
        )
        database.add(job)
        database.commit()
        database.refresh(job)
        return job

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/diarization",
        response_model=JobResponse,
        status_code=202,
        tags=["speaker-review"],
    )
    def queue_diarization(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> Job:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        normalized = database.scalar(
            select(Artifact)
            .join(GameSession, GameSession.id == Artifact.session_id)
            .where(
                Artifact.session_id == session_id,
                Artifact.kind == "normalized_audio",
                GameSession.campaign_id == campaign_id,
            )
            .order_by(Artifact.created_at.desc())
        )
        if normalized is None:
            raise HTTPException(
                status_code=409,
                detail="Transcription must finish before diarization can be queued",
            )
        existing_artifact = database.scalar(
            select(Artifact.id).where(
                Artifact.session_id == session_id,
                Artifact.kind == "diarization",
            )
        )
        if existing_artifact is not None:
            raise HTTPException(status_code=409, detail="This session is already diarized")
        existing_job = database.scalar(
            select(Job.id).where(
                Job.session_id == session_id,
                Job.kind == "diarization",
                Job.status.in_(["queued", "running"]),
            )
        )
        if existing_job is not None:
            raise HTTPException(status_code=409, detail="Diarization is already queued")
        job = Job(
            session_id=session_id,
            artifact_id=normalized.id,
            kind="diarization",
            status="queued",
            payload={"normalized_audio_artifact_id": str(normalized.id)},
        )
        database.add(job)
        database.commit()
        database.refresh(job)
        return job

    def speaker_review_response(review: SpeakerReview) -> SpeakerReviewResponse:
        return SpeakerReviewResponse(
            id=review.id,
            session_id=review.session_id,
            cluster_label=review.cluster_label,
            start_seconds=review.start_seconds,
            end_seconds=review.end_seconds,
            speaker_profile_id=review.speaker_profile_id,
            speaker_name=(
                review.speaker_profile.display_name if review.speaker_profile is not None else None
            ),
            disposition=review.disposition,
            approved_reference=review.approved_reference,
            notes=review.notes,
            created_at=review.created_at,
            updated_at=review.updated_at,
        )

    @app.get(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews",
        response_model=list[SpeakerReviewResponse],
        tags=["speaker-review"],
    )
    def list_speaker_reviews(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[SpeakerReviewResponse]:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        reviews = database.scalars(
            select(SpeakerReview)
            .join(GameSession, GameSession.id == SpeakerReview.session_id)
            .where(
                SpeakerReview.session_id == session_id,
                GameSession.campaign_id == campaign_id,
            )
            .order_by(SpeakerReview.cluster_label, SpeakerReview.start_seconds)
        ).all()
        return [speaker_review_response(review) for review in reviews]

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews",
        response_model=SpeakerReviewResponse,
        status_code=201,
        tags=["speaker-review"],
    )
    def create_speaker_review(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        request: SpeakerReviewCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> SpeakerReviewResponse:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        session_exists = database.scalar(
            select(GameSession.id).where(
                GameSession.id == session_id,
                GameSession.campaign_id == campaign_id,
            )
        )
        if session_exists is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if request.end_seconds <= request.start_seconds or request.end_seconds - request.start_seconds > 30:
            raise HTTPException(status_code=422, detail="Speaker clips must be between 1 and 30 seconds")
        profile = None
        if request.speaker_profile_id is not None:
            profile = database.scalar(
                select(SpeakerProfile).where(
                    SpeakerProfile.id == request.speaker_profile_id,
                    SpeakerProfile.campaign_id == campaign_id,
                )
            )
            if profile is None:
                raise HTTPException(status_code=422, detail="Speaker profile is not in this campaign")
        if request.approved_reference and (
            profile is None or request.disposition != "confirmed"
        ):
            raise HTTPException(
                status_code=422,
                detail="A reference clip must have a confirmed speaker",
            )
        review = SpeakerReview(
            session_id=session_id,
            cluster_label=request.cluster_label.strip(),
            start_seconds=request.start_seconds,
            end_seconds=request.end_seconds,
            speaker_profile_id=request.speaker_profile_id,
            disposition=request.disposition,
            approved_reference=request.approved_reference,
            notes=request.notes.strip(),
            reviewed_by_id=user.id,
        )
        database.add(review)
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(status_code=409, detail="This speaker clip was already reviewed") from exc
        database.refresh(review)
        review.speaker_profile = profile
        return speaker_review_response(review)

    @app.delete(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/speaker-reviews/{cluster_label}",
        status_code=204,
        tags=["speaker-review"],
    )
    def reopen_speaker_cluster(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        cluster_label: str,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> None:
        require_campaign_role(
            database, user, campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        reviews = list(database.scalars(
            select(SpeakerReview)
            .join(GameSession, GameSession.id == SpeakerReview.session_id)
            .where(
                SpeakerReview.session_id == session_id,
                GameSession.campaign_id == campaign_id,
                SpeakerReview.cluster_label == cluster_label,
            )
        ))
        if not reviews:
            raise HTTPException(status_code=404, detail="Reviewed cluster not found")
        for review in reviews:
            database.delete(review)
        database.commit()

    @app.get(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts",
        response_model=list[ArtifactResponse],
        tags=["review"],
    )
    def list_session_artifacts(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[Artifact]:
        membership = require_campaign_role(database, user, campaign_id)
        session_exists = database.scalar(
            select(GameSession.id).where(
                GameSession.id == session_id,
                GameSession.campaign_id == campaign_id,
            )
        )
        if session_exists is None:
            raise HTTPException(status_code=404, detail="Session not found")
        statement = select(Artifact).where(Artifact.session_id == session_id)
        if membership.role == CampaignRole.PLAYER.value:
            statement = statement.where(Artifact.visibility == "player")
        return list(database.scalars(statement.order_by(Artifact.created_at)))

    def review_artifact(
        database: Session,
        user: User,
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> Artifact:
        membership = require_campaign_role(database, user, campaign_id)
        artifact = database.scalar(
            select(Artifact)
            .join(GameSession, GameSession.id == Artifact.session_id)
            .where(
                Artifact.id == artifact_id,
                Artifact.session_id == session_id,
                GameSession.campaign_id == campaign_id,
            )
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        if membership.role == CampaignRole.PLAYER.value and artifact.visibility != "player":
            raise HTTPException(status_code=403, detail="Artifact is GM-only")
        return artifact

    @app.get(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts/{artifact_id}/content",
        tags=["review"],
    )
    def artifact_content(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        artifact_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> Response:
        artifact = review_artifact(database, user, campaign_id, session_id, artifact_id)
        try:
            content = read_artifact(resolved, artifact)
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        if isinstance(content, str):
            return PlainTextResponse(content)
        if artifact.kind in {"raw_transcript", "corrected_transcript"} and isinstance(
            content.get("segments"), list
        ):
            diarization = database.scalar(select(Artifact).where(
                Artifact.session_id == session_id,
                Artifact.kind == "diarization",
            ).order_by(Artifact.created_at.desc()))
            if diarization is not None:
                diarization_content = read_artifact(resolved, diarization)
                reviews = list(database.scalars(select(SpeakerReview).where(
                    SpeakerReview.session_id == session_id,
                )))
                content = dict(content)
                content["segments"] = attribute_transcript_segments(
                    content["segments"], diarization_content.get("turns", []),
                    cluster_resolutions(reviews),
                )
        return JSONResponse(content)

    @app.get(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/comparisons/{source_id}",
        tags=["review"],
    )
    def compare_transcript_source(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        source_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> dict[str, object]:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        source = review_artifact(database, user, campaign_id, session_id, source_id)
        if source.kind != "source_transcript":
            raise HTTPException(status_code=422, detail="Comparison source must be a transcript")
        native = database.scalar(
            select(Artifact)
            .where(
                Artifact.session_id == session_id,
                Artifact.kind.in_(["raw_transcript", "corrected_transcript"]),
            )
            .order_by(Artifact.created_at.desc())
        )
        if native is None:
            raise HTTPException(status_code=409, detail="Native transcript is not available")
        native_content = read_artifact(resolved, native)
        source_content = read_artifact(resolved, source)
        if not isinstance(native_content, dict) or not isinstance(source_content, str):
            raise HTTPException(status_code=422, detail="Transcript format is not comparable")
        result = compare_transcripts(native_content.get("segments", []), source_content)
        result["native_artifact_id"] = str(native.id)
        result["source_artifact_id"] = str(source.id)
        return result

    @app.put(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts/{artifact_id}",
        response_model=ArtifactResponse,
        tags=["sources"],
    )
    def update_text_source(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        artifact_id: uuid.UUID,
        request: TextSourceUpdate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> Artifact:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        artifact = review_artifact(database, user, campaign_id, session_id, artifact_id)
        if artifact.kind not in {"source_transcript", "source_notes"}:
            raise HTTPException(status_code=409, detail="Only uploaded text sources are editable")
        root = resolved.artifact_root.resolve()
        path = (root / artifact.relative_path).resolve()
        if not path.is_relative_to(root):
            raise HTTPException(status_code=422, detail="Artifact path escapes storage")
        encoded = request.content.encode("utf-8")
        temporary = path.with_suffix(f"{path.suffix}.partial")
        try:
            temporary.write_bytes(encoded)
            os.replace(temporary, path)
            artifact.original_filename = Path(request.filename).name
            artifact.size_bytes = len(encoded)
            artifact.sha256 = hashlib.sha256(encoded).hexdigest()
            database.commit()
            database.refresh(artifact)
            return artifact
        finally:
            temporary.unlink(missing_ok=True)

    @app.delete(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/artifacts/{artifact_id}",
        status_code=204,
        tags=["sources"],
    )
    def delete_source(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        artifact_id: uuid.UUID,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> None:
        require_campaign_role(database, user, campaign_id, {"owner", "gm"})
        artifact = review_artifact(database, user, campaign_id, session_id, artifact_id)
        if artifact.kind not in {"source_transcript", "source_notes"}:
            raise HTTPException(status_code=409, detail="Only uploaded text sources are deletable")
        root = resolved.artifact_root.resolve()
        path = (root / artifact.relative_path).resolve()
        if not path.is_relative_to(root):
            raise HTTPException(status_code=422, detail="Artifact path escapes storage")
        database.delete(artifact)
        database.commit()
        path.unlink(missing_ok=True)

    @app.get(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/audio-clip",
        tags=["review"],
    )
    def audio_clip(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        start: float = Query(ge=0),
        end: float = Query(gt=0),
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> Response:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        normalized = database.scalar(
            select(Artifact)
            .join(GameSession, GameSession.id == Artifact.session_id)
            .where(
                Artifact.session_id == session_id,
                Artifact.kind == "normalized_audio",
                GameSession.campaign_id == campaign_id,
            )
            .order_by(Artifact.created_at.desc())
        )
        if normalized is None:
            raise HTTPException(status_code=404, detail="Normalized audio is not available")
        try:
            clip = normalized_audio_clip(resolved, normalized, start, end)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(clip, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.post(
        "/api/v1/campaigns/{campaign_id}/sessions/{session_id}/transcripts/{artifact_id}/revisions",
        response_model=ArtifactResponse,
        status_code=201,
        tags=["review"],
    )
    def revise_transcript(
        campaign_id: uuid.UUID,
        session_id: uuid.UUID,
        artifact_id: uuid.UUID,
        request: TranscriptRevisionCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> Artifact:
        require_campaign_role(
            database,
            user,
            campaign_id,
            {CampaignRole.OWNER.value, CampaignRole.GM.value},
        )
        source = review_artifact(database, user, campaign_id, session_id, artifact_id)
        try:
            return create_transcript_revision(database, resolved, source, user, request.segments)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = Settings.from_environment()
    uvicorn.run(
        "campaign_manager.api:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
