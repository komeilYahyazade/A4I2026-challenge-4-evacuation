"""Wire contract: pydantic models for every payload the front end renders.

contract/README.md is the prose; this is the checkable version. Fixtures in contract/fixtures/
are validated against these models in tests/test_contract.py, and JSON Schema is exported to
contract/schemas/ with `python -m agent.contract`.

Payloads owned by komeil-d2 (brief, verification) are modelled loosely: the keys the UI depends on
are required, the rest pass through. Payloads owned by the agent lane are modelled fully.
"""

import json
import sys
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.6"
SCHEMA_VERSION = 1

ThreeState = Literal["YES", "NO", "UNRECORDED"]
Label = Literal["RECORDED", "UNRECORDED", "VERIFIED LIVE", "MODELED", "COUNTY-LEVEL", "APPROXIMATE"]
Severity = Literal["HIGH", "MEDIUM", "LOW"]


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Payload(_Open):
    payload_type: str
    schema_version: int = SCHEMA_VERSION


class LatLng(_Closed):
    lat: Optional[float]
    lng: Optional[float]


class MapsSource(_Open):
    title: str
    uri: str
    place_id: Optional[str] = None


class Ref(_Closed):
    kind: Literal["tract", "shelter", "facility", "county"]
    id: str
    lat: Optional[float] = None
    lng: Optional[float] = None


class Fix(_Open):
    """fix_before_season[] row; allocation gaps are a superset (severity, ref)."""

    type: str
    subject: str
    evidence: str
    action: str
    label: Label
    severity: Optional[Severity] = None
    ref: Optional[Ref] = None


class CouldNotTell(_Open):
    item: str
    label: Label
    spoken_to: Optional[int] = None
    unassessed: Optional[int] = None
    note: Optional[str] = None


# ---------------------------------------------------------------- allocation.v1 (agent lane)


class ExcludedShelter(_Closed):
    shelter_id: str
    name: Optional[str]
    reason: Literal["DOWN", "NOT_PRE_LANDFALL", "NAME_SAYS_INACTIVE", "CAPACITY_UNRECORDED", "CAPACITY_IMPLAUSIBLE",
                    "IN_SURGE_ZONE", "DUPLICATE_RECORD"]


class AllocationParams(_Closed):
    max_km: float
    shelter_seeking_share: float
    shelters_down: list[str]
    excluded_shelters: list[ExcludedShelter]
    exclude_surge: bool
    capacity_rule: str
    demand_rule: str
    cost_rule: str
    distance_basis: str


class AllocationTotals(_Closed):
    demand_persons: int
    assigned_persons: int
    unassigned_persons: int
    usable_capacity: int
    shelters_used: int


class AllocTract(_Closed):
    geo_id: str
    lat: Optional[float]
    lng: Optional[float]
    households_no_vehicle: int
    persons_per_household: Optional[float]
    demand_persons: int
    assigned_persons: int
    unassigned_persons: int


class AllocShelter(_Closed):
    shelter_id: str
    name: Optional[str]
    lat: Optional[float]
    lng: Optional[float]
    county_fips: Optional[str]
    capacity: Optional[int]
    capacity_status: Literal["RECORDED", "UNRECORDED"]
    capacity_implausible: bool
    is_pre_landfall: bool
    wheelchair: ThreeState
    generator: ThreeState
    surge: ThreeState
    is_medical: bool
    name_says_inactive: bool
    excluded_reason: Optional[str]
    assigned_persons: int
    utilization: Optional[float]


class Assignment(_Closed):
    geo_id: str
    shelter_id: str
    persons: int
    straight_line_km: float
    drive_minutes: Optional[float]
    distance_basis: str
    mode: Literal["WALK", "PARATRANSIT"]
    from_: LatLng = Field(alias="from")
    to: LatLng

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Unassigned(_Closed):
    geo_id: str
    persons: int
    reason: Literal["NO_SHELTER_WITHIN_MAX_KM", "CAPACITY_EXHAUSTED"]
    lat: Optional[float]
    lng: Optional[float]


