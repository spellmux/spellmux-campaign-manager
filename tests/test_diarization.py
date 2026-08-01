import uuid
import wave
from types import SimpleNamespace

import pytest

from campaign_manager.diarization import (
    _load_pcm_wav,
    attribute_transcript_segments,
    cluster_resolutions,
    representative_clips,
)


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


def test_reviewed_clusters_attribute_people_and_music() -> None:
    profile_id = uuid.uuid4()
    reviews = [
        SimpleNamespace(
            cluster_label="SPEAKER_00", disposition="confirmed",
            speaker_profile_id=profile_id,
            speaker_profile=SimpleNamespace(display_name="Rob"),
        ),
        SimpleNamespace(
            cluster_label="SPEAKER_01", disposition="featured_song",
            speaker_profile_id=None, speaker_profile=None,
        ),
    ]
    resolutions = cluster_resolutions(reviews)
    segments = attribute_transcript_segments(
        [
            {"id": 0, "start": 1.0, "end": 4.0, "text": "Welcome."},
            {"id": 1, "start": 10.0, "end": 14.0, "text": "Singing."},
        ],
        [
            {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00"},
            {"start": 9.0, "end": 15.0, "speaker": "SPEAKER_01"},
        ],
        resolutions,
    )

    assert segments[0]["speaker_name"] == "Rob"
    assert segments[0]["speaker_profile_id"] == str(profile_id)
    assert segments[1]["speaker_name"] == "Featured Song"
    assert segments[1]["speaker_disposition"] == "featured_song"


def test_conflicting_cluster_reviews_need_attention() -> None:
    reviews = [
        SimpleNamespace(
            cluster_label="SPEAKER_00", disposition="confirmed",
            speaker_profile_id=uuid.uuid4(), speaker_profile=SimpleNamespace(display_name="Rob"),
        ),
        SimpleNamespace(
            cluster_label="SPEAKER_00", disposition="crosstalk",
            speaker_profile_id=None, speaker_profile=None,
        ),
    ]

    assert cluster_resolutions(reviews)["SPEAKER_00"]["status"] == "needs_attention"


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
