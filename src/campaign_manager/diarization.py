"""Local speaker diarization and representative clip selection."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
import wave
from collections import defaultdict
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from campaign_manager.config import Settings
from campaign_manager.models import Artifact, GameSession, Job, SpeakerReview
from campaign_manager.transcription import _contained_path

Diarize = Callable[[Path], Iterable[tuple[float, float, str]]]

MUSIC_DISPOSITIONS = {"music", "background_music", "featured_song"}
UNUSABLE_VOICE_DISPOSITIONS = {"uncertain", "crosstalk", "noise"}


def cluster_resolutions(reviews: Iterable[SpeakerReview]) -> dict[str, dict[str, Any]]:
    """Resolve reviewed session-local clusters to people or non-speech labels."""
    grouped: dict[str, list[SpeakerReview]] = defaultdict(list)
    for review in reviews:
        grouped[review.cluster_label].append(review)
    resolved: dict[str, dict[str, Any]] = {}
    for label, items in grouped.items():
        dispositions = {item.disposition for item in items}
        music = dispositions & MUSIC_DISPOSITIONS
        profiles = {
            item.speaker_profile_id: item.speaker_profile.display_name
            for item in items
            if item.disposition == "confirmed"
            and item.speaker_profile_id is not None
            and item.speaker_profile is not None
        }
        if len(music) == 1 and dispositions <= MUSIC_DISPOSITIONS:
            disposition = next(iter(music))
            resolved[label] = {
                "status": "reviewed", "disposition": disposition,
                "display_name": disposition.replace("_", " ").title(),
            }
        elif (
            len(profiles) == 1
            and "confirmed" in dispositions
            and dispositions <= ({"confirmed"} | UNUSABLE_VOICE_DISPOSITIONS)
        ):
            profile_id, display_name = next(iter(profiles.items()))
            resolved[label] = {
                "status": "reviewed", "disposition": "confirmed",
                "speaker_profile_id": str(profile_id), "display_name": display_name,
                "excluded_clip_count": sum(
                    item.disposition in UNUSABLE_VOICE_DISPOSITIONS for item in items
                ),
            }
        else:
            resolved[label] = {
                "status": "needs_attention", "disposition": "mixed",
                "display_name": "Needs attention",
            }
    return resolved


def attribute_transcript_segments(
    segments: Iterable[dict[str, Any]],
    turns: Iterable[dict[str, Any]],
    resolutions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach the greatest-overlap diarization cluster and reviewed identity."""
    diarization_turns = sorted(turns, key=lambda turn: float(turn["start"]))
    attributed: list[dict[str, Any]] = []
    turn_index = 0
    for original in segments:
        segment = dict(original)
        start, end = segment.get("start"), segment.get("end")
        if start is None or end is None:
            attributed.append(segment)
            continue
        start_value, end_value = float(start), float(end)
        while (
            turn_index < len(diarization_turns)
            and float(diarization_turns[turn_index]["end"]) <= start_value
        ):
            turn_index += 1
        candidates: list[tuple[float, dict[str, Any]]] = []
        candidate_index = turn_index
        while (
            candidate_index < len(diarization_turns)
            and float(diarization_turns[candidate_index]["start"]) < end_value
        ):
            candidate = diarization_turns[candidate_index]
            candidates.append((
                max(
                    0.0,
                    min(end_value, float(candidate["end"]))
                    - max(start_value, float(candidate["start"])),
                ),
                candidate,
            ))
            candidate_index += 1
        overlap, turn = max(candidates, key=lambda item: item[0], default=(0.0, None))
        if turn is not None and overlap > 0:
            label = str(turn["speaker"])
            segment["speaker"] = label
            resolution = resolutions.get(label)
            if resolution:
                segment["speaker_status"] = resolution["status"]
                segment["speaker_disposition"] = resolution["disposition"]
                segment["speaker_name"] = resolution["display_name"]
                if resolution.get("speaker_profile_id"):
                    segment["speaker_profile_id"] = resolution["speaker_profile_id"]
        attributed.append(segment)
    return attributed


