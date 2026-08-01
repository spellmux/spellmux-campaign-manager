"""Provider-independent session analysis with an Ollama structured-output adapter."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from campaign_manager.comparison import parse_timed_text
from campaign_manager.config import Settings
from campaign_manager.diarization import attribute_transcript_segments, cluster_resolutions
from campaign_manager.models import (
    AnalysisProposal,
    Artifact,
    CampaignGuideEntry,
    GameSession,
    Job,
    SpeakerReview,
    User,
)
from campaign_manager.review import read_artifact


class ExtractedEvidence(BaseModel):
    segment_ids: list[int] = Field(default_factory=list, max_length=10)
    quote: str = Field(min_length=1, max_length=2_000)


class ExtractedProposal(BaseModel):
    kind: Literal[
        "session_summary", "character", "location", "item", "spell", "creature",
        "quest", "faction", "deity", "rule", "important_decision", "unresolved_question",
    ]
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20_000)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    evidence: list[ExtractedEvidence] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0, le=1)
    visibility: Literal["gm", "player"] = "gm"


class AnalysisResult(BaseModel):
    proposals: list[ExtractedProposal] = Field(default_factory=list, max_length=200)


Analyze = Callable[[str, str, dict[str, Any]], tuple[AnalysisResult, dict[str, Any]]]


def ollama_status(settings: Settings, timeout: float = 3) -> dict[str, Any]:
    """Return bounded readiness diagnostics without exposing the model service publicly."""
    if settings.analysis_provider != "ollama":
        return {"configured": False, "ready": False, "model": settings.analysis_model, "models": []}
    request = urllib.request.Request(f"{settings.analysis_base_url}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        return {
            "configured": True, "ready": False, "model": settings.analysis_model,
            "models": [], "detail": str(exc)[:500],
        }
    models = [item.get("name") for item in envelope.get("models", []) if item.get("name")]
    requested_base = settings.analysis_model.split(":", 1)[0]
    available = any(name == settings.analysis_model or name.split(":", 1)[0] == requested_base for name in models)
    return {
        "configured": True, "ready": available, "model": settings.analysis_model,
        "models": models,
        "detail": None if available else "Configured model has not been pulled",
    }


def process_analysis_job(
    database: Session, settings: Settings, job: Job, analyze: Analyze | None = None
) -> None:
    if job.session_id is None or job.artifact_id is None:
        raise ValueError("Analysis job requires a session and source artifact")
    game_session = database.get(GameSession, job.session_id)
    source = database.get(Artifact, job.artifact_id)
    if game_session is None or source is None or source.session_id != game_session.id:
        raise ValueError("Analysis source or session no longer exists")
    if source.kind not in {"raw_transcript", "corrected_transcript", "source_transcript", "source_notes"}:
        raise ValueError("Analysis requires a transcript or notes artifact")
    creator_id = job.payload.get("requested_by_id")
    creator = database.get(User, uuid.UUID(str(creator_id))) if creator_id else None
    if creator is None:
        raise ValueError("Analysis job requester no longer exists")

    guide = list(database.scalars(select(CampaignGuideEntry).where(
        CampaignGuideEntry.campaign_id == game_session.campaign_id,
        CampaignGuideEntry.is_active.is_(True),
    ).order_by(CampaignGuideEntry.kind, CampaignGuideEntry.canonical_name)))
    segments = _source_segments(read_artifact(settings, source))
    diarization = database.scalar(select(Artifact).where(
        Artifact.session_id == game_session.id,
        Artifact.kind == "diarization",
    ).order_by(Artifact.created_at.desc()))
    if diarization is not None and any(segment.get("start") is not None for segment in segments):
        diarization_document = read_artifact(settings, diarization)
        reviews = list(database.scalars(select(SpeakerReview).where(
            SpeakerReview.session_id == game_session.id,
        )))
        segments = attribute_transcript_segments(
            segments,
            diarization_document.get("turns", []),
            cluster_resolutions(reviews),
        )
    prompt, included = build_analysis_prompt(game_session, guide, segments, settings.analysis_max_input_chars)
    result, response_metadata = (analyze or ollama_analyzer(settings))(
        prompt, settings.analysis_model, AnalysisResult.model_json_schema()
    )

    # A retry replaces only still-unreviewed proposals from the same source.
    replaceable = database.scalars(select(AnalysisProposal).where(
        AnalysisProposal.session_id == game_session.id,
        AnalysisProposal.status == "proposed",
    )).all()
    for proposal in replaceable:
        if proposal.run_metadata.get("source_artifact_id") == str(source.id):
            database.delete(proposal)
    segment_map = {index: segment for index, segment in included}
    for extracted in result.proposals:
        evidence = []
        for item in extracted.evidence:
            referenced = [segment_map[index] for index in item.segment_ids if index in segment_map]
            evidence.append({
                "quote": item.quote,
                "artifact_id": str(source.id),
                "start_seconds": next((s.get("start") for s in referenced if s.get("start") is not None), None),
                "end_seconds": next((s.get("end") for s in reversed(referenced) if s.get("end") is not None), None),
            })
        database.add(AnalysisProposal(
            session_id=game_session.id, kind=extracted.kind, title=extracted.title.strip(),
            body=extracted.body.strip(), aliases=list(dict.fromkeys(a.strip() for a in extracted.aliases if a.strip())),
            evidence=evidence, confidence=extracted.confidence, visibility=extracted.visibility,
            provider="ollama", model=settings.analysis_model,
            run_metadata={"source_artifact_id": str(source.id), "job_id": str(job.id), **response_metadata},
            created_by_id=creator.id,
        ))
    database.commit()


def _source_segments(document: Any) -> list[dict[str, Any]]:
    if isinstance(document, dict) and isinstance(document.get("segments"), list):
        return [segment for segment in document["segments"] if str(segment.get("text", "")).strip()]
    if isinstance(document, str):
        return parse_timed_text(document)
    raise ValueError("Analysis source has no readable text")


def build_analysis_prompt(
    game_session: GameSession,
    guide: list[CampaignGuideEntry],
    segments: list[dict[str, Any]],
    max_chars: int,
) -> tuple[str, list[tuple[int, dict[str, Any]]]]:
    guide_lines = [
        f"- {entry.kind}: {entry.canonical_name}; aliases={', '.join(entry.aliases) or 'none'}; notes={entry.notes}"
        for entry in guide
    ]
    prefix = f"""Analyze this tabletop RPG session for a GM review inbox.
