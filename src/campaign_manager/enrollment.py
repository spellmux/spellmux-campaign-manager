"""Build reusable speaker voiceprints from GM-approved reference clips.

Diarization cluster labels are session-local, so a confirmed cluster teaches the
system nothing about the next session unless the voice itself is retained. This
enrolls from the clips a GM explicitly approved as references rather than from a
whole-cluster average, which would fold in the crosstalk they deliberately
excluded, and it needs no diarization re-run: the audio and the review decisions
already exist.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from campaign_manager.config import Settings
from campaign_manager.diarization import load_pcm_wav_window
from campaign_manager.models import (
    Artifact,
    GameSession,
    Job,
    SpeakerProfile,
    SpeakerReview,
    SpeakerVoiceprint,
    utc_now,
)
from campaign_manager.transcription import _contained_path

# Embed one time range of one audio file into a fixed-length vector.
Embed = Callable[[Path, float, float], list[float]]


@dataclass(frozen=True, slots=True)
class EnrollmentSummary:
    enrolled: int
    skipped_speakers: tuple[str, ...]
    failures: tuple[str, ...]
    embedding_model: str


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity, or 0.0 when either vector carries no signal."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _mean_vector(vectors: list[list[float]], weights: list[float]) -> list[float]:
    """Duration-weighted mean; longer reference clips are more reliable."""
    width = len(vectors[0])
    total = sum(weights) or float(len(vectors))
    return [
        sum(vector[index] * weight for vector, weight in zip(vectors, weights, strict=True)) / total
        for index in range(width)
    ]


def reference_clips_by_speaker(
    database: Session, campaign_id: uuid.UUID
) -> dict[uuid.UUID, list[tuple[SpeakerReview, GameSession]]]:
    """Group approved reference clips for a campaign by speaker profile."""
    rows = database.execute(
        select(SpeakerReview, GameSession)
        .join(GameSession, GameSession.id == SpeakerReview.session_id)
        .join(SpeakerProfile, SpeakerProfile.id == SpeakerReview.speaker_profile_id)
        .where(
            GameSession.campaign_id == campaign_id,
            SpeakerProfile.campaign_id == campaign_id,
            SpeakerReview.disposition == "confirmed",
            SpeakerReview.approved_reference.is_(True),
            SpeakerReview.speaker_profile_id.is_not(None),
        )
        .order_by(SpeakerReview.session_id, SpeakerReview.start_seconds)
    ).all()
    grouped: dict[uuid.UUID, list[tuple[SpeakerReview, GameSession]]] = {}
    for review, game_session in rows:
        grouped.setdefault(review.speaker_profile_id, []).append((review, game_session))
    return grouped


def _normalized_audio_path(
    database: Session, settings: Settings, session_id: uuid.UUID
) -> Path | None:
    artifact = database.scalar(
        select(Artifact)
        .where(Artifact.session_id == session_id, Artifact.kind == "normalized_audio")
        .order_by(Artifact.created_at.desc())
    )
    if artifact is None:
        return None
    path = _contained_path(settings.artifact_root, artifact.relative_path)
    return path if path.exists() else None


def enroll_campaign_speakers(
    database: Session,
    settings: Settings,
    campaign_id: uuid.UUID,
    embed: Embed,
    embedding_model: str,
) -> EnrollmentSummary:
    """Enrol or refresh a voiceprint per speaker from their approved clips.

    A speaker with no approved reference clip is skipped rather than enrolled from
    weaker audio, and one clip failing does not lose the speaker's other clips.
    """
    grouped = reference_clips_by_speaker(database, campaign_id)
    enrolled = 0
    skipped: list[str] = []
    failures: list[str] = []
    for profile_id, entries in grouped.items():
        profile = database.get(SpeakerProfile, profile_id)
        if profile is None:
            continue
        vectors: list[list[float]] = []
        weights: list[float] = []
        sessions: list[str] = []
        for review, game_session in entries:
            audio = _normalized_audio_path(database, settings, review.session_id)
            if audio is None:
                failures.append(f"{profile.display_name}: no normalized audio for {game_session.title}")
                continue
            duration = float(review.end_seconds - review.start_seconds)
            if duration <= 0:
                continue
            try:
                vector = [float(value) for value in embed(audio, float(review.start_seconds), float(review.end_seconds))]
            except Exception as exc:  # noqa: BLE001 - one clip must not lose the rest
                failures.append(f"{profile.display_name}: {str(exc)[:160]}")
                continue
            if not vector or not any(vector):
                continue
            vectors.append(vector)
            weights.append(duration)
            sessions.append(str(review.session_id))
        if not vectors:
            skipped.append(profile.display_name)
            continue
        if len({len(vector) for vector in vectors}) != 1:
            failures.append(f"{profile.display_name}: inconsistent embedding widths")
            continue
        centroid = _mean_vector(vectors, weights)
        existing = database.scalar(
            select(SpeakerVoiceprint).where(
                SpeakerVoiceprint.speaker_profile_id == profile_id,
                SpeakerVoiceprint.embedding_model == embedding_model,
            )
        )
        if existing is None:
            database.add(SpeakerVoiceprint(
                speaker_profile_id=profile_id, embedding_model=embedding_model,
                embedding=centroid, sample_count=len(vectors),
                sample_seconds=sum(weights),
                source_session_ids=sorted(set(sessions)),
            ))
        else:
            # Recomputed from all current reference clips, so replace rather than
            # blend: a review the GM changed must not persist in the centroid.
            existing.embedding = centroid
            existing.sample_count = len(vectors)
            existing.sample_seconds = sum(weights)
            existing.source_session_ids = sorted(set(sessions))
            existing.updated_at = utc_now()
        enrolled += 1
    database.commit()
    return EnrollmentSummary(
        enrolled=enrolled,
        skipped_speakers=tuple(skipped),
        failures=tuple(failures),
        embedding_model=embedding_model,
    )


def match_cluster_to_speakers(
    embedding: list[float],
    voiceprints: list[SpeakerVoiceprint],
    *,
    threshold: float,
    margin: float,
) -> tuple[SpeakerVoiceprint | None, float, float]:
    """Return the best voiceprint only when it is both close and unambiguous.

    The margin test is what protects an occasional guest: a voice with no
    enrolled print is often somewhat similar to everyone, and would otherwise be
    confidently labelled as whichever regular it resembles most.
    """
    scored = sorted(
        ((cosine_similarity(embedding, print_.embedding), print_) for print_ in voiceprints),
        key=lambda item: item[0],
        reverse=True,
    )
    if not scored:
        return None, 0.0, 0.0
    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    separation = best_score - runner_up
    if best_score < threshold or separation < margin:
        return None, best_score, separation
    return best, best_score, separation


def pyannote_embedder(settings: Settings) -> tuple[Embed, str]:
    """Return a clip embedder plus the model id its vectors belong to."""
    model_name = settings.speaker_embedding_model

    def embed(path: Path, start: float, end: float) -> list[float]:
        from pyannote.audio import Inference

        model = _cached_embedding_model(
            model_name, settings.huggingface_token, str(settings.model_root)
        )
        inference = Inference(model, window="whole")
        if settings.diarization_device and settings.diarization_device != "cpu":
            inference.to(_torch_device(settings.diarization_device))
        # The loaded window is already the clip, so it is embedded whole. Passing
        # a path would require TorchCodec, which the worker image cannot load.
        vector = inference(load_pcm_wav_window(path, start, end))
        return [float(value) for value in vector.reshape(-1)]

    return embed, model_name


def _torch_device(name: str) -> Any:
    import torch

    return torch.device(name)


_EMBEDDING_MODELS: dict[tuple[str, str], Any] = {}


def _cached_embedding_model(model_name: str, token: str | None, cache_root: str) -> Any:
    """Load the embedding model once per process; it is reused across clips."""
    key = (model_name, cache_root)
    if key not in _EMBEDDING_MODELS:
        from pyannote.audio import Model

        _EMBEDDING_MODELS[key] = Model.from_pretrained(
            model_name, use_auth_token=token, cache_dir=cache_root
        )
    return _EMBEDDING_MODELS[key]


def process_enrollment_job(
    database: Session,
    settings: Settings,
    job: Job,
    embed: Embed | None = None,
    embedding_model: str | None = None,
) -> None:
    """Rebuild voiceprints for one campaign from its approved reference clips."""
    campaign_id = job.payload.get("campaign_id")
    if not campaign_id:
        raise ValueError("Speaker enrollment job requires a campaign_id")
    resolved_model = embedding_model
    if embed is None:
        embed, resolved_model = pyannote_embedder(settings)
    summary = enroll_campaign_speakers(
        database, settings, uuid.UUID(str(campaign_id)), embed,
        resolved_model or settings.speaker_embedding_model,
    )
    if not summary.enrolled and summary.failures:
        raise ValueError(
            f"Speaker enrollment produced no voiceprints: {summary.failures[0]}"
        )
    job.payload = {
        **job.payload,
        "enrollment": {
            "enrolled": summary.enrolled,
            "skipped_speakers": list(summary.skipped_speakers),
            "failures": list(summary.failures),
            "embedding_model": summary.embedding_model,
        },
    }
    database.commit()