def representative_clips(
    turns: Iterable[dict[str, Any]], maximum_per_speaker: int = 3
) -> dict[str, list[dict[str, Any]]]:
    """Choose long, separated, bounded clips for fast human validation."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for turn in turns:
        duration = float(turn["end"]) - float(turn["start"])
        if duration >= 1.5:
            grouped[str(turn["speaker"])].append(turn)
    selected: dict[str, list[dict[str, Any]]] = {}
    for speaker, candidates in grouped.items():
        ranked = sorted(
            candidates,
            key=lambda value: min(float(value["end"]) - float(value["start"]), 15),
            reverse=True,
        )
        clips: list[dict[str, Any]] = []
        for candidate in ranked:
            start = float(candidate["start"])
            if any(abs(start - float(existing["start"])) < 30 for existing in clips):
                continue
            clips.append(
                {
                    "start": round(start, 3),
                    "end": round(min(float(candidate["end"]), start + 15), 3),
                }
            )
            if len(clips) == maximum_per_speaker:
                break
        selected[speaker] = sorted(clips, key=lambda value: value["start"])
    return selected


def process_diarization_job(
    database: Session,
    settings: Settings,
    job: Job,
    diarize: Diarize | None = None,
) -> None:
    if job.artifact_id is None or job.session_id is None:
        raise ValueError("Diarization job requires normalized audio and a session")
    source = database.get(Artifact, job.artifact_id)
    game_session = database.get(GameSession, job.session_id)
    if source is None or game_session is None or source.kind != "normalized_audio":
        raise ValueError("Normalized audio for diarization is not available")
    source_path = _contained_path(settings.artifact_root, source.relative_path)
    source_id = source.id
    created_by_id = source.created_by_id
    campaign_id = game_session.campaign_id
    session_id = game_session.id
    # Do not retain relation locks during hours-long model inference.
    database.commit()
    turns = [
        {"start": round(start, 3), "end": round(end, 3), "speaker": speaker}
        for start, end, speaker in (diarize or _pyannote_diarizer(settings))(source_path)
        if end > start
    ]
    if not turns:
        raise ValueError("Diarization produced no speaker turns")
    document = {
        "schema_version": 1,
        "provider": settings.diarization_provider,
        "model": settings.diarization_model,
        "source_artifact_id": str(source_id),
        "turns": turns,
        "clusters": [
            {"label": label, "representative_clips": clips}
            for label, clips in sorted(representative_clips(turns).items())
        ],
    }
    relative = (
        Path(str(campaign_id))
        / str(session_id)
        / "diarization"
        / f"{job.id}.json"
    )
    destination = _contained_path(settings.artifact_root, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.partial")
    encoded = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, destination)
        database.add(
            Artifact(
                id=uuid.uuid4(),
                session_id=session_id,
                kind="diarization",
                relative_path=relative.as_posix(),
                original_filename=destination.name,
                media_type="application/json",
                size_bytes=len(encoded),
                sha256=hashlib.sha256(encoded).hexdigest(),
                visibility="gm",
                created_by_id=created_by_id,
            )
        )
        database.commit()
    except Exception:
        database.rollback()
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


def _pyannote_diarizer(settings: Settings) -> Diarize:
    pipeline = _cached_pipeline(
        settings.diarization_model,
        settings.diarization_device,
        settings.huggingface_token,
        str(settings.model_root),
    )

    def diarize(path: Path) -> Iterable[tuple[float, float, str]]:
        output = pipeline(_load_pcm_wav(path))
        annotation = output.exclusive_speaker_diarization
        return ((turn.start, turn.end, speaker) for turn, speaker in annotation)

    return diarize


def _load_pcm_wav(path: Path) -> dict[str, Any]:
    """Load our normalized PCM WAV directly, avoiding optional TorchCodec decoding."""
    import torch

    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("Diarization requires mono 16-bit PCM normalized audio")
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    waveform = torch.frombuffer(bytearray(frames), dtype=torch.int16).to(torch.float32)
    waveform = (waveform / 32768.0).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": sample_rate}


@lru_cache(maxsize=1)
def _cached_pipeline(model: str, device: str, token: str | None, model_root: str) -> Any:
    os.environ.setdefault("HF_HOME", model_root)
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "0")
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(model, token=token)
    if pipeline is None:
        raise ValueError(
            "Unable to load diarization model; accept its terms and configure a Hugging Face token"
        )
    pipeline.to(torch.device(device))
    return pipeline
