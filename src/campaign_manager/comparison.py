"""Deterministic transcript parsing and token-sequence alignment."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

_TIMING = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s+-->\s+"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})"
)
_WORD = re.compile(r"[\w]+(?:['’][\w]+)?", re.UNICODE)
_TAG = re.compile(r"<[^>]+>")


def _seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def parse_timed_text(content: str) -> list[dict[str, Any]]:
    """Parse VTT/SRT cues, falling back to non-empty text blocks."""
    lines = content.replace("\r\n", "\n").split("\n")
    cues: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        timing = _TIMING.search(lines[index])
        if timing is None:
            index += 1
            continue
        text_lines: list[str] = []
        index += 1
        while index < len(lines) and lines[index].strip():
            text_lines.append(_TAG.sub("", lines[index]).strip())
            index += 1
        text = " ".join(line for line in text_lines if line)
        if text:
            cues.append(
                {
                    "start": _seconds(timing.group("start")),
                    "end": _seconds(timing.group("end")),
                    "text": text,
                }
            )
    if cues:
        return cues
    blocks = [block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()]
    return [{"start": None, "end": None, "text": block} for block in blocks]


def _tokens(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for block_index, block in enumerate(blocks):
        for match in _WORD.finditer(str(block.get("text", ""))):
            original = match.group(0)
            tokens.append(
                {
                    "normalized": original.casefold().replace("’", "'"),
                    "original": original,
                    "block": block_index,
                }
            )
    return tokens


def compare_transcripts(
    native_segments: list[dict[str, Any]], source_content: str
) -> dict[str, Any]:
    source_segments = parse_timed_text(source_content)
    native_tokens = _tokens(native_segments)
    source_tokens = _tokens(source_segments)
    matcher = SequenceMatcher(
        None,
        [token["normalized"] for token in native_tokens],
        [token["normalized"] for token in source_tokens],
        autojunk=False,
    )
    passages = []
    for kind, native_start, native_end, source_start, source_end in matcher.get_opcodes():
        native_slice = native_tokens[native_start:native_end]
        source_slice = source_tokens[source_start:source_end]
        native_blocks = sorted({token["block"] for token in native_slice})
        source_blocks = sorted({token["block"] for token in source_slice})
        passages.append(
            {
                "kind": kind,
                "native_text": " ".join(token["original"] for token in native_slice),
                "source_text": " ".join(token["original"] for token in source_slice),
                "native_start": (
                    native_segments[native_blocks[0]].get("start") if native_blocks else None
                ),
                "native_end": (
                    native_segments[native_blocks[-1]].get("end") if native_blocks else None
                ),
                "source_start": (
                    source_segments[source_blocks[0]].get("start") if source_blocks else None
                ),
            }
        )
    return {
        "similarity": round(matcher.ratio(), 4),
        "native_word_count": len(native_tokens),
        "source_word_count": len(source_tokens),
        "passages": passages,
    }