Session: {game_session.title}
Session description: {game_session.description or 'none'}

Campaign truth and spelling guide:
{chr(10).join(guide_lines) or '- none'}

Rules:
- Return only claims supported by the supplied source. Never invent missing details.
- Prefer canonical spellings from the campaign guide.
- Create one session_summary plus distinct typed findings when supported.
- Evidence must quote the source and identify its bracketed segment numbers.
- Mark secrets, uncertain identity, enemy plans, and unresolved questions as GM visibility.
- Confidence measures source support, not narrative importance.

Source segments:
"""
    remaining = max(0, max_chars - len(prefix))
    included: list[tuple[int, dict[str, Any]]] = []
    lines: list[str] = []
    for index, segment in enumerate(segments):
        timing = ""
        if segment.get("start") is not None:
            timing = f" {segment.get('start'):.2f}-{segment.get('end', segment.get('start')):.2f}s"
        speaker = segment.get("speaker_name") or segment.get("speaker")
        attribution = f" {speaker}:" if speaker else ""
        line = f"[{index}{timing}]{attribution} {str(segment.get('text', '')).strip()}"
        if len(line) + 1 > remaining:
            break
        lines.append(line)
        included.append((index, segment))
        remaining -= len(line) + 1
    if not included:
        raise ValueError("Analysis input limit is too small for any source text")
    return prefix + "\n".join(lines), included


def ollama_analyzer(settings: Settings) -> Analyze:
    def analyze(prompt: str, model: str, schema: dict[str, Any]) -> tuple[AnalysisResult, dict[str, Any]]:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": schema,
            "options": {"temperature": 0, "num_ctx": settings.analysis_context_tokens},
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{settings.analysis_base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=settings.analysis_timeout_seconds) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2_000]
            raise RuntimeError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        content = envelope.get("message", {}).get("content")
        if not isinstance(content, str):
            raise TypeError("Ollama response did not contain message content")
        result = AnalysisResult.model_validate_json(content)
        metadata = {key: envelope[key] for key in (
            "created_at", "done_reason", "total_duration", "load_duration", "prompt_eval_count", "eval_count"
        ) if key in envelope}
        return result, metadata
    return analyze
