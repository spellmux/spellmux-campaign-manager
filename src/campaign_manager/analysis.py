"""Provider-independent session analysis with an Ollama structured-output adapter."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Literal, NamedTuple, get_args

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from campaign_manager.comparison import parse_timed_text
from campaign_manager.compute import probe_ollama, select_analysis_target
from campaign_manager.config import Settings
from campaign_manager.diarization import (
    MUSIC_DISPOSITIONS,
    UNUSABLE_VOICE_DISPOSITIONS,
    attribute_transcript_segments,
    cluster_resolutions,
)
from campaign_manager.models import (
    AnalysisProposal,
    AnalysisRun,
    Artifact,
    CampaignGuideEntry,
    GameSession,
    Job,
    SpeakerCharacterAssignment,
    SpeakerProfile,
    SpeakerReview,
    User,
    utc_now,
)
from campaign_manager.review import read_artifact

GM_SPEAKER_LABEL = "GM"
UNKNOWN_SPEAKER_LABEL = "Unidentified speaker"
NON_SPEECH_LABEL = "Non-speech audio"
# Reviewed clusters that resolve to something other than a person.
NON_SPEECH_DISPOSITIONS = frozenset(MUSIC_DISPOSITIONS | UNUSABLE_VOICE_DISPOSITIONS)
RESERVED_SPEAKER_LABELS = frozenset(
    {GM_SPEAKER_LABEL.casefold(), UNKNOWN_SPEAKER_LABEL.casefold(), NON_SPEECH_LABEL.casefold()}
    | {name.replace("_", " ").casefold() for name in NON_SPEECH_DISPOSITIONS}
    | {"needs attention"}
)
_RAW_CLUSTER_LABEL = re.compile(r"^\s*speaker[_\s-]*\d+\s*$", re.IGNORECASE)
# "Speaker C" is a pseudonym for an unreviewed cluster, never a character name.
_SPEAKER_PSEUDONYM = re.compile(r"^\s*speaker\s+[a-z]{1,3}\s*$", re.IGNORECASE)


def narration_speaker(segment: dict[str, Any]) -> str:
    """Return the name to attribute a transcript line to.

    Real player names never reach the model. A player's name in front of every
    line is what produced findings like "Lyndon's Grapple Attempt" and entities
    described as "Player Lyndon controls Norixius Torrin", and raw cluster labels
    produced entities named "SPEAKER_01 (Bit)". Resolving to the character, the
    GM role, or an explicit unknown makes both impossible to express.
    """
    character = str(segment.get("character_name") or "").strip()
    if character:
        return character
    if segment.get("speaker_profile_id"):
        # A confirmed person with no character is the GM voicing the world.
        return GM_SPEAKER_LABEL
    disposition = str(segment.get("speaker_disposition") or "").strip().casefold()
    if disposition in NON_SPEECH_DISPOSITIONS:
        # Music and crosstalk carry a label but no person. Treating them as a
        # person put song lyrics and unusable audio in the game master's mouth.
        return str(segment.get("speaker_name") or "").strip() or NON_SPEECH_LABEL
    pseudonym = str(segment.get("speaker_pseudonym") or "").strip()
    if pseudonym:
        return pseudonym
    if segment.get("speaker"):
        return UNKNOWN_SPEAKER_LABEL
    return ""


def assign_speaker_pseudonyms(segments: list[dict[str, Any]]) -> dict[str, str]:
    """Give each unreviewed cluster a stable, neutral, distinct name.

    Rendering every unreviewed cluster as the same unknown label would tell the
    model one person spoke the entire session. Distinct pseudonyms keep speaker
    changes visible without exposing a cluster label or a real name, which
    matters because a session is analyzable before its speakers are reviewed.
    """
    mapping: dict[str, str] = {}
    for segment in segments:
        if segment.get("character_name") or segment.get("speaker_profile_id"):
            continue
        label = segment.get("speaker")
        if not label:
            continue
        key = str(label)
        if key not in mapping:
            mapping[key] = f"Speaker {_pseudonym_suffix(len(mapping))}"
        segment["speaker_pseudonym"] = mapping[key]
    return mapping


def _pseudonym_suffix(index: int) -> str:
    """Return A, B, ... Z, AA, AB, ... so any speaker count stays readable."""
    letters = ""
    position = index + 1
    while position:
        position, remainder = divmod(position - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def is_reserved_speaker_name(value: str) -> bool:
    """True when a name is a transcript role or cluster label, never a character."""
    candidate = value.strip().casefold()
    if not candidate:
        return False
    return (
        candidate in RESERVED_SPEAKER_LABELS
        or bool(_RAW_CLUSTER_LABEL.match(candidate))
        or bool(_SPEAKER_PSEUDONYM.match(candidate))
    )


KIND_ALIASES = {
    "event": "scene", "scene_event": "scene", "moment": "memorable_moment",
    "summary": "session_summary", "recap": "session_summary",
    "story_thread": "unresolved_question", "question": "unresolved_question",
    "task": "follow_up", "todo": "follow_up", "rule_question": "rule", "spell": "item", "monster": "creature",
    "entity": "npc",
}


def _resolved_kind(key: Any) -> str | None:
    """Map a model-supplied kind or container key onto a known proposal kind."""
    if not isinstance(key, str):
        return None
    candidate = key.strip().casefold().replace(" ", "_").replace("-", "_")
    for name in (candidate, candidate.removesuffix("s"), candidate.removesuffix("es")):
        if name in PROPOSAL_KINDS:
            return name
        if name in KIND_ALIASES:
            return KIND_ALIASES[name]
    return None


def _evidence_number(item: Any) -> float | None:
    """Return item as a number when it is one, so bare id/second lists are usable."""
    if isinstance(item, bool):
        return None
    if isinstance(item, int | float):
        return float(item)
    if isinstance(item, str):
        try:
            return float(item.strip())
        except ValueError:
            return None
    return None


def _validates_as_evidence(item: Any) -> bool:
    try:
        ExtractedEvidence.model_validate(item)
    except ValidationError:
        return False
    return True


class ExtractedEvidence(BaseModel):
    segment_ids: list[int] = Field(default_factory=list, max_length=10)
    quote: str = Field(default="", max_length=2_000)
    cited_seconds: list[float] = Field(default_factory=list, max_length=2)

    @model_validator(mode="before")
    @classmethod
    def normalize_shape(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"segment_ids": [], "quote": value.strip()}
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        identifiers = normalized.get("segment_ids")
        if identifiers is None and "segment_id" in normalized:
            identifiers = [normalized.pop("segment_id")]
        if isinstance(identifiers, list):
            cleaned: list[int] = []
            for item in identifiers:
                try:
                    candidate = int(item)
                except (TypeError, ValueError):
                    continue
                if candidate not in cleaned:
                    cleaned.append(candidate)
            normalized["segment_ids"] = [*cleaned[:5], *cleaned[-5:]] if len(cleaned) > 10 else cleaned
        quote = normalized.get("quote", "")
        if isinstance(quote, list):
            normalized["quote"] = " ".join(str(item).strip() for item in quote if str(item).strip())
        elif quote is None:
            normalized["quote"] = ""
        return normalized

    @model_validator(mode="before")
    @classmethod
    def accept_singular_segment_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            match = re.match(r"^\s*\[(\d+)[^\]]*\]\s*(.*)$", value, re.DOTALL)
            if match:
                return {"segment_ids": [int(match.group(1))], "quote": match.group(2).strip()}
        if not isinstance(value, dict) or "segment_ids" in value or "segment_id" not in value:
            return value
        normalized = dict(value)
        normalized["segment_ids"] = [normalized.pop("segment_id")]
        return normalized

    @field_validator("segment_ids", mode="before")
    @classmethod
    def normalize_segment_ids(cls, value: Any) -> list[int]:
        """Bound over-broad model evidence without rejecting an otherwise usable result."""
        if not isinstance(value, list):
            return []
        identifiers = []
        for item in value:
            if isinstance(item, int):
                identifier = item
            elif isinstance(item, str) and item.strip().isdigit():
                identifier = int(item.strip())
            else:
                continue
            if identifier not in identifiers:
                identifiers.append(identifier)
        if len(identifiers) <= 10:
            return identifiers
        return [*identifiers[:5], *identifiers[-5:]]


class ExtractedProposal(BaseModel):
    kind: Literal[
        # A named individual is an npc, whatever its species; a creature is a type,
        # which is what D&D calls a monster, so "monster" was the same kind twice.
        # No "character": it only ever meant "a person, unclassified" and was always
        # resolved to npc, while leaving ambiguous entries behind in the guide.
        # No "spell": not worth a dictionary entry of its own.
        "session_summary", "player_character", "npc", "location", "item", "creature",
        "quest", "faction", "deity", "rule", "important_decision", "unresolved_question",
        "scene", "memorable_moment", "follow_up", "table_note",
    ]
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20_000)
    lane: Literal["story", "meta"] = "story"
    aliases: list[str] = Field(default_factory=list, max_length=30)
    evidence: list[ExtractedEvidence] = Field(default_factory=list, max_length=20)
    confidence: float = Field(default=0.5, ge=0, le=1)
    visibility: Literal["gm", "player"] = "gm"

    @model_validator(mode="before")
    @classmethod
    def normalize_misplaced_lane(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if normalized.get("kind") in KIND_ALIASES:
            normalized["kind"] = KIND_ALIASES[normalized["kind"]]
        if normalized.get("lane") in {"gm", "player"}:
            normalized.setdefault("visibility", normalized["lane"])
            normalized.pop("lane", None)
        if normalized.get("kind") == "meta":
            text = f"{normalized.get('title', '')} {normalized.get('body', '')}".casefold()
            if any(term in text for term in ("follow up", "follow-up", "check later", "to-do", "todo", "research")):
                normalized["kind"] = "follow_up"
            elif any(term in text for term in ("rule", "ruling", "mechanic")):
                normalized["kind"] = "rule"
            else:
                normalized["kind"] = "table_note"
        return normalized

    @model_validator(mode="after")
    def classify_lane(self) -> ExtractedProposal:
        self.lane = "meta" if self.kind in {"rule", "follow_up", "table_note"} else "story"
        return self

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: Any) -> list[str]:
        """Accept common structured-alias output while preserving alias names only."""
        if not isinstance(value, list):
            return []
        aliases: list[str] = []
        for item in value:
            if isinstance(item, str):
                alias = item.strip()
            elif isinstance(item, dict):
                candidate = item.get("name") or item.get("alias") or item.get("title")
                alias = candidate.strip() if isinstance(candidate, str) else ""
            else:
                alias = ""
            if alias and alias not in aliases:
                aliases.append(alias)
        return aliases[:30]

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence_count(cls, value: Any) -> list[Any]:
        """Keep representative evidence when a model cites every line in a scene.

        Unparseable entries are dropped rather than failing the whole proposal. A
        finding with weaker evidence is still worth reviewing, and rejecting it
        would discard a chunk's other findings with it.
        """
        if not isinstance(value, list):
            return []
        numbers = [_evidence_number(item) for item in value]
        if value and all(number is not None for number in numbers):
            # Bare number lists are segment ids, or start/end seconds when the
            # model cites the timing shown beside each segment. Only whole
            # numbers can be segment ids; fractional seconds are resolved later
            # against the segment map, so they are carried as a time range.
            identifiers = [int(number) for number in numbers if float(number).is_integer()]
            seconds = [float(number) for number in numbers if not float(number).is_integer()]
            entry: dict[str, Any] = {"segment_ids": identifiers[:10], "quote": ""}
            if seconds:
                entry["cited_seconds"] = [min(seconds), max(seconds)]
            return [entry]
        usable = [item for item in value if _validates_as_evidence(item)]
        if len(usable) <= 20:
            return usable
        return [*usable[:10], *usable[-10:]]


class AnalysisResult(BaseModel):
    proposals: list[ExtractedProposal] = Field(default_factory=list, max_length=40)


Analyze = Callable[[str, str, dict[str, Any]], tuple[AnalysisResult, dict[str, Any]]]

# Derived from the model so the sampling grammar cannot drift from validation.
PROPOSAL_KINDS: tuple[str, ...] = get_args(ExtractedProposal.model_fields["kind"].annotation)

# Ollama compiles the requested format into a sampling grammar, which makes the
# wrong envelope or a non-integer segment id structurally impossible rather than
# something to repair afterwards. Pydantic's own schema is rejected because it
# describes nested evidence through $defs/$ref, so this is written flat and
# deliberately avoids $ref, combinators, and length or range constraints. The
# lenient parser is retained: grammars cannot prevent an output token limit from
# cutting a response short.
ANALYSIS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(PROPOSAL_KINDS)},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "segment_ids": {"type": "array", "items": {"type": "integer"}},
                                "quote": {"type": "string"},
                            },
                            "required": ["segment_ids", "quote"],
                        },
                    },
                    "confidence": {"type": "number"},
                    "visibility": {"type": "string", "enum": ["gm", "player"]},
                },
                "required": ["kind", "title", "body", "evidence", "confidence", "visibility"],
            },
        },
    },
    "required": ["proposals"],
}


def parse_analysis_content(content: str) -> tuple[AnalysisResult, dict[str, Any]]:
    """Turn raw model output into proposals, keeping whatever is usable.

    Small local models stop mid-JSON when they reach the output token limit, and
    occasionally emit an unexpected shape for one proposal. Either would abort a
    whole multi-chunk job if the response were parsed all-or-nothing, so complete
    proposals are salvaged from partial output and individually invalid ones are
    dropped. Diagnostics record what was lost so a thin chunk is visible instead
    of silent.
    """
    diagnostics: dict[str, Any] = {}
    try:
        payload: Any = json.loads(content)
    except ValueError:
        payload = {"proposals": _salvage_proposal_objects(content)}
        diagnostics["recovered_from_truncation"] = True
    if isinstance(payload, list):
        payload = {"proposals": payload}
    if not isinstance(payload, dict):
        raise TypeError("Analysis response was not a JSON object")
    candidates = payload.get("proposals")
    if not isinstance(candidates, list) or not candidates:
        candidates = _proposals_from_kind_keys(payload)
        if candidates:
            diagnostics["recovered_from_kind_keys"] = True
    proposals: list[ExtractedProposal] = []
    dropped = 0
    for item in candidates:
        try:
            proposals.append(ExtractedProposal.model_validate(item))
        except ValidationError:
            dropped += 1
    if dropped:
        diagnostics["dropped_proposals"] = dropped
    if not proposals:
        raise ValueError(
            "Analysis response contained no usable proposals; "
            f"discarded {dropped} malformed entr{'y' if dropped == 1 else 'ies'}"
        )
    return AnalysisResult(proposals=proposals[:40]), diagnostics


def _proposals_from_kind_keys(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Recover proposals from a response keyed by kind instead of "proposals".

    A model asked for one section reliably answers with the section's kind as the
    top-level key, for example {"session_summary": ["paragraph", ...]}. The
    content is usable; only the envelope is wrong, and discarding it threw away
    finished recaps.
    """
    recovered: list[dict[str, Any]] = []
    for key, value in payload.items():
        kind = _resolved_kind(key)
        if kind is None:
            continue
        entries = value if isinstance(value, list) else [value]
        texts = [item for item in entries if isinstance(item, str) and item.strip()]
        if texts:
            if kind == "session_summary":
                # Paragraph arrays are one recap, not one finding per paragraph.
                recovered.append({"kind": kind, "title": "Session recap",
                                  "body": "\n\n".join(text.strip() for text in texts)})
            else:
                recovered.extend(
                    {"kind": kind, "title": _title_from_text(text), "body": text.strip()}
                    for text in texts
                )
        for item in entries:
            if isinstance(item, dict):
                recovered.append({"kind": kind, **item})
    return recovered