class Gap(Fix):
    type: Literal["UNASSIGNED_DEMAND", "ASSIGNED_SHELTER_IN_SURGE_ZONE", "ASSESS_ASSIGNED_SHELTER",
                  "CAPACITY_UNRECORDED_EXCLUDED", "CAPACITY_IMPLAUSIBLE_EXCLUDED"]
    severity: Severity
    ref: Ref
    persons_affected: Optional[int] = None
    households_affected: Optional[int] = None


class SiteOption(Fix):
    type: Literal["DESIGNATE_SITE_NEAR"]
    severity: Severity
    ref: Ref
    persons_covered: int
    seats_needed: int
    tracts_covered: list[str]


class FullRef(_Closed):
    method: Literal["POST", "GET"]
    path: str
    body: dict[str, Any]


class AllocationV1(Payload):
    """compact=true is the chat-tool view: tracts/shelters/assignments are empty and gaps are the top
    ones by severity; fetch `full` for the map layers."""

    payload_type: Literal["allocation.v1"]
    compact: bool = False
    full: Optional[FullRef] = None
    gaps_total: Optional[int] = None
    gap_counts: Optional[dict[str, int]] = None
    excluded_counts: Optional[dict[str, int]] = None
    county_fips: str
    generated_at: str
    solver: Literal["ortools.SimpleMinCostFlow"]
    status: Literal["OPTIMAL", "NO_DEMAND", "SOLVER_ERROR"]
    params: AllocationParams
    totals: AllocationTotals
    tracts: list[AllocTract]
    shelters: list[AllocShelter]
    assignments: list[Assignment]
    unassigned: list[Unassigned]
    gaps: list[Gap]
    could_not_tell_you: list[CouldNotTell]
    site_options: list[SiteOption] = []
    maps_used: Literal[False]


# ---------------------------------------------------------------- rehearsal.v1 / storm_track.v1


class TrackPoint(_Open):
    lat: float
    lng: float
    valid_at: Optional[str] = None
    wind_kt: Optional[float] = None


class Scenario(_Open):
    id: str
    name: str
    storm_id: str
    landfall_at: str
    landfall: LatLng
    advisory: Optional[str] = None
    issued_at: Optional[str] = None
    cone: Optional[dict[str, Any]] = Field(None, description="GeoJSON Geometry (Polygon|MultiPolygon), [lng, lat]")
    source: str


class ShelterDown(_Closed):
    shelter_id: str
    name: Optional[str]
    lat: Optional[float]
    lng: Optional[float]
    reason: Literal["IN_SURGE_ZONE", "IN_CONE_AND_SURGE", "COASTAL_FLOOD_TRACT", "REPORTED_CLOSED", "MANUAL"]
    rule: str
    in_plan: bool
    note: Optional[str] = None


class Reassigned(_Closed):
    geo_id: str
    from_shelter_id: str
    to_shelter_id: str
    persons: int
    from_: Optional[LatLng] = Field(alias="from")
    to_old: Optional[LatLng]
    to_new: Optional[LatLng]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NewlyUnassigned(_Closed):
    geo_id: str
    persons: int
    lat: Optional[float]
    lng: Optional[float]


class RehearsalDiff(_Closed):
    reassigned: list[Reassigned]
    reassigned_total: Optional[int] = None
    newly_unassigned: list[NewlyUnassigned]
    unassigned_before: int
    unassigned_after: int
    capacity_lost: int


class RehearsalV1(Payload):
    payload_type: Literal["rehearsal.v1"]
    compact: bool = False
    full: Optional[FullRef] = None
    county_fips: str
    generated_at: str
    scenario: Scenario
    shelters_down: list[ShelterDown]
    at_risk_not_in_plan: list[ShelterDown]
    before: AllocationV1
    after: AllocationV1
    diff: RehearsalDiff
    maps_used: Literal[False]
    note: str


class Advisory(_Open):
    advisory: str
    issued_at: Optional[str]
    hours_before_landfall: Optional[float] = None
    cone: Optional[dict[str, Any]]
    track: list[TrackPoint]


class StormTrackV1(Payload):
    payload_type: Literal["storm_track.v1"]
    storm_id: str
    name: str
    landfall_at: str
    landfall: LatLng
    source: str
    advisories: list[Advisory]
    best_track: list[TrackPoint] = []


