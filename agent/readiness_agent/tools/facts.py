"""Facts live in session state, one key per fact, so parallel tool calls never overwrite each other.

A shared nested dict would lose writes: ADK merges the state deltas of parallel function calls
key by key, so two verify_shelter calls writing the same "facts" key would race. Instead every
fact is its own key ("fact|<kind>|<id>"); collect() rebuilds the {kind: {id: value}} view that the
API assembles the brief from, and digest() builds the compact JSON the writer reads.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

PREFIX = "fact|"


def put(tool_context, kind: str, key: str, value: Any) -> None:
    tool_context.state[f"{PREFIX}{kind}|{key}"] = value


def collect(state: Mapping) -> dict[str, dict[str, Any]]:
    """Accepts a plain dict (session.state) or an ADK State (which has to_dict, not keys)."""
    items = state.to_dict() if hasattr(state, "to_dict") else dict(state)
    facts: dict[str, dict[str, Any]] = {}
    for k, v in items.items():
        if isinstance(k, str) and k.startswith(PREFIX):
            _, kind, key = k.split("|", 2)
            facts.setdefault(kind, {})[key] = v
    return facts


def source_map(verifications: dict[str, dict]) -> dict[tuple[str, str], str]:
    """(shelter_id, per-verification source id) -> brief-wide source id.

    Each verification numbers its own sources from src_1. The writer and the brief need ids that
    are unique across the brief, so both use this one deterministic numbering (by shelter_id)."""
    mapping, n = {}, 1
    for sid in sorted(verifications):
        for src in verifications[sid].get("sources", []):
            mapping[(sid, src["id"])] = f"src_{n}"
            n += 1
    return mapping


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _ints(v: Any) -> Any:
    """BigQuery returns counts as FLOAT64; show 401.0 as 401 so the writer quotes it that way."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, dict):
        return {k: _ints(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_ints(x) for x in v]
    return v


def digest(facts: dict[str, dict[str, Any]]) -> str:
    """Compact JSON of what the writer may refer to: ids, the numbers behind each judgment, and
    verification outcomes. The writer references ids only; the API fills in every number."""
    out: dict[str, Any] = {}
    if "county" in facts:
        out["county"] = next(iter(facts["county"].values()))
    if "tracts" in facts:
        out["tracts"] = [
            _drop_none({k: t.get(k) for k in (
                "geo_id", "total_pop", "households_no_vehicle", "pct_no_vehicle", "pop_65_plus",
                "pct_age_65_plus", "pct_disability", "mobile_homes", "group_quarters_pop", "need_score",
                "nearest_shelter_km", "nearest_wheelchair_yes_km")})
            for t in next(iter(facts["tracts"].values()))["tracts"]]
    if "care" in facts:
        out["care_facilities"] = [
            _drop_none({k: c.get(k) for k in ("ccn", "name", "certified_beds", "nearest_generator_yes_km")})
            for c in next(iter(facts["care"].values()))["facilities"]]
    if "power" in facts:
        out["power_dependence"] = next(iter(facts["power"].values()))
    if "candidates" in facts:
        out["candidates"] = {
            geo: {"counts_within_reach": c["counts_within_reach"],
                  "shelters": [_drop_none({k: s.get(k) for k in (
                      "shelter_id", "name", "usage", "straight_line_km", "within_reach",
                      "evacuation_capacity", "wheelchair_accessible", "generator_onsite",
                      "in_surge_slosh_area", "in_100_yr_floodplain", "is_medical")})
                      for s in c["shelters"][:12]]}
            for geo, c in facts["candidates"].items()}
    if "verifications" in facts:
        smap = source_map(facts["verifications"])
        out["verifications"] = {
            sid: _drop_none({
                "status": v["status"], "maps_name": v.get("maps_name"), "record_name": v.get("record_name"),
                "place_type": v.get("place_type"), "wheelchair": v.get("wheelchair"),
                "drive_minutes": (v.get("drive") or {}).get("drive_minutes"),
                "source_ids": [smap[(sid, s["id"])] for s in v.get("sources", [])],
            })
            for sid, v in facts["verifications"].items()}
    if "allocation" in facts:
        a = next(iter(facts["allocation"].values()))
        out["seat_plan"] = {"totals": a["totals"], "max_km": a["max_km"], "unassigned_tracts": a.get("unassigned_tracts"),
                            "site_options": [{k: o[k] for k in ("geo_id", "persons_covered")} for o in a["site_options"]]}
    if "gaps" in facts:
        out["gaps"] = next(iter(facts["gaps"].values()))["gaps"]
    return json.dumps(_ints(out), separators=(",", ":"), default=str)
