import wave

import pytest

from campaign_manager.diarization import _load_pcm_wav, representative_clips


def test_representative_clips_group_speakers_and_skip_short_turns() -> None:
    turns = [
        {"start": 1.0, "end": 9.0, "speaker": "SPEAKER_00"},
        {"start": 12.0, "end": 13.0, "speaker": "SPEAKER_00"},
        {"start": 42.0, "end": 48.0, "speaker": "SPEAKER_00"},
        {"start": 80.0, "end": 100.0, "speaker": "SPEAKER_01"},
    ]

    clips = representative_clips(turns)

    assert clips["SPEAKER_00"] == [
        {"start": 1.0, "end": 9.0},
        {"start": 42.0, "end": 48.0},
    ]
    assert clips["SPEAKER_01"] == [{"start": 80.0, "end": 95.0}]


def test_load_pcm_wav_returns_in_memory_waveform(tmp_path) -> None:
    pytest.importorskip("torch")
    path = tmp_path / "normalized.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * 160)

    audio = _load_pcm_wav(path)

    assert audio["sample_rate"] == 16_000
    assert tuple(audio["waveform"].shape) == (1, 160)