# ---------------------------------------------------------------- county_map.v1 (agent lane)


class County(_Open):
    fips: str
    name: Optional[str]
    lat: Optional[float] = None
    lng: Optional[float] = None


class MapTract(_Open):
    geo_id: str
    lat: Optional[float]
    lng: Optional[float]
    households_no_vehicle: Optional[int]


class MapShelter(_Open):
    shelter_id: str
    name: Optional[str]
    lat: Optional[float]
    lng: Optional[float]
    usage: Optional[str]
    is_pre_landfall: bool
    capacity: Optional[int]
    wheelchair: ThreeState
    generator: ThreeState
    surge: ThreeState
    is_medical: bool


class MapCareFacility(_Open):
    facility_id: str
    facility_name: Optional[str] = None
    city: Optional[str] = None
    certified_beds: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    km_to_generator_recorded_shelter: Optional[float] = None
    km_to_medical_generator_recorded_shelter: Optional[float] = None


class CountyMapV1(Payload):
    payload_type: Literal["county_map.v1"]
    county_fips: str
    county: County
    tracts: list[MapTract]
    shelters: list[MapShelter]
    care_facilities: list[MapCareFacility] = []


# ---------------------------------------------------------------- komeil-d2 payloads (loose)


class Live(_Open):
    label: Label
    verified_at: Optional[str] = None
    sources: list[MapsSource]
    status: Optional[str] = None


class VerificationRow(_Open):
    shelter_id: str
    record: dict[str, Any]
    live: Optional[Live]
    accessibility: Optional[dict[str, Any]]
    verdict: Literal["KEEP", "DROP", "KEEP_UNVERIFIED", "NOT_VERIFIED_THIS_RUN"]
    why: str
    flags: list[str] = []


class ShelterVerificationV1(Payload):
    payload_type: Literal["shelter_verification.v1"]
    verified_at: str
    results: list[VerificationRow]
    grounding_ledger: dict[str, Any]


class WhoCannotLeave(_Open):
    rank: int
    geo_id: str
    reasons: list[dict[str, Any]]
    mobility: dict[str, Any]


class WhereTheyWouldGo(_Open):
    for_tract: str
    shelter_id: str
    record: dict[str, Any]
    live: Optional[Live]
    verdict: Literal["KEEP", "DROP", "KEEP_UNVERIFIED", "NOT_VERIFIED_THIS_RUN"]
    why: str


class ReadinessBriefV1(Payload):
    payload_type: Literal["readiness_brief.v1"]
    county: County
    generated_at: str
    assumptions: dict[str, Any]
    county_shelters: dict[str, Any]
    power_dependence: Optional[dict[str, Any]]
    who_cannot_leave: list[WhoCannotLeave]
    where_they_would_go: list[WhereTheyWouldGo]
    fix_before_season: list[Fix]
    could_not_tell_you: list[CouldNotTell]
    grounding_ledger: dict[str, Any]
    maps_sources: list[MapsSource]


PAYLOAD_MODELS: dict[str, type[Payload]] = {
    "allocation.v1": AllocationV1,
    "rehearsal.v1": RehearsalV1,
    "storm_track.v1": StormTrackV1,
    "county_map.v1": CountyMapV1,
    "shelter_verification.v1": ShelterVerificationV1,
    "readiness_brief.v1": ReadinessBriefV1,
}


def validate(obj: dict[str, Any]) -> Payload:
    """Validate a payload by its payload_type. Raises KeyError for an unknown type."""
    return PAYLOAD_MODELS[obj["payload_type"]].model_validate(obj)


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in PAYLOAD_MODELS.items():
        path = out_dir / f"{name}.schema.json"
        schema = model.model_json_schema(by_alias=True)
        schema["$comment"] = f"contract {CONTRACT_VERSION}; generated by `python -m agent.contract`"
        path.write_text(json.dumps(schema, indent=2) + "\n")
        written.append(path)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "contract" / "schemas"
    for p in export_schemas(target):
        print("wrote", p)
