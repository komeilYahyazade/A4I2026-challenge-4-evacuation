"""grounding_metadata -> sources[] and per-text citations (BUILD_SPEC.md section 11).

Rules: a None segment.start_index means 0; confidence_scores are never populated, so ignore them;
source ids are src_1, src_2... unique within one response. Only place_id may ever be stored.
"""

from __future__ import annotations

import re
from typing import Any


_ROUTE_TITLE = re.compile(r"^\(-?[\d.]+,\s*-?[\d.]+\) to \(-?[\d.]+,\s*-?[\d.]+\)$")


def _clean_title(title: str | None) -> str | None:
    if title and title.endswith(" - Google Maps"):
        title = title[: -len(" - Google Maps")]
    if title and _ROUTE_TITLE.match(title):  # a routing chunk: coordinates are not a useful link label
        return "Driving directions"
    return title


def sources_from_metadata(gm: Any, start: int = 1) -> tuple[list[dict], dict[int, str]]:
    """Return (sources, chunk_index -> source id) for every Maps chunk in the metadata."""
    sources: list[dict] = []
    by_chunk: dict[int, str] = {}
    for i, chunk in enumerate(getattr(gm, "grounding_chunks", None) or []):
        maps = getattr(chunk, "maps", None)
        if maps is None:
            continue
        sid = f"src_{start + len(sources)}"
        by_chunk[i] = sid
        sources.append({"id": sid, "provider": "Google Maps", "title": _clean_title(maps.title),
                        "uri": maps.uri, "place_id": maps.place_id})
    return sources, by_chunk


def supports(gm: Any) -> list[tuple[int, int, str, list[int]]]:
    """(start, end, text, chunk_indices) for every grounding support; start None -> 0."""
    out = []
    for sp in getattr(gm, "grounding_supports", None) or []:
        seg = sp.segment
        if seg is None:
            continue
        out.append((seg.start_index or 0, seg.end_index or 0, seg.text or "",
                    list(sp.grounding_chunk_indices or [])))
    return out


def segments_from_response(text: str, gm: Any, start: int = 1) -> tuple[list[dict], list[dict]]:
    """Split model text into attributed segments (used by chat). Unsupported text is kept as a
    segment with no sources, so nothing the model said is silently dropped."""
    sources, by_chunk = sources_from_metadata(gm, start)
    sups = sorted(supports(gm), key=lambda s: s[0])
    segments: list[dict] = []
    cursor = 0
    for s, e, seg_text, idx in sups:
        if s > cursor and text[cursor:s].strip():
            segments.append({"text": text[cursor:s].strip(), "source_ids": [], "freshness": "historical"})
        ids = sorted({by_chunk[i] for i in idx if i in by_chunk}, key=lambda x: int(x[4:]))
        body = text[s:e] if e > s else seg_text
        if body.strip():
            segments.append({"text": body.strip(), "source_ids": ids,
                             "freshness": "live" if ids else "historical"})
        cursor = max(cursor, e)
    if text[cursor:].strip():
        segments.append({"text": text[cursor:].strip(), "source_ids": [], "freshness": "historical"})
    return segments, sources


def renumber(objs: list[dict], start: int = 1) -> tuple[list[dict], int]:
    """Make source ids unique across several objects that each numbered from src_1.

    Each obj has `sources` and segment lists (`summary`, `verdict_reason`, ...) whose
    `source_ids` refer to them. Mutates in place; returns (all sources, next free number)."""
    all_sources: list[dict] = []
    n = start
    for obj in objs:
        mapping = {}
        for src in obj.get("sources", []):
            mapping[src["id"]] = f"src_{n}"
            src["id"] = f"src_{n}"
            all_sources.append(src)
            n += 1
        for key in ("summary", "verdict_reason", "reply"):
            for seg in obj.get(key, []) or []:
                seg["source_ids"] = [mapping.get(i, i) for i in seg.get("source_ids", [])]
    return all_sources, n