def _title_from_text(text: str) -> str:
    """Derive a title for a bare string entry, which carries no title of its own."""
    head = text.strip().split("\n", 1)[0]
    sentence = re.split(r"(?<=[.!?])\s", head, maxsplit=1)[0]
    return (sentence if len(sentence) <= 200 else f"{sentence[:197]}...") or "Untitled"


def _salvage_proposal_objects(content: str) -> list[dict[str, Any]]:
    """Extract the complete objects from a proposals array cut off mid-write."""
    labelled = content.find('"proposals"')
    opening = content.find("[", labelled if labelled != -1 else 0)
    if opening == -1:
        return []
    objects: list[dict[str, Any]] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index in range(opening, len(content)):
        character = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    candidate = json.loads(content[start:index + 1])
                except ValueError:
                    candidate = None
                if isinstance(candidate, dict):
                    objects.append(candidate)
                start = -1
        elif character == "]" and depth == 0:
            break
    return objects


def ollama_status(settings: Settings, timeout: float = 3) -> dict[str, Any]:
    """Return bounded readiness diagnostics without exposing the model service publicly."""
    if settings.analysis_provider != "ollama":
        return {"configured": False, "ready": False, "model": settings.analysis_model, "models": []}
    return {"configured": True, **probe_ollama(
        settings.analysis_base_url, settings.analysis_model, timeout
    )}


