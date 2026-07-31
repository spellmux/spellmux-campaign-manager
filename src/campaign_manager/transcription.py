"""Local audio normalization and faster-whisper transcription."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from campaign_manager.config import Settings
from campaign_manager.models import (
    Artifact,
    CampaignGuideEntry,
    GameSession,
    Job,
    SessionStatus,
)

Transcribe = Callable[[Path, str], dict[str, Any]]


def build_initial_prompt(entries: Iterable[CampaignGuideEntry], limit: int = 4_000) -> str:
    """Build a bounded campaign-specific spelling and context hint for Whisper."""
    lines = ["Dungeons & Dragons campaign transcript. Use these canonical terms:"]
    for entry in entries:
        detail = f"{entry.kind}: {entry.canonical_name}"
        if entry.aliases:
            detail += f" (also heard as: {', '.join(entry.aliases)})"
        if entry.kind in {"instruction", "pronunciation"} and entry.notes:
            detail += f" — {entry.notes.strip()}"
        if sum(len(line) + 1 for line in lines) + len(detail) > limit:
            break
        lines.append(detail)
    return "\n".join(lines)


def process_transcription_job(
    database: Session,
    settings: Settings,
    job: Job,
    transcribe: Transcribe | None = None,
) -> None:
    if job.artifact_id is None or job.session_id is None:
        raise ValueError("Transcription job requires an artifact and session")
    source = database.get(Artifact, job.artifact_id)
    game_session = database.get(GameSession, job.session_id)
    if source is None or game_session is None:
        raise ValueError("Transcription source artifact or session no longer exists")

    guide = database.scalars(
        select(CampaignGuideEntry)
        .where(
            CampaignGuideEntry.campaign_id == game_session.campaign_id,
            CampaignGuideEntry.is_active.is_(True),
        )
        .order_by(CampaignGuideEntry.kind, CampaignGuideEntry.canonical_name)
    ).all()
    prompt = build_initial_prompt(guide)
    source_path = _contained_path(settings.artifact_root, source.relative_path)
    output_dir = Path(str(game_session.campaign_id)) / str(game_session.id)
    normalized_relative = output_dir / "normalized" / f"{job.id}.wav"
    transcript_relative = output_dir / "transcript" / f"{job.id}.json"
    normalized_path = _contained_path(settings.artifact_root, normalized_relative)
    transcript_path = _contained_path(settings.artifact_root, transcript_relative)
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.parent.mkdir(parents=True, exist_ok=True)

    game_session.status = SessionStatus.PROCESSING.value
    database.commit()
    created_paths: list[Path] = []
    try:
        _normalize_audio(source_path, normalized_path)
        created_paths.append(normalized_path)
        result = (transcribe or _faster_whisper_transcriber(settings))(normalized_path, prompt)
        result.update(
            {
                "schema_version": 1,
                "source_artifact_id": str(source.id),
                "normalized_audio_artifact_kind": "normalized_audio",
                "campaign_prompt": prompt,
            }
        )
        _write_json_atomic(transcript_path, result)
        created_paths.append(transcript_path)
        normalized_artifact = _artifact_for_file(
            normalized_path, normalized_relative, game_session, source, "normalized_audio", "audio/wav"
        )
        transcript_artifact = _artifact_for_file(
            transcript_path,
            transcript_relative,
            game_session,
            source,
            "raw_transcript",
            "application/json",
        )
        database.add_all((normalized_artifact, transcript_artifact))
        game_session.status = SessionStatus.REVIEW.value
        database.commit()
    except Exception:
        database.rollback()
        game_session.status = SessionStatus.FAILED.value
        database.commit()
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise


def _contained_path(root: Path, relative: str | Path) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("Artifact path escapes the configured artifact root")
    return candidate


def _normalize_audio(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f"{destination.stem}.partial{destination.suffix}")
    temporary.unlink(missing_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", "-f", "wav", str(temporary),
            ],
            check=True,
            timeout=24 * 60 * 60,
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _faster_whisper_transcriber(settings: Settings) -> Transcribe:
    settings.model_root.mkdir(parents=True, exist_ok=True)
    model = _cached_whisper_model(
        settings.whisper_model,
        settings.whisper_device,
        settings.whisper_compute_type,
        settings.whisper_cpu_threads,
        str(settings.model_root),
    )

    def transcribe(audio_path: Path, prompt: str) -> dict[str, Any]:
        segments, info = model.transcribe(
            str(audio_path),
            initial_prompt=prompt,
            vad_filter=True,
            word_timestamps=True,
        )
        serialized = []
        for segment in segments:
            serialized.append(
                {
                    "id": segment.id,
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text.strip(),
                    "words": [
                        {"start": word.start, "end": word.end, "word": word.word, "probability": word.probability}
                        for word in (segment.words or [])
                    ],
                }
            )
        return {
            "provider": "faster-whisper",
            "model": settings.whisper_model,
            "device": settings.whisper_device,
            "compute_type": settings.whisper_compute_type,
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration,
            "duration_after_vad": info.duration_after_vad,
            "segments": serialized,
        }

    return transcribe


@lru_cache(maxsize=2)
def _cached_whisper_model(
    model_name: str,
    device: str,
    compute_type: str,
    cpu_threads: int,
    download_root: str,
) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        cpu_threads=cpu_threads,
        download_root=download_root,
    )


def _write_json_atomic(destination: Path, value: dict[str, Any]) -> None:
    temporary = destination.with_suffix(f"{destination.suffix}.partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, destination)


def _artifact_for_file(
    path: Path,
    relative_path: Path,
    game_session: GameSession,
    source: Artifact,
    kind: str,
    media_type: str,
) -> Artifact:
    return Artifact(
        id=uuid.uuid4(),
        session_id=game_session.id,
        kind=kind,
        relative_path=relative_path.as_posix(),
        original_filename=path.name,
        media_type=media_type,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
        visibility="gm",
        created_by_id=source.created_by_id,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
