import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from campaign_manager.review import normalized_audio_clip, read_artifact


def test_read_artifact_rejects_path_escape(tmp_path) -> None:
    settings = SimpleNamespace(artifact_root=tmp_path)
    artifact = SimpleNamespace(relative_path="../secret.txt", media_type="text/plain")

    with pytest.raises(ValueError, match="escapes"):
        read_artifact(settings, artifact)


def test_read_artifact_parses_json(tmp_path) -> None:
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps({"segments": [{"id": 1, "text": "Hello"}]}), encoding="utf-8")
    settings = SimpleNamespace(artifact_root=tmp_path)
    artifact = SimpleNamespace(relative_path=path.name, media_type="application/json")

    assert read_artifact(settings, artifact)["segments"][0]["text"] == "Hello"


def test_normalized_audio_clip_is_bounded_and_valid_wav(tmp_path) -> None:
    path = tmp_path / "normalized.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 32_000)
    settings = SimpleNamespace(artifact_root=tmp_path)
    artifact = SimpleNamespace(relative_path=Path(path.name))

    clip = normalized_audio_clip(settings, artifact, 0.5, 1.5)

    output = tmp_path / "clip.wav"
    output.write_bytes(clip)
    with wave.open(str(output), "rb") as audio:
        assert audio.getnframes() == 16_000
        assert audio.getframerate() == 16_000
    with pytest.raises(ValueError, match="between"):
        normalized_audio_clip(settings, artifact, 0, 31)
