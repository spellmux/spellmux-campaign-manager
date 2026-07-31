from types import SimpleNamespace

import pytest

from campaign_manager.transcription import _contained_path, build_initial_prompt


def test_campaign_guide_builds_bounded_whisper_prompt() -> None:
    entries = [
        SimpleNamespace(kind="character", canonical_name="Tasha", aliases=["Iggwilv"], notes=""),
        SimpleNamespace(
            kind="pronunciation",
            canonical_name="Prismeer",
            aliases=[],
            notes="Pronounced PRIZ-meer.",
        ),
    ]

    prompt = build_initial_prompt(entries)

    assert "character: Tasha (also heard as: Iggwilv)" in prompt
    assert "pronunciation: Prismeer — Pronounced PRIZ-meer." in prompt


def test_artifact_path_must_remain_below_root(tmp_path) -> None:
    assert _contained_path(tmp_path, "campaign/audio.wav").is_relative_to(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        _contained_path(tmp_path, "../outside.wav")
