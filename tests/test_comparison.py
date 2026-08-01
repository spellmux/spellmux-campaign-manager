from campaign_manager.comparison import compare_transcripts, parse_timed_text


def test_parse_vtt_preserves_timestamps_and_removes_tags() -> None:
    cues = parse_timed_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\n<v GM>Hello, heroes.</v>\n"
    )

    assert cues == [{"start": 1.0, "end": 3.5, "text": "Hello, heroes."}]


def test_compare_transcripts_aligns_different_segmentation() -> None:
    native = [
        {"start": 1.0, "end": 2.0, "text": "Caelen enters the room."},
        {"start": 2.0, "end": 4.0, "text": "He opens the red door."},
    ]
    source = (
        "WEBVTT\n\n00:00:01.100 --> 00:00:04.200\n"
        "Kaelin enters the room and opens the blue door.\n"
    )

    result = compare_transcripts(native, source)

    assert result["native_word_count"] == 9
    assert result["source_word_count"] == 9
    assert 0.6 < result["similarity"] < 1
    assert any(passage["kind"] == "replace" for passage in result["passages"])
    assert result["passages"][0]["native_start"] == 1.0