def _analysis_run_for_job(
    database: Session, game_session: GameSession, source: Artifact, job: Job
) -> AnalysisRun:
    """The run this job's findings belong to, created once and reused on resume.

    The id lives in the job payload so a resumed job checkpoints into the same
    generation instead of starting a third one, which is how re-running analysis
    used to leave findings from two attempts interleaved in the review queue.
    """
    existing_id = job.payload.get("analysis_run_id")
    if existing_id:
        run = database.get(AnalysisRun, uuid.UUID(str(existing_id)))
        if run is not None and run.session_id == game_session.id:
            run.status = "running"
            database.commit()
            return run
    # A run still marked running when a new one starts was interrupted by a
    # restart; saying so is more useful than leaving it looking live forever.
    for stale in database.scalars(select(AnalysisRun).where(
        AnalysisRun.session_id == game_session.id, AnalysisRun.status == "running"
    )):
        stale.status = "interrupted"
    run = AnalysisRun(
        session_id=game_session.id,
        source_artifact_id=source.id,
        job_id=job.id,
        provider="ollama",
        status="running",
    )
    database.add(run)
    database.flush()
    job.payload = {**job.payload, "analysis_run_id": str(run.id)}
    database.commit()
    return run


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

    run = _analysis_run_for_job(database, game_session, source, job)

    guide = list(database.scalars(select(CampaignGuideEntry).where(
        CampaignGuideEntry.campaign_id == game_session.campaign_id,
        CampaignGuideEntry.is_active.is_(True),
    ).order_by(CampaignGuideEntry.kind, CampaignGuideEntry.canonical_name)))
    segments = _source_segments(read_artifact(settings, source))
    diarization = database.scalar(select(Artifact).where(
        Artifact.session_id == game_session.id,
        Artifact.kind == "diarization",
        Artifact.superseded_at.is_(None),
    ).order_by(Artifact.created_at.desc()))
    if diarization is not None and any(segment.get("start") is not None for segment in segments):
        diarization_document = read_artifact(settings, diarization)
        # Only reviews describing this diarization's clusters. A review from a
        # superseded generation names a label this run may have given to someone
        # else, which is how a line ends up attributed to the wrong person.
        reviews = list(database.scalars(select(SpeakerReview).where(
            SpeakerReview.session_id == game_session.id,
            SpeakerReview.diarization_artifact_id.is_(None)
            | (SpeakerReview.diarization_artifact_id == diarization.id),
        )))
        segments = attribute_transcript_segments(
            segments,
            diarization_document.get("turns", []),
            cluster_resolutions(reviews),
        )
    assignment_rows = database.execute(
        select(SpeakerCharacterAssignment, SpeakerProfile, CampaignGuideEntry)
        .join(SpeakerProfile, SpeakerProfile.id == SpeakerCharacterAssignment.speaker_profile_id)
        .join(CampaignGuideEntry, CampaignGuideEntry.id == SpeakerCharacterAssignment.guide_entry_id)
        .where(
            SpeakerProfile.campaign_id == game_session.campaign_id,
            (
                SpeakerCharacterAssignment.session_id.is_(None)
                | (SpeakerCharacterAssignment.session_id == game_session.id)
            ),
        )
        .order_by(SpeakerProfile.display_name, SpeakerCharacterAssignment.is_primary.desc())
    ).all()
    # Resolve each speaker to their character so transcript lines can be attributed
    # in character. Primary assignments win; ordering above puts them first.
    character_by_speaker: dict[str, str] = {}
    for assignment, speaker, character in assignment_rows:
        character_by_speaker.setdefault(str(speaker.id), character.canonical_name)
    for segment in segments:
        profile_id = segment.get("speaker_profile_id")
        character_name = character_by_speaker.get(str(profile_id)) if profile_id else None
        if character_name:
            segment["character_name"] = character_name
    # Unreviewed clusters still need to be told apart from one another.
    assign_speaker_pseudonyms(segments)
    speaker_context = [
        f"{character.canonical_name} is a player character"
        f"{' (primary voice)' if assignment.is_primary else ''}"
        f"{' for this session' if assignment.session_id else ''}"
        f"; notes={assignment.notes or 'none'}"
        for assignment, speaker, character in assignment_rows
    ]
    speaker_context.append(
        f"{GM_SPEAKER_LABEL} is the game master describing the world and voicing NPCs"
    )
    player_character_ids = {character.id for _assignment, _speaker, character in assignment_rows}
    chunk_limit = min(settings.analysis_max_input_chars, settings.analysis_chunk_chars)
    prompts = build_analysis_prompts(
        game_session, guide, segments, chunk_limit, settings.analysis_chunk_overlap_segments,
        speaker_context,
    )
    resume_proposals = []
    if job.payload.get("analysis_progress", {}).get("stage") == "consolidating":
        resume_proposals = checkpoint_analysis_proposals(
            database, game_session, run, segments, guide, player_character_ids
        )
    started = time.monotonic()
    if not resume_proposals:
        job.payload = {
            **job.payload,
            "analysis_progress": {
                "stage": "extracting", "completed_chunks": 0, "total_chunks": len(prompts),
                "percent": 0, "estimated_seconds_remaining": None,
            },
        }
        database.commit()
    analysis_settings = settings
    if analyze is None:
        analysis_settings, target, target_status = select_analysis_target(database, settings)
        if not target_status["ready"]:
            raise RuntimeError(target_status.get("detail") or "No analysis worker is ready")
        job.payload = {
            **job.payload,
            "compute_worker": {
                "id": target.worker_id,
                "name": target.name,
                "model": target.model,
                "source": target.source,
            },
        }
        database.commit()
    run.model = analysis_settings.analysis_model
    database.commit()
    analyzer = analyze or ollama_analyzer(analysis_settings)
    extracted_runs: list[tuple[list[ExtractedProposal], list[tuple[int, dict[str, Any]]]]] = []
    response_metadata: list[dict[str, Any]] = []
    chunk_failures: list[dict[str, Any]] = []
    if resume_proposals:
        extracted_runs.append((resume_proposals, list(enumerate(segments))))
    else:
        for chunk_index, chunk in enumerate(prompts):
            prompt, included = chunk.prompt, chunk.segments
            try:
                result, metadata = analyzer(
                    prompt, analysis_settings.analysis_model,
                    extraction_schema(chunk.candidate_budget),
                )
            except Exception as exc:  # noqa: BLE001 - one chunk must not lose the rest
                # A single unusable chunk previously failed the entire job, so a
                # 36-chunk session was only as reliable as its worst response.
                chunk_failures.append({"chunk_index": chunk_index, "error": str(exc)[:500]})
                job.payload = {
                    **job.payload,
                    "analysis_progress": {
                        **job.payload.get("analysis_progress", {}),
                        "failed_chunks": len(chunk_failures),
                    },
                    "chunk_failures": chunk_failures[-20:],
                }
                database.commit()
                continue
            normalized = canonicalize_character_kinds(
                result.proposals, guide, player_character_ids
            )
            extracted_runs.append((normalized, included))
            response_metadata.append({"chunk_index": chunk_index, **metadata})
            completed = chunk_index + 1
            elapsed = time.monotonic() - started
            remaining = (elapsed / completed) * (len(prompts) - completed)
            job.payload = {
                **job.payload,
                "analysis_progress": {
                    "stage": "extracting" if completed < len(prompts) else "reducing",
                    "completed_chunks": completed, "total_chunks": len(prompts),
                    "percent": round(completed / len(prompts) * 100),
                    "estimated_seconds_remaining": round(remaining),
                },
            }
            checkpoint = merge_chunk_proposals(extracted_runs)
            if checkpoint:
                replace_analysis_proposals(
                    database, game_session, source, creator, job, run, checkpoint,
                    response_metadata, len(prompts), analysis_settings.analysis_model,
                )
            database.commit()
    merged = merge_chunk_proposals(extracted_runs)
    if not merged:
        detail = (
            f" All {len(chunk_failures)} chunk(s) failed; first error: "
            f"{chunk_failures[0]['error']}" if chunk_failures else ""
        )
        # A run that produced nothing must not become the active generation, or
        # approving the previous run's findings would stop working after a retry.
        run.status = "failed"
        run.completed_at = utc_now()
        run.notes = f"no findings; {len(chunk_failures)} chunk failure(s)"
        database.commit()
        raise ValueError(
            "Analysis model returned no findings; the source was not marked complete. "
            "Retry with a smaller source window or a more capable model." + detail
        )

    raw_proposals = [proposal for proposals, _included in extracted_runs for proposal in proposals]
    consolidation_error: str | None = None
    if len(raw_proposals) > 1:
        job.payload = {
            **job.payload,
            "analysis_progress": {
                **job.payload.get("analysis_progress", {}),
                "stage": "consolidating", "percent": 95,
                "raw_finding_count": len(raw_proposals),
            },
        }
        database.commit()
        consolidation_analyzer = analyzer
        if analyze is None:
            consolidation_analyzer = ollama_analyzer(replace(
                analysis_settings,
                analysis_max_output_tokens=max(
                    analysis_settings.analysis_max_output_tokens, 6_144
                ),
            ))
        try:
            consolidated, consolidation_metadata = consolidate_analysis(
                game_session, guide, speaker_context, raw_proposals, consolidation_analyzer,
                analysis_settings.analysis_model,
                min(
                    analysis_settings.analysis_max_input_chars,
                    analysis_settings.analysis_context_tokens,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - keep the extracted findings
            # Editorial consolidation is an improvement pass over findings that are
            # already checkpointed. Losing the whole run because the editor would
            # not produce a recap wastes every chunk that did succeed.
            consolidation_error = str(exc)[:500]
        else:
            consolidated.proposals = canonicalize_character_kinds(
                consolidated.proposals, guide, player_character_ids
            )
            response_metadata.extend(consolidation_metadata)
            consolidated_merged = merge_chunk_proposals([(
                consolidated.proposals, list(enumerate(segments)),
            )])
            if consolidated_merged:
                merged = consolidated_merged
                replace_analysis_proposals(
                    database, game_session, source, creator, job, run, merged,
                    response_metadata, len(prompts), analysis_settings.analysis_model,
                )
                database.commit()

    run.status = "succeeded"
    run.finding_count = len(merged)
    run.completed_at = utc_now()
    run.notes = "; ".join(part for part in (
        f"{len(chunk_failures)} chunk failure(s)" if chunk_failures else "",
        f"consolidation failed: {consolidation_error}" if consolidation_error else "",
    ) if part)
    # The run becomes active only now, at the end. Switching earlier would empty
    # the review queue for the duration of a re-analysis and leave nothing to
    # review at all if the run failed.
    game_session.active_analysis_run_id = run.id
    job.payload = {
        **job.payload,
        "analysis_progress": {
            **job.payload.get("analysis_progress", {}),
            "stage": "complete", "percent": 100, "estimated_seconds_remaining": 0,
            "finding_count": len(merged), "raw_finding_count": len(raw_proposals),
            "failed_chunks": len(chunk_failures),
            "consolidation_error": consolidation_error,
        },
    }
    database.commit()


def replace_analysis_proposals(
    database: Session,
    game_session: GameSession,
    source: Artifact,
    creator: User,
    job: Job,
    run: AnalysisRun,
    merged: list[tuple[ExtractedProposal, list[dict[str, object]]]],
    response_metadata: list[dict[str, Any]],
    chunk_count: int,
    model: str,
) -> None:
    """Checkpoint the latest merged findings after every successful chunk.

    Only this run's own findings are replaced. Keying the delete on the run rather
    than the source artifact is what lets a re-analysis build a new generation
    without deleting the generation the GM is still reviewing.
    """
    replaceable = database.scalars(select(AnalysisProposal).where(
        AnalysisProposal.session_id == game_session.id,
        AnalysisProposal.analysis_run_id == run.id,
        AnalysisProposal.status == "proposed",
    )).all()
    for proposal in replaceable:
        database.delete(proposal)
    for extracted, evidence in merged:
        for item in evidence:
            item["artifact_id"] = str(source.id)
        database.add(AnalysisProposal(
            session_id=game_session.id, analysis_run_id=run.id,
            kind=extracted.kind, title=extracted.title.strip(),
            body=extracted.body.strip(), lane=extracted.lane,
            aliases=list(dict.fromkeys(a.strip() for a in extracted.aliases if a.strip())),
            evidence=evidence, confidence=extracted.confidence, visibility=extracted.visibility,
            provider="ollama", model=model,
            run_metadata={
                "source_artifact_id": str(source.id), "job_id": str(job.id),
                "analysis_strategy": "overlapping_chunks", "chunk_count": chunk_count,
                "completed_chunks": len(response_metadata),
                "responses": response_metadata,
                **(response_metadata[0] if len(response_metadata) == 1 else {}),
            },
            created_by_id=creator.id,
        ))
    database.flush()


def checkpoint_analysis_proposals(
    database: Session,
    game_session: GameSession,
    run: AnalysisRun,
    segments: list[dict[str, Any]],
    guide: list[CampaignGuideEntry],
    player_character_ids: set[uuid.UUID],
) -> list[ExtractedProposal]:
    """Rehydrate grounded checkpoints so a failed editorial pass can resume cheaply."""
    rows = database.scalars(select(AnalysisProposal).where(
        AnalysisProposal.session_id == game_session.id,
        AnalysisProposal.analysis_run_id == run.id,
        AnalysisProposal.status == "proposed",
    )).all()
    proposals = []
    for row in rows:
        evidence = []
        for item in row.evidence:
            segment_ids = _segments_for_checkpoint_evidence(item, segments)
            if segment_ids:
                evidence.append({
                    "segment_ids": segment_ids,
                    "quote": str(item.get("quote", ""))[:2_000],
                })
        proposals.append(ExtractedProposal.model_validate({
            "kind": row.kind, "title": row.title, "body": row.body,
            "aliases": row.aliases, "evidence": evidence,
            "confidence": row.confidence if row.confidence is not None else 0.5,
            "visibility": row.visibility,
        }))
    return canonicalize_character_kinds(proposals, guide, player_character_ids)


def _segments_for_checkpoint_evidence(
    evidence: dict[str, object], segments: list[dict[str, Any]]
) -> list[int]:
    start = evidence.get("start_seconds")
    end = evidence.get("end_seconds")
    if not isinstance(start, (int, float)):
        return []
    bounded_end = float(end) if isinstance(end, (int, float)) else float(start) + 15
    overlapping = [
        index for index, segment in enumerate(segments)
        if isinstance(segment.get("start"), (int, float))
        and float(segment["start"]) <= bounded_end
        and float(segment.get("end", segment["start"])) >= float(start)
    ]
    if overlapping:
        return overlapping[:3]
    timed = [
        (abs(float(segment["start"]) - float(start)), index)
        for index, segment in enumerate(segments)
        if isinstance(segment.get("start"), (int, float))
    ]
    return [min(timed)[1]] if timed else []


def canonicalize_character_kinds(
    proposals: list[ExtractedProposal],
    guide: list[CampaignGuideEntry],
    player_character_ids: set[uuid.UUID],
) -> list[ExtractedProposal]:
    """Resolve ambiguous people to canonical PCs or NPCs and remove title annotations."""
    entity_kinds = {"player_character", "npc", "creature"}
    normalized = []
    for original in proposals:
        proposal = original.model_copy(deep=True)
        proposal.aliases = _scoped_aliases(proposal, guide)
        if proposal.kind not in entity_kinds:
            normalized.append(proposal)
            continue
        bare_title = re.sub(r"\s*\([^)]*\)\s*$", "", proposal.title).strip()
        if is_reserved_speaker_name(bare_title) or is_reserved_speaker_name(proposal.title):
            # The GM and unidentified speakers are transcript roles. Promoting one
            # created entities such as "Tim (Host)" and "SPEAKER_01 (Bit)".
            continue
        title_key = re.sub(r"\s*\([^)]*\)\s*$", "", proposal.title).strip().casefold()
        match = None
        for entry in guide:
            names = [entry.canonical_name, *entry.aliases]
            if any(
                title_key == name.casefold() or title_key.startswith(f"{name.casefold()} ")
                for name in names if name.strip()
            ):
                match = entry
                break
        if match is not None:
            proposal.title = match.canonical_name
            if match.id in player_character_ids or match.kind == "player_character":
                proposal.kind = "player_character"
            elif match.kind in {"npc", "creature"}:
                proposal.kind = match.kind
        else:
            proposal.title = re.sub(r"\s*\([^)]*\)\s*$", "", proposal.title).strip()
        normalized.append(proposal)
    return normalized


def _scoped_aliases(
    proposal: ExtractedProposal, guide: list[CampaignGuideEntry]
) -> list[str]:
    """Drop aliases that demonstrably belong to a different guide entity.

    The guide is injected into every chunk prompt with its aliases, and models
    echo them back onto unrelated findings: one run stamped a single entity's
    alias onto five others, which on approval would have merged them. Aliases the
    guide has never seen are kept, since new spellings are the point of the field.
    """
    if not proposal.aliases:
        return proposal.aliases
    owner: dict[str, str] = {}
    for entry in guide:
        identity = str(entry.canonical_name).strip().casefold()
        for name in (entry.canonical_name, *entry.aliases):
            text = str(name).strip().casefold()
            if text:
                owner.setdefault(text, identity)
    own = owner.get(proposal.title.strip().casefold())
    kept: list[str] = []
    for alias in proposal.aliases:
        text = alias.strip().casefold()
        if not text or is_reserved_speaker_name(alias):
            continue
        holder = owner.get(text)
        if holder is None or holder == own:
            kept.append(alias)
    return kept


def build_analysis_prompts(
    game_session: GameSession,
    guide: list[CampaignGuideEntry],
    segments: list[dict[str, Any]],
    max_chars: int,
    overlap_segments: int = 8,
    speaker_context: list[str] | None = None,
) -> list[AnalysisChunk]:
    """Build bounded prompts while retaining global source-segment identities."""
    prompts = []
    cursor = 0
    while cursor < len(segments):
        chunk = build_analysis_prompt(
            game_session, guide, segments[cursor:], max_chars,
            start_index=cursor, speaker_context=speaker_context,
        )
        prompts.append(chunk)
        consumed = len(chunk.segments)
        if cursor + consumed >= len(segments):
            break
        effective_overlap = min(max(0, overlap_segments), max(0, consumed // 5))
        cursor += max(1, consumed - effective_overlap)
    return prompts


def merge_key_title(title: str) -> str:
    """The title reduced to what makes two findings the same finding.

    Asked to merge duplicates, the model instead qualified them: one session ended
    with "Moth CR and Threat Level Discrepancy", the same again as "(Contextual
    Note)", and once more as "(Follow-up)", which an exact-title key kept apart.
    Trailing parentheticals and punctuation are dropped so they collapse into one.
    """
    normalized = re.sub(r"\s*\([^)]*\)\s*$", "", title.strip())
    normalized = re.sub(r"[^\w\s]", " ", normalized.casefold())
    return re.sub(r"\s+", " ", normalized).strip() or title.casefold().strip()


def merge_chunk_proposals(
    runs: list[tuple[list[ExtractedProposal], list[tuple[int, dict[str, Any]]]]]
) -> list[tuple[ExtractedProposal, list[dict[str, object]]]]:
    """Deterministically reduce chunk findings without discarding grounded evidence."""
    merged: dict[tuple[str, str], tuple[ExtractedProposal, list[dict[str, object]]]] = {}
    for proposals, included in runs:
        segment_map = dict(included)
        for proposal in proposals:
            key = (
                proposal.kind,
                "session recap" if proposal.kind == "session_summary"
                else merge_key_title(proposal.title),
            )
            grounded = []
            for item in proposal.evidence:
                referenced = [segment_map[index] for index in item.segment_ids if index in segment_map]
                if not referenced:
                    continue
                quote = item.quote.strip()
                if not quote:
                    quote = " ".join(
                        str(segment.get("text", "")).strip() for segment in referenced[:3]
                    )[:2_000]
                grounded.append({
                    "quote": quote,
                    "start_seconds": next(
                        (s.get("start") for s in referenced if s.get("start") is not None), None
                    ),
                    "end_seconds": next(
                        (s.get("end") for s in reversed(referenced) if s.get("end") is not None), None
                    ),
                })
            if key not in merged:
                merged[key] = (proposal.model_copy(deep=True), grounded)
                continue
            current, current_evidence = merged[key]
            if proposal.body and proposal.body not in current.body:
                current.body = f"{current.body}\n\n{proposal.body}".strip()
            current.aliases = list(dict.fromkeys([*current.aliases, *proposal.aliases]))
            current.confidence = max(current.confidence, proposal.confidence)
            current.visibility = "gm" if "gm" in {current.visibility, proposal.visibility} else "player"
            seen = {(item["quote"], item["start_seconds"], item["end_seconds"]) for item in current_evidence}
            current_evidence.extend(
                item for item in grounded
                if (item["quote"], item["start_seconds"], item["end_seconds"]) not in seen
            )
    return list(merged.values())


def consolidate_analysis(
    game_session: GameSession,
    guide: list[CampaignGuideEntry],
    speaker_context: list[str],
    proposals: list[ExtractedProposal],
    analyzer: Analyze,
    model: str,
    max_chars: int,
) -> tuple[AnalysisResult, list[dict[str, Any]]]:
    """Create Chronicle-shaped sections from compact deterministic candidates."""
    del max_chars  # Retained in the public adapter signature for provider portability.
    prepared = _deduplicate_consolidation_candidates(proposals)
    return _finalize_analysis_sections(
        game_session, guide, speaker_context, prepared, analyzer, model
    )


def _deduplicate_consolidation_candidates(
    proposals: list[ExtractedProposal],
) -> list[ExtractedProposal]:
    """Merge exact entity duplicates and bound material before asking the model to edit it."""
    entity_kinds = {
        "player_character", "npc", "location", "item", "creature", "faction", "deity",
    }
    merged: dict[tuple[str, str], ExtractedProposal] = {}
    ordered: list[ExtractedProposal] = []
    for original in proposals:
        proposal = original.model_copy(deep=True)
        proposal.body = proposal.body[:1_000]
        proposal.evidence = proposal.evidence[:5]
        if proposal.kind not in entity_kinds:
            ordered.append(proposal)
            continue
        key = (proposal.kind, merge_key_title(proposal.title))
        if key not in merged:
            merged[key] = proposal
            ordered.append(proposal)
            continue
        current = merged[key]
        if proposal.body and proposal.body not in current.body:
            current.body = f"{current.body}\n\n{proposal.body}"[:1_000]
        current.aliases = list(dict.fromkeys([*current.aliases, *proposal.aliases]))[:30]
        current.evidence = [*current.evidence, *proposal.evidence][:5]
        current.confidence = max(current.confidence, proposal.confidence)
    return ordered


@dataclass(frozen=True, slots=True)
class SectionSpec:
    name: str
    # Kinds the grammar will permit in the response.
    output_kinds: tuple[str, ...]
    # Kinds drawn from the extracted findings as input material.
    input_kinds: frozenset[str]
    # Which candidate-selection strategy to use.
    candidate_source: str
    instruction: str
    # None means keep everything the model returns.
    maximum: int | None
    required: bool = False


_ENTITY_OUTPUT_KINDS = (
    "player_character", "npc", "location", "item", "creature", "faction", "deity",
)
_NARRATIVE_INPUT_KINDS = frozenset({
    "scene", "memorable_moment", "important_decision", "quest",
    "unresolved_question", "player_character", "npc", "location",
})
REQUIRED_KINDS = frozenset({"session_summary"})

SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        name="recap",
        # Only a recap is expressible here, so scenes cannot crowd it out.
        output_kinds=("session_summary",),
        input_kinds=_NARRATIVE_INPUT_KINDS,
        candidate_source="narrative",
        instruction=(
            "Write the session recap. Return exactly one entry of 5-7 paragraphs and "
            "roughly 350-650 words covering the opening, the major turning points, the "
            "choices characters made, the escalating conflict, and how it ended. Ground "
            "it across the whole session rather than the opening scene, and cite "
            "evidence from early, middle, and late segments."
        ),
        # Models tend to answer with one entry per paragraph, which is fine: every
        # session_summary merges into a single recap, so several entries become one
        # multi-paragraph recap. Capping this at one would discard most of it.
        maximum=8,
        required=True,
    ),
    SectionSpec(
        name="scenes",
        output_kinds=("scene", "memorable_moment"),
        input_kinds=frozenset({"scene", "memorable_moment"}),
        candidate_source="narrative",
        instruction=(
            "Return 6-10 scene entries in chronological order, each under 90 words, and "
            "up to 4 memorable_moment entries under 60 words. Merge duplicates that "
            "describe the same beat."
        ),
        maximum=14,
    ),
    SectionSpec(
        name="entities",
        output_kinds=_ENTITY_OUTPUT_KINDS,
        input_kinds=frozenset(_ENTITY_OUTPUT_KINDS),
        candidate_source="entities",
        instruction=(
            "Return canonical reusable entity updates only. Merge duplicates, use "
            "canonical-name-only titles, and keep each body under 100 words. An action "
            "or reaction is not an entity. Write each body as a reference entry: what "
            "this entity is, and what the party learned about it this session. Do not "
            "state which session it was; that is recorded separately."
        ),
        maximum=14,
    ),
    SectionSpec(
        name="threads",
        output_kinds=("quest", "important_decision", "unresolved_question"),
        input_kinds=frozenset({"quest", "important_decision", "unresolved_question"}),
        candidate_source="threads",
        instruction=(
            "Return meaningful quests, consequential decisions, and genuine in-fiction "
            "open questions. Remove transcript ambiguity and table chatter. Keep each "
            "body under 90 words."
        ),
        maximum=10,
    ),
    SectionSpec(
        name="meta",
        output_kinds=("rule", "follow_up", "table_note"),
        input_kinds=frozenset({"rule", "follow_up", "table_note"}),
        candidate_source="meta",
        instruction=(
            "Return durable rules rulings, explicit follow-ups, and useful scheduling or "
            "technical notes. Remove ordinary table chatter. Keep each body under 80 words."
        ),
        maximum=10,
    ),
)


def extraction_schema(max_items: int) -> dict[str, Any]:
    """Cap the candidate list in the grammar rather than only asking for a cap.

    Told to return at most 18, one chunk returned 30 and ran past the output token
    limit; the truncation salvage recovered it, but a cap the sampler enforces means
    the overrun cannot happen in the first place.
    """
    schema = deepcopy(ANALYSIS_RESPONSE_SCHEMA)
    schema["properties"]["proposals"]["maxItems"] = max_items
    return schema


def _section_schema(output_kinds: tuple[str, ...]) -> dict[str, Any]:
    """Restrict the response grammar to one section's kinds.

    Enforcing this in the sampling grammar rather than the prompt is the point: a
    section told in prose to return only a recap returned scenes and entities and
    no recap, and no amount of post-hoc filtering recovers what was never sampled.
    """
    schema = deepcopy(ANALYSIS_RESPONSE_SCHEMA)
    item = schema["properties"]["proposals"]["items"]
    item["properties"]["kind"]["enum"] = list(output_kinds)
    return schema


def _finalize_analysis_sections(
    game_session: GameSession,
    guide: list[CampaignGuideEntry],
    speaker_context: list[str],
    proposals: list[ExtractedProposal],
    analyzer: Analyze,
    model: str,
) -> tuple[AnalysisResult, list[dict[str, Any]]]:
    """Build each Chronicle section under a schema that only permits its own kinds.

    Asking one prompt to select, rewrite, and respect kind restrictions did not
    work: given a section instruction to return exactly one recap and no entities,
    the model returned eleven scenes, several entities, and no recap at all. The
    kinds a section may contain are now enforced by the sampling grammar instead of
    requested in prose, and the recap is its own call so nothing competes with it.
    """
    combined: list[ExtractedProposal] = []
    metadata: list[dict[str, Any]] = []
    for section in SECTIONS:
        candidates = _section_candidates(proposals, section.candidate_source, section.input_kinds)
        if not candidates:
            continue
        prompt = _section_consolidation_prompt(
            game_session, guide, speaker_context, section.name, section.instruction, candidates
        )
        schema = _section_schema(section.output_kinds)
        result, response = analyzer(prompt, model, schema)
        # The grammar restricts kinds, but an empty array is still expressible.
        if not result.proposals and section.required:
            retry_prompt = prompt + (
                f"\nThis section is empty without at least one {section.output_kinds[0]} entry. "
                "Return it now."
            )
            result, retry_response = analyzer(retry_prompt, model, schema)
            response = {**response, "retry": retry_response}
        returned = len(result.proposals)
        kept = [p for p in result.proposals if p.kind in section.output_kinds]
        wrong_kind = returned - len(kept)
        guarded = 0
        if section.name == "threads":
            before = len(kept)
            kept = [p for p in kept if not _unsupported_identity_thread(p, guide)]
            guarded = before - len(kept)
        trimmed = _trim_section(kept, section)
        if section.required and not kept:
            response = {**response, "missing_required_section": section.name}
        combined.extend(trimmed)
        # Every drop is counted. A section that returned findings and kept none used
        # to look identical to a section that was never asked, which is how an entire
        # run lost its quests and open questions without anything saying so.
        metadata.append({
            "stage": "finalizing", "section": section.name,
            "candidates": len(candidates), "returned": returned, "kept": len(trimmed),
            "dropped_wrong_kind": wrong_kind, "dropped_by_guard": guarded,
            "dropped_over_section_limit": len(kept) - len(trimmed),
            **response,
        })
    bounded = _bounded_result(combined)
    if len(bounded) < len(combined):
        dropped = Counter(p.kind for p in combined) - Counter(p.kind for p in bounded)
        metadata.append({
            "stage": "finalizing", "section": "assembly",
            "dropped_over_response_ceiling": dict(dropped),
        })
    return AnalysisResult(proposals=bounded), metadata


def _trim_section(
    proposals: list[ExtractedProposal], section: SectionSpec
) -> list[ExtractedProposal]:
    """Drop the least confident surplus rather than whatever arrived last.

    Trimming by arrival position assumed the model emits in importance order,
    which is how a required recap was discarded while the check that demanded it
    passed against the untrimmed list.
    """
    if section.maximum is None or len(proposals) <= section.maximum:
        return proposals
    if section.name == "recap":
        # Arrival order is narrative order, and these paragraphs are concatenated
        # into one recap, so reordering by confidence would scramble the prose.
        return proposals[: section.maximum]
    if section.name == "scenes":
        # Scenes are a chronological outline, so keep the earliest ones in order
        # rather than the most confident ones out of order.
        return sorted(proposals, key=_first_evidence_segment)[: section.maximum]
    return sorted(proposals, key=lambda p: p.confidence, reverse=True)[: section.maximum]


def _bounded_result(proposals: list[ExtractedProposal]) -> list[ExtractedProposal]:
    """Respect the response ceiling without letting a required kind fall off.

    The ceiling is the sum of what the sections already allow, so it is a backstop
    rather than a second opinion. A flat 40 silently reordered every section's output
    by confidence and dropped the tail: one run lost all ten of its threads, which
    score lower than scenes and entities by nature, and nothing recorded it.
    """
    ceiling = sum(section.maximum or 0 for section in SECTIONS)
    if len(proposals) <= ceiling:
        return proposals
    required = [p for p in proposals if p.kind in REQUIRED_KINDS]
    optional = [p for p in proposals if p.kind not in REQUIRED_KINDS]
    optional.sort(key=lambda p: p.confidence, reverse=True)
    return [*required[:ceiling], *optional[: max(0, ceiling - len(required))]]


def _section_candidates(
    proposals: list[ExtractedProposal], section: str, kinds: frozenset[str] | set[str]
) -> list[ExtractedProposal]:
    candidates = [proposal for proposal in proposals if proposal.kind in kinds]
    if section == "narrative":
        scenes = sorted(
            (proposal for proposal in candidates if proposal.kind == "scene"),
            key=_first_evidence_segment,
        )[:24]
        scenes = _spread_chronological_scenes(scenes)
        moments = sorted(
            (proposal for proposal in candidates if proposal.kind == "memorable_moment"),
            key=lambda proposal: proposal.confidence, reverse=True,
        )[:6]
        context = sorted(
            (proposal for proposal in candidates if proposal.kind not in {"scene", "memorable_moment"}),
            key=lambda proposal: proposal.confidence, reverse=True,
        )[:12]
        return [*scenes, *moments, *context]
    limit = 28 if section == "entities" else 20
    return sorted(candidates, key=lambda proposal: proposal.confidence, reverse=True)[:limit]


def _spread_chronological_scenes(scenes: list[ExtractedProposal]) -> list[ExtractedProposal]:
    """Put beginning, middle, and ending scenes first so recap synthesis sees the arc."""
    if len(scenes) < 4:
        return scenes
    anchors = [scenes[0], scenes[len(scenes) // 3], scenes[(2 * len(scenes)) // 3], scenes[-1]]
    seen: set[int] = set()
    result: list[ExtractedProposal] = []
    for scene in [*anchors, *scenes]:
        identity = id(scene)
        if identity not in seen:
            seen.add(identity)
            result.append(scene)
    return result


# Phrases that turn a question into a claim that two names are one person. "the
# same" alone is too common ("at the same time") to belong here.
IDENTITY_CLAIM_TERMS = (
    "also known as", "alias", "revealed to be", "is actually", "same person",
    "impersonat", "the same as", "are the same", "is the same", "really is",
    "secretly", "in disguise", "true identity",
)


def _unsupported_identity_thread(
    proposal: ExtractedProposal, guide: list[CampaignGuideEntry]
) -> bool:
    """Drop a thread that claims two characters are one person without a source.

    Matching is per entity across canonical name and aliases, on word boundaries.
    Canonical-name-only substring matching both missed real merges, because
    "Magnus vs. Torin" names neither "Magnus Heartsbane" nor "Norixius Torrin"
    in full, and fired falsely, because "Bit" is a substring of "rabbit".

    Only an identity claim is judged. The earlier form dropped any question naming
    two player characters unless it used identity language, which is backwards: it
    kept the invented merges and threw away ordinary questions about two characters,
    and in one run that was every thread the session had.
    """
    if proposal.kind != "unresolved_question":
        return False
    text = f"{proposal.title} {proposal.body}"
    if not any(term in text.casefold() for term in IDENTITY_CLAIM_TERMS):
        return False
    mentioned = sum(
        1 for entry in guide
        if entry.kind == "player_character" and _mentions_entity(text, entry)
    )
    if mentioned < 2:
        return False
    # The claim stands only if the cited transcript says it, not the model.
    quoted = " ".join(item.quote for item in proposal.evidence).casefold()
    return not any(term in quoted for term in IDENTITY_CLAIM_TERMS)


def _mentions_entity(text: str, entry: CampaignGuideEntry) -> bool:
    """True when text names this entity by canonical name or any alias."""
    for name in (entry.canonical_name, *entry.aliases):
        candidate = str(name).strip()
        if candidate and re.search(rf"\b{re.escape(candidate)}\b", text, re.IGNORECASE):
            return True
    return False


def _first_evidence_segment(proposal: ExtractedProposal) -> int:
    identifiers = [
        identifier for evidence in proposal.evidence for identifier in evidence.segment_ids
    ]
    return min(identifiers) if identifiers else 10**9


def _section_consolidation_prompt(
    game_session: GameSession,
    guide: list[CampaignGuideEntry],
    speaker_context: list[str],
    section: str,
    instruction: str,
    proposals: list[ExtractedProposal],
) -> str:
    guide_lines = [
        f"- {entry.kind}: {entry.canonical_name}; aliases={', '.join(entry.aliases) or 'none'}"
        for entry in guide
    ]
    return f"""Create the {section} section of a tabletop RPG Session Chronicle.
Session: {game_session.title}
Campaign guide:
{chr(10).join(guide_lines) or '- none'}
Player-to-character mapping:
{chr(10).join(f'- {line}' for line in speaker_context) or '- none'}

{instruction}
Return exactly one JSON object with a proposals array and no commentary. Preserve only supplied
segment_ids. Never invent facts. Speakers are not automatically their PCs. Every proposal contains
kind, title, body, aliases, evidence, confidence, and visibility. visibility is gm or player.

Candidate findings JSON:
{json.dumps([_compact_consolidation_candidate(proposal) for proposal in proposals], ensure_ascii=False, separators=(',', ':'))}
"""


def _compact_consolidation_candidate(proposal: ExtractedProposal) -> dict[str, Any]:
    return {
        "kind": proposal.kind,
        "title": proposal.title,
        "body": proposal.body[:600],
        "aliases": proposal.aliases[:10],
        "evidence": [
            {"segment_ids": item.segment_ids, "quote": item.quote[:200]}
            for item in proposal.evidence[:3]
        ],
        "confidence": proposal.confidence,
        "visibility": proposal.visibility,
    }


def _pack_proposals(
    proposals: list[ExtractedProposal], available_chars: int
) -> list[list[ExtractedProposal]]:
    if available_chars < 2_000:
        raise ValueError("Analysis input limit is too small for consolidation")
    batches: list[list[ExtractedProposal]] = []
    current: list[ExtractedProposal] = []
    current_size = 2
    for proposal in proposals:
        size = len(proposal.model_dump_json()) + 1
        if current and current_size + size > available_chars:
            batches.append(current)
            current = []
            current_size = 2
        current.append(proposal)
        current_size += size
    if current:
        batches.append(current)
    return batches


def _consolidation_prompt_prefix(
    game_session: GameSession,
    guide: list[CampaignGuideEntry],
    speaker_context: list[str],
    *,
    final: bool,
) -> str:
    guide_lines = [
        f"- {entry.kind}: {entry.canonical_name}; aliases={', '.join(entry.aliases) or 'none'}; notes={entry.notes}"
        for entry in guide
    ]
    target = "16-24" if final else "at most 10"
    return f"""Consolidate candidate findings from one tabletop RPG session into a GM review package.
Session: {game_session.title}
Session description: {game_session.description or 'none'}

Campaign truth and canonical spelling:
{chr(10).join(guide_lines) or '- none'}

Player-to-character mapping:
{chr(10).join(f'- {line}' for line in speaker_context) or '- none'}

Return exactly one JSON object with a proposals array and no commentary. Return {target} useful
proposals. Preserve only evidence segment_ids present in the candidates; never manufacture evidence.

Required editorial behavior:
- Merge duplicates and near-duplicates. Prefer campaign-guide names and aliases.
- Produce exactly one session_summary on a final pass: a readable 3-5 paragraph narrative recap,
  no more than 450 words.
- Produce 6-10 chronological scene entries on a final pass, each no more than 90 words. Each scene summarizes what happened and
  the important PC choices or consequences. Do not create a separate entity for each action.
- Include up to 4 genuinely memorable_moment entries when warranted, each no more than 60 words.
- Entity kinds are reusable campaign records: player_character, npc, location, item, creature,
  faction, deity. Their title must be only the canonical entity name. Combine all session
  discoveries or developments for that entity into its body, using no more than 100 words.
- A named individual is an npc whatever its species. Use creature only for a kind of creature:
  "Bob the Mock Turtle" is an npc, "Mock Turtle" is a creature.
- A speaker/player is not their PC. Attribute an action to a PC only when the mapping or source supports it.
- Use quest for actionable objectives and unresolved_question only for genuine in-fiction mysteries or
  open story threads.
- Use rule for an explicit mechanics ruling worth remembering; follow_up for deferred rules research,
  promised work, or a GM/player task; table_note for useful scheduling, attendance, recording, or
  technical information.
- Remove greetings, food chatter, incidental jokes, cross-talk, repeated rules lookup, transcript
  ambiguity, and irrelevant real-world discussion. Never turn uncertainty caused by transcription into lore.
- Prefer fewer strong entries. Confidence is evidentiary support, not importance.
- Keep meta entries under 80 words. Omit low-value candidates to stay inside the requested count and limits.
- Every proposal contains kind, title, body, aliases, evidence, confidence, visibility.
- kind is one of session_summary, scene, memorable_moment, player_character, npc,
  location, item, creature, quest, faction, deity, rule, important_decision,
  unresolved_question, follow_up, table_note.
- visibility is gm or player. Secrets, enemy plans, and uncertain identities remain gm.

Candidate findings JSON:
"""


def _source_segments(document: Any) -> list[dict[str, Any]]:
    if isinstance(document, dict) and isinstance(document.get("segments"), list):
        return [segment for segment in document["segments"] if str(segment.get("text", "")).strip()]
    if isinstance(document, str):
        return parse_timed_text(document)
    raise ValueError("Analysis source has no readable text")


# One candidate per this many characters of transcript, which is the density the
# 16,000-character chunk produced: roughly 9,000 characters of transcript and 8
# candidates. Raising the chunk size without raising the cap cut candidate density
# by the same factor -- a 2.8x larger chunk still returned 8 candidates, so the run
# got faster while covering less of the table.
CHARS_PER_CANDIDATE = 1_200
CANDIDATE_FLOOR = 8
# The whole candidate list has to fit in one bounded response, so density stops
# scaling here rather than risking a truncated chunk.
CANDIDATE_CEILING = 20
CANDIDATE_TOKEN = "__CANDIDATE_BUDGET__"


def candidate_budget(transcript_chars: int) -> int:
    """How many candidates to ask a chunk for, given how much source it holds."""
    scaled = transcript_chars // CHARS_PER_CANDIDATE
    return max(CANDIDATE_FLOOR, min(CANDIDATE_CEILING, scaled))


class AnalysisChunk(NamedTuple):
    """One extraction prompt, the segments it covers, and how many findings it may return."""

    prompt: str
    segments: list[tuple[int, dict[str, Any]]]
    candidate_budget: int


def build_analysis_prompt(
    game_session: GameSession,
    guide: list[CampaignGuideEntry],
    segments: list[dict[str, Any]],
    max_chars: int,
    start_index: int = 0,
    speaker_context: list[str] | None = None,
) -> AnalysisChunk:
    guide_lines = [
        f"- {entry.kind}: {entry.canonical_name}; aliases={', '.join(entry.aliases) or 'none'}; notes={entry.notes}"
        for entry in guide
    ]
    # The rules and guide prefix costs several thousand characters, so the candidate
    # budget is scaled to the room actually left for transcript rather than to the
    # whole chunk. The prefix is written with a placeholder, measured, then filled in.
    prefix = f"""Analyze this tabletop RPG session for a GM review inbox.
Session: {game_session.title}
Session description: {game_session.description or 'none'}

Campaign truth and spelling guide:
{chr(10).join(guide_lines) or '- none'}

Speakers in this transcript (already resolved; real player names are deliberately withheld):
{chr(10).join(f'- {line}' for line in (speaker_context or [])) or '- none'}

Rules:
- Extract at most {CANDIDATE_TOKEN} important source-supported candidates. Never invent; use guide spellings.
- Do not summarize each chunk. A later pass builds the session recap.
- Reusable entities have canonical-name-only titles. Actions/reactions are scenes or moments, not entities.
- story: scenes, moments, entities, quests, decisions, and real in-fiction mysteries.
- meta: explicit rulings, promised/deferred follow-ups, useful scheduling, attendance, or technical notes.
- Ignore greetings, food, interruptions, cross-talk, incidental jokes, inconclusive lookup, and transcript noise.
- Speakers are not automatically their PCs. Mark secrets and uncertain identity GM-only.
- "{GM_SPEAKER_LABEL}", "{UNKNOWN_SPEAKER_LABEL}", and "Speaker A"-style names are transcript roles for
  voices that are not yet identified. They are never characters: never make an entity for them,
  never treat one as an NPC, and never use one in a title.
- Name people only by character name. Do not describe a character in terms of who plays them.
- Return exactly one JSON object with a "proposals" array and no surrounding commentary.
- Each proposal contains kind, title, body, aliases, evidence, confidence, visibility.
- kinds: session_summary, player_character, npc, location, item, creature, quest,
  faction, deity, rule, important_decision, unresolved_question, scene, memorable_moment, follow_up, table_note.
- A named individual is an npc whatever its species. Use creature only for a kind of creature:
  "Bob the Mock Turtle" is an npc, "Mock Turtle" is a creature.
- Evidence has 1-3 bracketed segment_ids plus a short exact quote. confidence is 0-1; visibility is gm or player.

Source segments:
"""
    budget = candidate_budget(max(0, max_chars - len(prefix)))
    prefix = prefix.replace(CANDIDATE_TOKEN, str(budget))
    remaining = max(0, max_chars - len(prefix))
    included: list[tuple[int, dict[str, Any]]] = []
    lines: list[str] = []
    for index, segment in enumerate(segments, start=start_index):
        timing = ""
        if segment.get("start") is not None:
            timing = f" {segment.get('start'):.2f}-{segment.get('end', segment.get('start')):.2f}s"
        speaker = narration_speaker(segment)
        attribution = f" {speaker}:" if speaker else ""
        line = f"[{index}{timing}]{attribution} {str(segment.get('text', '')).strip()}"
        if len(line) + 1 > remaining:
            break
        lines.append(line)
        included.append((index, segment))
        remaining -= len(line) + 1
    if not included:
        raise ValueError("Analysis input limit is too small for any source text")
    return AnalysisChunk(prefix + "\n".join(lines), included, budget)


def ollama_analyzer(settings: Settings) -> Analyze:
    def analyze(prompt: str, model: str, schema: dict[str, Any]) -> tuple[AnalysisResult, dict[str, Any]]:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            # Constrain sampling to the schema so the model cannot emit the wrong
            # envelope or a non-integer segment id in the first place. The schema
            # is written flat because Ollama's grammar compiler rejects the
            # $defs/$ref form that Pydantic generates.
            "format": schema,
            "think": False,
            # Release VRAM promptly so a transcription or image job does not
            # wait behind an idle model on a single-GPU worker.
            "keep_alive": f"{settings.analysis_keep_alive_seconds}s",
            "options": {
                "temperature": 0,
                "num_ctx": settings.analysis_context_tokens,
                "num_predict": settings.analysis_max_output_tokens,
            },
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
        result, diagnostics = parse_analysis_content(content)
        metadata = {key: envelope[key] for key in (
            "created_at", "done_reason", "total_duration", "load_duration", "prompt_eval_count", "eval_count"
        ) if key in envelope}
        return result, {**metadata, **diagnostics}
    return analyze
