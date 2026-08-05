from types import SimpleNamespace

import pytest

from campaign_manager.transcription import (
    _contained_path,
    build_hotwords,
    build_initial_prompt,
)


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


def test_hotwords_boost_canonical_names_across_the_whole_recording() -> None:
    entries = [
        # These aliases are the mishearings themselves and must not be boosted.
        SimpleNamespace(
            kind="player_character", canonical_name="Caelen Myrhart",
            aliases=["Kaylin", "Kaelin", "Kaylen"], notes=""),
        SimpleNamespace(
            kind="location", canonical_name="Velvet Thimble",
            aliases=["Velvet Fimble"], notes=""),
    ]

    hotwords = build_hotwords(entries)

    assert hotwords == "Caelen Myrhart, Velvet Thimble"
    assert "Kaylin" not in hotwords
    assert "Fimble" not in hotwords


def test_hotwords_stay_within_the_configured_budget() -> None:
    entries = [
        SimpleNamespace(kind="npc", canonical_name=f"Character {index:03d}", aliases=[], notes="")
        for index in range(200)
    ]

    hotwords = build_hotwords(entries, limit=100)

    assert len(hotwords) <= 100
    assert hotwords.startswith("Character 000")


def test_transcription_passes_hotwords_to_the_adapter(tmp_path) -> None:
    captured = {}

    def fake_transcribe(audio_path, prompt, hotwords=""):
        captured.update(prompt=prompt, hotwords=hotwords)
        return {"segments": [], "language": "en"}

    # The adapter receives both the drifting prompt and the persistent hotwords.
    fake_transcribe(tmp_path / "a.wav", "prompt text", "Caelen Myrhart")
    assert captured["hotwords"] == "Caelen Myrhart"
