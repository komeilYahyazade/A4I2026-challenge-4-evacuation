"""Evidence labels. Every factual claim the agent makes carries one of these.

The labels exist because this challenge mixes facts of very different kinds: a 2022 federal
registry row, a Google Maps lookup from nine seconds ago, a CDC model estimate, and a
county-level count that can never be placed on a block. Blending them into one confident
sentence is the failure mode judges look for.
"""

from typing import Any, Optional

RECORDED = "RECORDED"
UNRECORDED = "UNRECORDED"
VERIFIED_LIVE = "VERIFIED LIVE"
MODELED = "MODELED"
COUNTY_LEVEL = "COUNTY-LEVEL"
APPROXIMATE = "APPROXIMATE"

LABELS: dict[str, str] = {
    RECORDED: "Written in a federal record (FEMA NSS, Census ACS, CDC SVI, CMS, FEMA NRI). "
    "Historical: describes what was filed, not necessarily what is true today.",
    UNRECORDED: "The field is blank. Nobody wrote it down. This is never the same as 'no'.",
    VERIFIED_LIVE: "Checked against Google Maps during this session, with a timestamp.",
    MODELED: "A statistical estimate (CDC PLACES), not a count.",
    COUNTY_LEVEL: "Published per county only (HHS emPOWER). Says how many, never which tract.",
    APPROXIMATE: "Derived from a federal record by our own approximation, such as a shelter matched to "
    "its nearest census tract centroid. Describes the neighbourhood, not the building.",
}

YES, NO = "YES", "NO"


def three_state(value: Any) -> str:
    """YES, NO or UNRECORDED. Blank strings, 'UNK' and None are UNRECORDED, never NO."""
    if value is True:
        return YES
    if value is False:
        return NO
    if value is None:
        return UNRECORDED
    normalized = str(value).strip().upper()
    if normalized in ("YES", "TRUE", "Y"):
        return YES
    if normalized in ("NO", "FALSE", "N"):
        return NO
    return UNRECORDED


def claim(text: str, label: str, source: str = "") -> dict[str, str]:
    if label not in LABELS:
        raise ValueError(f"Unknown evidence label {label!r}")
    out = {"text": text, "label": label}
    if source:
        out["source"] = source
    return out


SCHEMA_VERSION = 1


def payload(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    """Tag a tool response so the front end can dispatch on payload_type, not on tool names."""
    return {"payload_type": f"{kind}.v{SCHEMA_VERSION}", "schema_version": SCHEMA_VERSION, **body}


def collect_maps_sources(results: Any) -> list[dict[str, Any]]:
    """Deduplicated Google Maps sources across verification results, each with the shelters it supports."""
    by_key: dict[str, dict[str, Any]] = {}
    for r in results:
        live = r.get("live") or {}
        for src in live.get("sources") or []:
            key = src.get("place_id") or src.get("uri") or src.get("title")
            entry = by_key.setdefault(key, {**src, "supports_shelter_ids": []})
            if r.get("shelter_id") not in entry["supports_shelter_ids"]:
                entry["supports_shelter_ids"].append(r.get("shelter_id"))
    return list(by_key.values())


# Fix severity, so the front end can sort and color without parsing text.
SEVERITY: dict[str, str] = {
    "NO_DESTINATION": "HIGH", "CLOSED": "HIGH", "NOT_FOUND": "HIGH", "PRE_LANDFALL_IN_SURGE_ZONE": "HIGH",
    "NO_WALKABLE_DESTINATION": "HIGH", "GENERATOR_GAP": "HIGH", "CARE_FACILITY_FAR_FROM_GENERATOR": "HIGH",
    "NAME_SAYS_INACTIVE": "HIGH", "SUCCESSOR_AT_ADDRESS": "HIGH", "PRE_LANDFALL_FLOOD_EXPOSED_SITE": "MEDIUM", "OUTREACH_FOR_MUST_LEAVE": "MEDIUM",
    "NO_ASSESSED_ACCESSIBLE_DESTINATION": "MEDIUM", "ACCESSIBILITY_CONFLICT": "MEDIUM",
    "TEMPORARILY_CLOSED": "MEDIUM", "CAPACITY_IMPLAUSIBLE": "MEDIUM",
    "NAME_DIFFERS": "LOW", "UNASSESSED_ACCESSIBILITY": "LOW", "UNCONFIRMED": "LOW",
}


def ref(kind: str, ident: Any, lat: Any = None, lng: Any = None) -> dict[str, Any]:
    """Machine-readable pointer from a fix to the thing it is about: tract, shelter, facility or county."""
    if kind not in ("tract", "shelter", "facility", "county"):
        raise ValueError(f"Unknown ref kind {kind!r}")
    return {"kind": kind, "id": None if ident is None else str(ident), "lat": lat, "lng": lng}


def make_fix(kind: str, subject: str, evidence: str, action: str, label: str,
             target: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"type": kind, "severity": SEVERITY.get(kind, "LOW"), "subject": subject, "evidence": evidence,
            "action": action, "label": label, "ref": target}
