"""Curated Markdown drafts and guarded OtterWiki Git publishing."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

from campaign_manager.models import AnalysisProposal, GameSession


def default_target_path(game_session: GameSession) -> str:
    slug = re.sub(r"[^a-z0-9]+", " ", game_session.title.casefold()).strip()
    if not slug:
        slug = str(game_session.id)
    return f"session summaries/{slug}.md"


def render_player_draft(game_session: GameSession, proposals: list[AnalysisProposal]) -> str:
    summaries = [item for item in proposals if item.kind == "session_summary"]
    sections = {
        "important_decision": "Important decisions",
        "quest": "Quests",
        "character": "Characters",
        "location": "Locations",
        "item": "Items",
        "spell": "Spells",
        "creature": "Creatures",
        "faction": "Factions",
        "deity": "Deities",
        "rule": "Rules and rulings",
        "unresolved_question": "Open questions",
    }
    lines = [f"# {game_session.title}"]
    if game_session.session_date:
        lines.extend(("", f"*{game_session.session_date.isoformat()}*"))
    if summaries:
        for summary in summaries:
            lines.extend(("", summary.body or summary.title))
    elif game_session.description:
        lines.extend(("", game_session.description))
    for kind, heading in sections.items():
        matching = [item for item in proposals if item.kind == kind]
        if not matching:
            continue
        lines.extend(("", f"## {heading}"))
        for item in matching:
            detail = item.body.strip()
            lines.append(f"- **{item.title}**{f': {detail}' if detail else ''}")
    lines.append("")
    return "\n".join(lines)


def validate_target_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value.strip().replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or path.suffix.casefold() != ".md":
        raise ValueError("Publish target must be a relative Markdown path without parent traversal")
    if not path.parts or any(part in {"", ".", ".git"} for part in path.parts):
        raise ValueError("Invalid publish target path")
    return path


def publish_to_otterwiki(
    repository: Path,
    target_path: str,
    content: str,
    commit_message: str,
    expected_blob_hash: str | None,
    confirm_overwrite: bool,
) -> tuple[str, str]:
    repository = repository.resolve()
    if not (repository / ".git").is_dir():
        raise ValueError("Configured OtterWiki path is not a Git repository")
    relative = validate_target_path(target_path)
    destination = (repository / Path(*relative.parts)).resolve()
    if not destination.is_relative_to(repository) or (repository / ".git") in destination.parents:
        raise ValueError("Publish target escapes the wiki repository")
    status = _git(repository, "status", "--porcelain", "--", relative.as_posix()).stdout.strip()
    if status:
        raise RuntimeError("The target page has uncommitted OtterWiki changes")
    if destination.exists():
        current_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
        if expected_blob_hash is None and not confirm_overwrite:
            raise FileExistsError("Target page already exists; explicit overwrite confirmation is required")
        if expected_blob_hash is not None and current_hash != expected_blob_hash:
            raise RuntimeError("Target page changed since the last Campaign Manager publication")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    temporary = destination.with_name(f".{destination.name}.campaign-manager.partial")
    temporary.write_bytes(encoded)
    os.replace(temporary, destination)
    _git(repository, "add", "--", relative.as_posix())
    changed = _git(repository, "diff", "--cached", "--quiet", "--", relative.as_posix(), check=False)
    if changed.returncode != 0:
        _git(
            repository, "-c", "user.name=Campaign Manager",
            "-c", "user.email=campaign-manager@localhost",
            "commit", "-m", commit_message, "--", relative.as_posix(),
        )
    commit = _git(repository, "rev-parse", "HEAD").stdout.strip()
    return commit, hashlib.sha256(encoded).hexdigest()


def _git(repository: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repository}", "-C", str(repository), *args],
        text=True, capture_output=True, check=check, timeout=60,
    )
