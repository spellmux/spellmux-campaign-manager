"""Private artifact ingestion and metadata creation."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from campaign_manager.config import Settings
from campaign_manager.models import Artifact, GameSession, Job, SessionStatus, User

ALLOWED_AUDIO_EXTENSIONS = {".flac", ".m4a", ".mp3", ".mp4", ".ogg", ".opus", ".wav"}
CHUNK_SIZE = 1024 * 1024


def ingest_text(
    database: Session,
    settings: Settings,
    game_session: GameSession,
    user: User,
    kind: str,
    content: str,
    filename: str | None = None,
) -> Artifact:
    artifact_id = uuid.uuid4()
    artifact_kind = "source_transcript" if kind == "transcript" else "source_notes"
    clean_filename = Path(filename or f"{kind}.md").name[:255]
    relative_path = (
        Path(str(game_session.campaign_id))
        / str(game_session.id)
        / "source"
        / f"{artifact_id}.md"
    )
    destination = settings.artifact_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    if len(encoded) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Text source exceeds upload limit")
    temporary = destination.with_suffix(".md.partial")
    try:
        with temporary.open("xb") as output:
            output.write(encoded)
        os.replace(temporary, destination)
        artifact = Artifact(
            id=artifact_id,
            session_id=game_session.id,
            kind=artifact_kind,
            relative_path=relative_path.as_posix(),
            original_filename=clean_filename,
            media_type="text/markdown; charset=utf-8",
            size_bytes=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
            visibility="gm",
            created_by_id=user.id,
        )
        database.add(artifact)
        game_session.status = SessionStatus.REVIEW.value
        database.commit()
        database.refresh(artifact)
        return artifact
    except Exception:
        database.rollback()
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


def ingest_audio(
    database: Session,
    settings: Settings,
    game_session: GameSession,
    user: User,
    upload: UploadFile,
) -> tuple[Artifact, Job]:
    original_filename = Path(upload.filename or "session-audio").name[:255]
    extension = Path(original_filename).suffix.casefold()
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported audio extension: {extension or '(none)'}",
        )

    artifact_id = uuid.uuid4()
    relative_directory = Path(str(game_session.campaign_id)) / str(game_session.id) / "source"
    relative_path = relative_directory / f"{artifact_id}{extension}"
    destination = settings.artifact_root / relative_path
    temporary = destination.with_suffix(f"{destination.suffix}.partial")
    destination.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    total_size = 0
    try:
        with temporary.open("xb") as output:
            while chunk := upload.file.read(CHUNK_SIZE):
                total_size += len(chunk)
                if total_size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Audio file exceeds upload limit")
                digest.update(chunk)
                output.write(chunk)
        os.replace(temporary, destination)

        artifact = Artifact(
            id=artifact_id,
            session_id=game_session.id,
            kind="source_audio",
            relative_path=relative_path.as_posix(),
            original_filename=original_filename,
            media_type=upload.content_type or "application/octet-stream",
            size_bytes=total_size,
            sha256=digest.hexdigest(),
            visibility="gm",
            created_by_id=user.id,
        )
        database.add(artifact)
        database.flush()
        job = Job(
            session_id=game_session.id,
            artifact_id=artifact.id,
            kind="transcription",
            status="queued",
            payload={"artifact_id": str(artifact.id)},
        )
        database.add(job)
        game_session.status = SessionStatus.UPLOADED.value
        database.commit()
        database.refresh(artifact)
        database.refresh(job)
        return artifact, job
    except Exception:
        database.rollback()
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
