"""The v1 API contract (BUILD_SPEC.md section 10). Pydantic models, enums as Literal types.

This file is THE contract between the API, the agents and the front end. After v0, changes are
additive only (new optional fields), with mocks in contract/mocks/ updated in the same PR.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- enums ------------------------------------------------------------------------------------

Flag = Literal["yes", "no", "unrecorded"]
Freshness = Literal["historical", "live"]
SourceKind = Literal[
    "fema_nss", "acs", "cdc_svi", "cdc_places", "fema_nri", "cms", "hhs_empower", "google_maps"
]
Mode = Literal["mock", "live"]
VerificationStatus = Literal["confirmed", "renamed", "closed", "not_found", "unclear", "error"]
MapsWheelchair = Literal["yes", "no", "no_answer"]
Agreement = Literal["agree", "disagree", "record_blank", "maps_blank", "both_blank"]
Verdict = Literal["holds_up", "verify_before_season", "do_not_rely"]
FixCategory = Literal[
    "no_accessible_destination",
    "unassessed_access",
    "generator_gap",
    "shelter_in_surge_zone",
    "record_mismatch",
    "medical_needs_gap",
    "seat_gap",  # additive v0.3: people the capacity plan leaves without a seat within reach
]
Track = Literal["shelter_readiness", "getting_people_out", "medically_dependent"]
Step = Literal["rank_tracts", "find_shelters", "verify_shelters", "find_gaps", "write_brief", "complete"]
JobStatus = Literal["running", "complete", "failed"]
WhoKind = Literal["tract", "care_facility"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- errors -----------------------------------------------------------------------------------


class ErrorDetail(_Model):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(_Model):
    error: ErrorDetail


# ---- attribution pair -------------------------------------------------------------------------


class Source(_Model):
    id: str  # src_1, src_2... unique within one response
    provider: Literal["Google Maps"] = "Google Maps"
    title: str | None = None
    uri: str | None = None
    place_id: str | None = None


class Segment(_Model):
    text: str
    source_ids: list[str]
    freshness: Freshness


# ---- shared objects ---------------------------------------------------------------------------


class LatLon(_Model):
    lat: float
    lon: float


class Shelter(_Model):
    shelter_id: str
    name: str
    address: str
    county_fips: str | None
    lat: float
    lon: float
    nearest_geo_id: str | None = None
    usage: str | None = None  # FEMA facility usage code (EVAC / POST) or null
    evacuation_capacity: int | None = None  # null when FEMA recorded a negative number
    post_impact_capacity: int | None = None
    wheelchair_accessible: Flag
    generator_onsite: Flag
    in_surge_slosh_area: Flag
    in_100_yr_floodplain: Flag
    is_medical: bool
    place_id: str | None = None  # null until verified once
    source: Literal["fema_nss"] = "fema_nss"
    freshness: Literal["historical"] = "historical"


class WheelchairCheck(_Model):
    maps: MapsWheelchair
    record: Flag
    agreement: Agreement


class Drive(_Model):
    from_geo_id: str | None = None
    drive_minutes: float | None = None
    drive_km: float | None = None


class ShelterVerification(_Model):
    shelter_id: str
    checked_at: datetime
    status: VerificationStatus
    maps_name: str | None = None
    record_name: str
    place_type: str | None = None
    open_now: bool | None = None  # business hours, not "opened as a shelter"
    wheelchair: WheelchairCheck
    drive: Drive | None = None
    summary: list[Segment] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)


class ShelterDetail(Shelter):
    pet_accommodations_code: str | None = None
    population_code: str | None = None
    shelter_status_code: str | None = None
    incident_id: str | None = None
    last_verification: ShelterVerification | None = None


# ---- routes: health, counties, map ------------------------------------------------------------


class Health(_Model):
    status: Literal["ok"] = "ok"
    mode: Mode
    model: str
    state: str
    dataset: str
    version: str


class FlagCounts(_Model):
    yes: int
    no: int
    unrecorded: int


class ShelterCounts(_Model):
    total: int
    medical_needs: int
    in_surge_zone: int
    wheelchair: FlagCounts


class County(_Model):
    county_fips: str
    name: str
    rank: int
    need_score: float | None
    exposure_score: float | None
    total_pop: int | None
    shelters: ShelterCounts
    center: LatLon | None


class CountiesResponse(_Model):
    state: str
    counties: list[County]


class Tract(_Model):
    geo_id: str
    lat: float
    lon: float
    total_pop: int | None = None
    households_no_vehicle: float | None = None
    pct_no_vehicle: float | None = None
    pop_65_plus: float | None = None
    pct_age_65_plus: float | None = None
    pct_disability: float | None = None
    mobile_homes: float | None = None
    group_quarters_pop: float | None = None
    need_score: float | None = None
    nearest_shelter_km: float | None = None  # straight line
    nearest_wheelchair_yes_km: float | None = None  # straight line, recorded-yes only
    # additive (v0.2): exposure and modeled mobility for the insights views
    households: float | None = None
    exposure_score: float | None = None  # min-max of NRI HRCN_EALP + CFLD_EALP within the state
    pct_mobility: float | None = None  # CDC PLACES, modeled prevalence, not a count


class CareFacility(_Model):
    ccn: str
    name: str
    lat: float
    lon: float
    certified_beds: int | None = None
    nearest_generator_yes_km: float | None = None


class PowerDependence(_Model):
    county_fips: str
    power_dependent_dme: int | None
    is_censored: bool  # True when emPOWER shows 11, meaning 1 to 10
    scope: Literal["county"] = "county"


class CountyMap(_Model):
    county_fips: str
    center: LatLon | None
    tracts: list[Tract]
    shelters: list[Shelter]
    care_facilities: list[CareFacility]
    power_dependence: PowerDependence | None


# ---- routes: insights (additive, v0.2) ----------------------------------------------------------


class FlagRow(_Model):
    scope: Literal["all", "evac"]
    flag: Literal["wheelchair_accessible", "generator_onsite", "self_sufficient_electricity",
                  "in_surge_slosh_area", "in_100_yr_floodplain"]
    yes: int
    no: int
    unrecorded: int


class AccessStats(_Model):
    """Straight-line km from tract centroids. Wheelchair-yes counts only recorded yes."""
    tracts: int
    median_km_any: float | None
    median_km_wheelchair_yes: float | None
    people_over_10km_any: int | None
    households_no_vehicle: int | None
    households_no_vehicle_within_reach_any: int | None
    households_no_vehicle_within_reach_wheelchair_yes: int | None
    tracts_no_wheelchair_yes_within_reach: int


class Baseline(_Model):
    """Population-weighted. pct_mobility is CDC PLACES modeled prevalence."""
    pct_no_vehicle: float | None
    pct_age_65_plus: float | None
    pct_disability: float | None
    pct_mobile_homes: float | None
    poverty_rate: float | None
    pct_mobility: float | None


class ShelterSummary(_Model):
    total: int
    evac: int
    medical_needs: int
    capacity_recorded: int | None
    capacity_unrecorded: int
    capacity_implausible: int
    verified_on_maps: int  # shelters with a stored place_id (place_id is all we keep)


class CareSummary(_Model):
    facilities: int
    certified_beds: int | None
    far_from_generator: int
    certified_beds_far_from_generator: int | None
    threshold_km: float


class PowerSummary(_Model):
    power_dependent_dme: int | None
    is_censored: bool
    medical_capacity: int | None
    scope: Literal["county"] = "county"


class CountyInsights(_Model):
    county_fips: str
    name: str
    reachable_km: float
    shelters: ShelterSummary
    flags: list[FlagRow]
    access: dict[Literal["county", "state"], AccessStats]
    baseline: dict[Literal["county", "state"], Baseline]
    care: CareSummary
    power: PowerSummary
    freshness: Literal["historical"] = "historical"
    sources: list[SourceKind]


# ---- routes: allocation, Ian rehearsal, storm track (additive, v0.3) ----------------------------
# Never Google Maps: the allocator also runs in the rehearsal, where grounding is not used.


class AllocTotals(_Model):
    demand_persons: int
    assigned_persons: int
    unassigned_persons: int
    usable_capacity: int
    shelters_used: int


class AllocTract(_Model):
    geo_id: str
    lat: float | None
    lon: float | None
    households_no_vehicle: int
    persons_per_household: float | None
    demand_persons: int
    assigned_persons: int
    unassigned_persons: int


class AllocShelter(_Model):
    shelter_id: str
    name: str
    lat: float | None
    lon: float | None
    county_fips: str | None
    capacity: int | None
    capacity_implausible: bool
    wheelchair_accessible: Flag
    generator_onsite: Flag
    in_surge_slosh_area: Flag
    is_medical: bool
    excluded_reason: Literal["DOWN", "NAME_SAYS_INACTIVE", "DUPLICATE_RECORD", "CAPACITY_UNRECORDED",
                             "CAPACITY_IMPLAUSIBLE"] | None
    assigned_persons: int
    utilization: float | None


class Assignment(_Model):
    geo_id: str
    shelter_id: str
    persons: int
    straight_line_km: float
    mode: Literal["WALK", "PARATRANSIT"]


class Unplaced(_Model):
    geo_id: str
    persons: int
    lat: float | None
    lon: float | None
    reason: Literal["CAPACITY_EXHAUSTED", "NO_SHELTER_WITHIN_MAX_KM"]


class SiteOption(_Model):
    geo_id: str
    lat: float | None
    lon: float | None
    persons_covered: int
    tracts_covered: list[str]


class Allocation(_Model):
    county_fips: str
    status: Literal["OPTIMAL", "NO_DEMAND", "SOLVER_ERROR"]
    max_km: float
    shelter_seeking_share: float
    totals: AllocTotals
    tracts: list[AllocTract]
    shelters: list[AllocShelter]
    assignments: list[Assignment]
    unassigned: list[Unplaced]
    site_options: list[SiteOption]
    excluded_counts: dict[str, int]
    rules: dict[str, str]
    maps_used: Literal[False] = False
    freshness: Literal["historical"] = "historical"


class DownShelter(_Model):
    shelter_id: str
    name: str
    lat: float | None
    lon: float | None
    reason: Literal["IN_SURGE_ZONE", "IN_CONE_AND_FLOOD_EXPOSED"]
    in_plan: bool
    capacity: int | None
    evidence: str


class Reassignment(_Model):
    geo_id: str
    from_shelter_id: str
    to_shelter_id: str
    persons: int
    tract_lat: float | None
    tract_lon: float | None
    to_lat: float | None
    to_lon: float | None
    from_lat: float | None
    from_lon: float | None


class Scenario(_Model):
    id: str
    name: str
    storm_id: str
    landfall_at: str
    source: str
    advisory: str
    issued_at: str | None
    hours_before_landfall: float | None


class Rehearsal(_Model):
    county_fips: str
    scenario: Scenario
    at_risk_total: int
    shelters_down: list[DownShelter]
    at_risk_not_in_plan: list[DownShelter]
    before: AllocTotals
    after: AllocTotals
    capacity_lost: int
    reassigned: list[Reassignment]
    reassigned_total: int
    newly_unassigned: list[Unplaced]
    cone: dict | None  # GeoJSON polygon, NOAA NHC (public domain)
    maps_used: Literal[False] = False
    note: str
    freshness: Literal["historical"] = "historical"


class TrackPoint(_Model):
    lat: float
    lon: float
    valid_at: str | None = None
    wind_kt: int | None = None


class StormAdvisory(_Model):
    advisory: str
    issued_at: str | None
    hours_before_landfall: float | None
    cone: dict | None
    track: list[TrackPoint]


class StormTrack(_Model):
    storm_id: str
    name: str
    landfall_at: str
    landfall: LatLon
    source: str
    advisories: list[StormAdvisory]
    best_track: list[TrackPoint]


# ---- routes: verify ---------------------------------------------------------------------------


class VerifyRequest(_Model):
    from_geo_id: str | None = None
    include_route: bool = True


# ---- routes: briefs ---------------------------------------------------------------------------


class BriefOptions(_Model):
    reachable_km_no_vehicle: float = 3.0
    max_shelters_to_verify: int = 12
    include_routes: bool = True


class BriefRequest(_Model):
    county_fips: str
    track: Track = "shelter_readiness"
    options: BriefOptions = Field(default_factory=BriefOptions)


class BriefAccepted(_Model):
    brief_id: str
    status: Literal["running"] = "running"
    poll_url: str


class Progress(_Model):
    step: Step
    message: str
    done: int = 0
    total: int = 0


class CountyRef(_Model):
    county_fips: str
    name: str
    state: str


class Assumptions(_Model):
    reachable_km_no_vehicle: float
    transport_measure: str
    distance: str
    shelter_to_tract: str
    need_score: str | None = None


class Driver(_Model):
    metric: str
    value: float | None
    unit: str
    source: SourceKind


class WhoCannotLeave(_Model):
    rank: int
    kind: WhoKind
    id: str
    label: str
    lat: float
    lon: float
    people: int | None
    drivers: list[Driver]
    reason: str


class BriefPower(_Model):
    power_dependent_dme: int | None
    is_censored: bool
    caveat: str


class Destination(_Model):
    shelter: Shelter
    serves: list[str]
    straight_line_km: float | None
    verification: ShelterVerification | None
    verdict: Verdict
    verdict_reason: list[Segment]


class Affected(_Model):
    tracts: list[str]
    shelters: list[str]
    care_facilities: list[str]


class Fix(_Model):
    priority: int
    category: FixCategory
    title: str
    recommendation: str
    affected: Affected
    source_ids: list[str]


class Unknown(_Model):
    topic: str
    count: int | None
    of: int | None
    unit: str
    why: str
    how_to_close: str


class Brief(_Model):
    county: CountyRef
    track: Track
    generated_at: datetime
    model: str
    headline: str
    assumptions: Assumptions
    who_cannot_leave: list[WhoCannotLeave]
    power_dependence: BriefPower | None
    destinations: list[Destination]
    fixes: list[Fix]
    unknowns: list[Unknown]
    sources: list[Source]


class BriefStatus(_Model):
    brief_id: str
    status: JobStatus
    progress: Progress
    brief: Brief | None = None
    error: ErrorDetail | None = None


# ---- routes: audit ----------------------------------------------------------------------------


class AuditRow(_Model):
    metric: str
    unit: str
    recommended: float | None
    county: float | None
    state: float | None
    used_as_input: bool


class AuditResponse(_Model):
    brief_id: str
    compares: str
    rows: list[AuditRow]
    finding: str | None = None  # written by a person, so may be null


# ---- routes: chat -----------------------------------------------------------------------------


class ChatRequest(_Model):
    session_id: str | None = None
    county_fips: str | None = None
    brief_id: str | None = None
    message: str


class ToolCall(_Model):
    name: str
    duration_ms: int


class ChatResponse(_Model):
    session_id: str
    reply: list[Segment]
    sources: list[Source]
    highlight: Affected
    tool_calls: list[ToolCall]
    refused: bool


# ---- writer output_schema ---------------------------------------------------------------------
# No defaults here on purpose: this becomes a Gemini response schema, and every field must be
# produced by the writer. The writer refers to facts by id only; the API fills in every number.


class JudgmentSegment(BaseModel):
    text: str
    source_ids: list[str]
    freshness: Freshness


class TractReason(BaseModel):
    id: str  # geo_id for a tract, ccn for a care facility
    reason: str


class DestinationVerdict(BaseModel):
    shelter_id: str
    verdict: Verdict
    verdict_reason: list[JudgmentSegment]


class JudgmentAffected(BaseModel):
    tracts: list[str]
    shelters: list[str]
    care_facilities: list[str]


class FixJudgment(BaseModel):
    priority: int
    category: FixCategory
    title: str
    recommendation: str
    affected: JudgmentAffected
    source_ids: list[str]


class UnknownText(BaseModel):
    topic: str
    why: str
    how_to_close: str


class BriefJudgments(BaseModel):
    headline: str
    tract_reasons: list[TractReason]
    destination_verdicts: list[DestinationVerdict]
    fixes: list[FixJudgment]
    unknowns_text: list[UnknownText]
