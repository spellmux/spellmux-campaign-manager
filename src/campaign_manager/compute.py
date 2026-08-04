"""Portable compute endpoint discovery and routing."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from campaign_manager.config import Settings
from campaign_manager.models import ComputeWorker


@dataclass(frozen=True, slots=True)
class AnalysisTarget:
    worker_id: str | None
    name: str
    base_url: str
    model: str
    source: str


def probe_ollama(base_url: str, model: str, timeout: float = 3) -> dict[str, Any]:
    """Probe an Ollama-compatible endpoint with a bounded, read-only request."""
    request = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        return {
            "ready": False,
            "model": model,
            "models": [],
            "detail": str(exc)[:500],
        }
    models = [item.get("name") for item in envelope.get("models", []) if item.get("name")]
    requested_base = model.split(":", 1)[0]
    available = any(
        name == model or name.split(":", 1)[0] == requested_base for name in models
    )
    return {
        "ready": available,
        "model": model,
        "models": models,
        "detail": None if available else "Configured model has not been pulled",
    }


def analysis_workers(database: Session) -> list[ComputeWorker]:
    workers = list(database.scalars(
        select(ComputeWorker)
        .where(ComputeWorker.enabled.is_(True), ComputeWorker.provider == "ollama")
        .order_by(ComputeWorker.priority.desc(), ComputeWorker.name)
    ))
    return [worker for worker in workers if "analysis" in worker.capabilities]


def select_analysis_target(
    database: Session, settings: Settings, *, probe: bool = True
) -> tuple[Settings, AnalysisTarget, dict[str, Any]]:
    """Select the first healthy analysis worker, then the environment fallback."""
    for worker in analysis_workers(database):
        status = probe_ollama(worker.base_url, worker.analysis_model) if probe else {"ready": True}
        if status["ready"]:
            routed = replace(
                settings,
                analysis_provider="ollama",
                analysis_base_url=worker.base_url.rstrip("/"),
                analysis_model=worker.analysis_model,
            )
            return routed, AnalysisTarget(
                worker_id=str(worker.id), name=worker.name,
                base_url=worker.base_url.rstrip("/"), model=worker.analysis_model,
                source="managed",
            ), status
    fallback = probe_ollama(settings.analysis_base_url, settings.analysis_model) if (
        probe and settings.analysis_provider == "ollama"
    ) else {
        "ready": settings.analysis_provider == "ollama",
        "model": settings.analysis_model,
        "models": [],
        "detail": None,
    }
    return settings, AnalysisTarget(
        worker_id=None, name="Bundled Ollama", base_url=settings.analysis_base_url,
        model=settings.analysis_model, source="environment",
    ), fallback


def effective_analysis_status(database: Session, settings: Settings) -> dict[str, Any]:
    routed, target, status = select_analysis_target(database, settings)
    del routed
    return {
        "configured": bool(analysis_workers(database)) or settings.analysis_provider == "ollama",
        **status,
        "worker_id": target.worker_id,
        "worker_name": target.name,
        "source": target.source,
    }
