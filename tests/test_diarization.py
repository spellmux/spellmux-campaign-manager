import uuid
import wave
from types import SimpleNamespace

import pytest

from campaign_manager.diarization import (
    DiarizationResult,
    _load_pcm_wav,
    _speaker_centroids,
    as_diarization_result,
    attribute_transcript_segments,
    cluster_resolutions,
    load_pcm_wav_window,
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


def test_unusable_clip_does_not_conflict_with_confirmed_cluster_identity() -> None:
    profile_id = uuid.uuid4()
    reviews = [
        SimpleNamespace(
            cluster_label="SPEAKER_00", disposition="confirmed",
            speaker_profile_id=profile_id, speaker_profile=SimpleNamespace(display_name="Rob"),
        ),
        SimpleNamespace(
            cluster_label="SPEAKER_00", disposition="crosstalk",
            speaker_profile_id=None, speaker_profile=None,
        ),
    ]

    resolution = cluster_resolutions(reviews)["SPEAKER_00"]

    assert resolution["status"] == "reviewed"
    assert resolution["speaker_profile_id"] == str(profile_id)
    assert resolution["excluded_clip_count"] == 1


def test_cluster_with_only_unusable_clips_needs_attention() -> None:
    reviews = [
        SimpleNamespace(
            cluster_label="SPEAKER_00", disposition="crosstalk",
            speaker_profile_id=None, speaker_profile=None,
        ),
        SimpleNamespace(
            cluster_label="SPEAKER_00", disposition="noise",
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


def test_diarization_retains_voice_centroids_for_enrollment() -> None:
    class FakeAnnotation:
        def labels(self):
            return ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]

    # pyannote zero-pads when it finds fewer centroids than speakers.
    output = SimpleNamespace(
        speaker_diarization=FakeAnnotation(),
        speaker_embeddings=[[0.1, 0.2], [0.3, 0.4], [0.0, 0.0]],
    )

    centroids = _speaker_centroids(output)

    assert centroids == {"SPEAKER_00": [0.1, 0.2], "SPEAKER_01": [0.3, 0.4]}


def test_diarization_accepts_adapters_that_return_only_turns() -> None:
    # Existing providers yield plain tuples; centroids are an optional enrichment.
    plain = as_diarization_result([(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")])
    assert plain.turns == [(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")]
    assert plain.embeddings == {}

    enriched = DiarizationResult(
        turns=[(0.0, 1.0, "SPEAKER_00")],
        embeddings={"SPEAKER_00": [1.0, 0.0]},
        embedding_model="pyannote/speaker-diarization-community-1",
    )
    assert as_diarization_result(enriched) is enriched


def test_wav_window_reads_only_the_requested_clip(tmp_path) -> None:
    pytest.importorskip("torch")
    import struct

    path = tmp_path / "normalized.wav"
    sample_rate = 16_000
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        # Three seconds: 0s silence, 1s loud, 2s silence.
        frames = [0] * sample_rate + [12_000] * sample_rate + [0] * sample_rate
        target.writeframes(struct.pack(f"<{len(frames)}h", *frames))

    window = load_pcm_wav_window(path, 1.0, 2.0)

    assert window["sample_rate"] == sample_rate
    assert window["waveform"].shape == (1, sample_rate)
    # Only the loud second was read, not the whole file.
    assert float(window["waveform"].abs().mean()) > 0.3

    clamped = load_pcm_wav_window(path, 2.5, 99.0)
    assert clamped["waveform"].shape[1] == sample_rate // 2

    with pytest.raises(ValueError, match="empty"):
        load_pcm_wav_window(path, 3.0, 3.0)
