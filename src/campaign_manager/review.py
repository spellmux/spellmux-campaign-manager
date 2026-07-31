"""Private session artifact access and versioned transcript review."""

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
import wave
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from campaign_manager.config import Settings
from campaign_manager.models import Artifact, User
from campaign_manager.schemas import TranscriptSegmentEdit
from campaign_manager.transcription import _contained_path

MAX_CLIP_SECONDS = 30.0


def read_artifact(settings: Settings, artifact: Artifact) -> Any:
    path = _contained_path(settings.artifact_root, artifact.relative_path)
    if artifact.media_type == "application/json":
        return json.loads(path.read_text(encoding="utf-8"))
    if artifact.media_type.startswith("text/"):
        return path.read_text(encoding="utf-8")
    raise ValueError("Artifact content is not directly reviewable")


def normalized_audio_clip(
    settings: Settings,
    artifact: Artifact,
    start: float,
    end: float,
) -> bytes:
    if start < 0 or end <= start or end - start > MAX_CLIP_SECONDS:
        raise ValueError(f"Clip must be between 0 and {MAX_CLIP_SECONDS:g} seconds")
    path = _contained_path(settings.artifact_root, artifact.relative_path)
    output = io.BytesIO()
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        start_frame = min(round(start * rate), source.getnframes())
        end_frame = min(round(end * rate), source.getnframes())
        source.setpos(start_frame)
        frames = source.readframes(max(0, end_frame - start_frame))
        with wave.open(output, "wb") as destination:
            destination.setparams(source.getparams())
            destination.writeframes(frames)
    return output.getvalue()


def create_transcript_revision(
    database: Session,
    settings: Settings,
    source: Artifact,
    user: User,
    edits: list[TranscriptSegmentEdit],
) -> Artifact:
    if source.kind not in {"raw_transcript", "corrected_transcript"}:
        raise ValueError("Revisions require a timestamped transcript artifact")
    document = read_artifact(settings, source)
    segments = document.get("segments") if isinstance(document, dict) else None
    if not isinstance(segments, list):
        raise TypeError("Transcript artifact has no editable segments")
    by_id = {segment.get("id"): segment for segment in segments}
    for edit in edits:
        if edit.id not in by_id:
            raise ValueError(f"Unknown transcript segment: {edit.id}")
        by_id[edit.id]["text"] = edit.text.strip()
        by_id[edit.id]["reviewed"] = True
    document["schema_version"] = max(int(document.get("schema_version", 1)), 1)
    document["revision_of_artifact_id"] = str(source.id)
    document["revision_edit_count"] = len(edits)

    artifact_id = uuid.uuid4()
    relative = (
        Path(str(source.session_id)) / "corrected" / f"{artifact_id}.json"
    )
    # Newer artifacts use campaign/session paths, but revisions only require a
    # stable, contained and unique path; session_id prevents cross-session collisions.
    destination = _contained_path(settings.artifact_root, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.partial")
    encoded = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        with temporary.open("xb") as output:
            output.write(encoded)
        os.replace(temporary, destination)
        artifact = Artifact(
            id=artifact_id,
            session_id=source.session_id,
            kind="corrected_transcript",
            relative_path=relative.as_posix(),
            original_filename=f"corrected-{artifact_id}.json",
            media_type="application/json",
            size_bytes=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
            visibility="gm",
            created_by_id=user.id,
        )
        database.add(artifact)
        database.commit()
        database.refresh(artifact)
        return artifact
    except Exception:
        database.rollback()
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
