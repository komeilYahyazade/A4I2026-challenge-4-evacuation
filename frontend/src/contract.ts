// Wire types for contract v0.1 (contract/README.md). Hand-written; tolerant of the pending
// v0.1 additions (coordinates, flags, refs), which may be absent until komeil-d2 ships them.

export type ThreeState = "YES" | "NO" | "UNRECORDED";
export type EvidenceLabel = "RECORDED" | "UNRECORDED" | "VERIFIED LIVE" | "MODELED" | "COUNTY-LEVEL" | "APPROXIMATE";

/** Shelter matched to its nearest tract centroid; FEMA NRI exposure of that tract. Always APPROXIMATE. */
export interface SiteHazard {
  label?: EvidenceLabel;
  coastal_flood_exposed_share?: number | null;
  coastal_flood_annual_frequency?: number | null;
  hurricane_annual_frequency?: number | null;
  nearest_geo_id?: string | null;
  km_to_centroid?: number | null;
  method?: string;
  note?: string;
}
export type Severity = "HIGH" | "MEDIUM" | "LOW";

export interface LatLng {
  lat: number;
  lng: number;
}

export interface Envelope {
  _fixture?: unknown;
  _note?: string;
  payload_type: string;
  schema_version: number;
  county_fips?: string;
  generated_at?: string;
}

export interface MapsSource {
  title?: string | null;
  uri?: string | null;
  place_id?: string | null;
  supports_shelter_ids?: string[];
}

export interface Reason {
  text: string;
  label: EvidenceLabel;
  source?: string;
}

export interface Mobility {
  households_no_vehicle?: number;
  share_no_vehicle?: number;
  needs_pickup_plan?: boolean;
  straight_line_km?: number | null;
  drive_minutes?: number | null;
  distance_basis?: string | null;
  walkable?: boolean;
  paratransit_reachable?: boolean;
  car_reachable?: boolean;
  status?: string;
  recommendation?: string;
}

export interface TractEntry {
  rank: number;
  geo_id: string;
  priority_score?: number;
  lat?: number;
  lng?: number;
  lon?: number;
  people?: {
    total_pop?: number;
    households_no_vehicle?: number;
    pop_65_plus?: number;
    pct_disability?: number;
    mobile_homes?: number;
  };
  reach?: {
    km_any_shelter?: number | null;
    km_pre_landfall_shelter?: number | null;
    km_pre_landfall_wheelchair_recorded_yes?: number | null;
    shelters_within_walk?: number;
    pre_landfall_within_run?: number;
    within_run_wheelchair?: { yes: number; no: number; unrecorded: number };
  };
  hazard?: { hurricane_exposed_pop?: number | null; coastal_flood_exposed_pop?: number | null };
  reasons?: Reason[];
  mobility?: Mobility;
}

export interface ShelterRecord {
  label?: EvidenceLabel;
  source?: string;
  capacity?: number | null;
  evacuation_capacity?: number | null;
  capacity_status?: "RECORDED" | "UNRECORDED";
  capacity_as_filed?: number | null;
  capacity_implausible?: boolean;
  usage?: string;
  wheelchair?: ThreeState;
  generator?: ThreeState;
  surge?: ThreeState;
  is_medical?: boolean;
  site_hazard?: SiteHazard | null;
  name_says_inactive?: boolean;
}

export type Tri = boolean | "unknown";

export interface LiveCheck {
  label: EvidenceLabel;
  verified_at?: string;
  sources: MapsSource[];
  exists: Tri;
  current_name?: string | null;
  place_type?: string | null;
  closed?: "no" | "permanently" | "temporarily" | "unknown";
  wheelchair_entrance?: "yes" | "no" | "unknown";
  drive_minutes?: number | null;
  road_km?: number | null;
  drive_basis?: string | null;
  notes?: string | null;
  status?: "VERIFIED" | "NO_MAPS_EVIDENCE" | "UNPARSEABLE" | "ERROR" | string;
}

export type Verdict = "KEEP" | "DROP" | "KEEP_UNVERIFIED" | "NOT_VERIFIED_THIS_RUN";

export interface WhereRow {
  for_tract?: string;
  shelter_id: string;
  name?: string;
  shelter_name?: string;
  address?: string;
  straight_line_km?: number | null;
  lat?: number;
  lng?: number;
  record?: ShelterRecord;
  live: LiveCheck | null;
  accessibility: { label: EvidenceLabel; summary: string } | null;
  verdict: Verdict;
  why?: string;
  flags?: string[];
}

export interface Ref {
  kind: "tract" | "shelter" | "facility" | "county";
  id: string;
  lat?: number;
  lng?: number;
}

export interface FixItem {
  type: string;
  subject: string;
  evidence: string;
  action: string;
  label: EvidenceLabel;
  ref?: Ref;
  severity?: Severity;
  households_affected?: number;
  persons_affected?: number;
}

export interface CannotTell {
  item: string;
  spoken_to?: number;
  unassessed?: number;
  note?: string;
  label: EvidenceLabel;
}

export interface TriCount {
  yes: number;
  no: number;
  unrecorded: number;
}

export interface CountyShelters {
  shelters?: number;
  pre_landfall?: number;
  post_only_or_other?: number;
  usage_unrecorded?: number;
  pre_landfall_capacity_plausible?: number;
  pre_landfall_capacity_recorded?: number;
  pre_landfall_capacity_unrecorded?: number;
  pre_landfall_flood_exposed_approx?: number;
  pre_landfall_name_says_inactive?: number;
  capacity_implausible?: number;
  medical_needs?: number;
  medical_needs_generator_yes?: number;
  pre_landfall_wheelchair?: TriCount;
  pre_landfall_generator?: TriCount;
  pre_landfall_surge?: TriCount;
}

export interface PowerDependence {
  county_fips?: string;
  power_dependent_dme?: number | null;
  power_dependent_is_censored?: boolean;
  label?: EvidenceLabel;
  note?: string;
}

export interface Fix extends FixItem {
  cost_usd?: number;
  persons_covered?: number;
}

export interface ReadinessBrief extends Envelope {
  county: { fips: string; name: string; lat?: number; lng?: number };
  assumptions?: Record<string, unknown>;
  county_shelters?: CountyShelters;
  power_dependence?: PowerDependence | null;
  who_cannot_leave: TractEntry[];
  /** Ranked by mobile homes, 65+ and estimated disability counts: people who must leave and may need help. */
  must_leave_may_need_help?: TractEntry[];
  where_they_would_go: WhereRow[];
  fix_before_season: FixItem[];
  could_not_tell_you: CannotTell[];
  grounding_ledger?: Record<string, unknown>;
  maps_sources: MapsSource[];
  evidence_labels?: Record<string, string>;
  // Could: Allocator part 2
  fixes?: Fix[];
  budget?: { total_usd: number; spent_usd?: number };
  _note?: string;
}

export interface ShelterVerification extends Envelope {
  verified_at?: string;
  results: WhereRow[];
  not_in_registry?: string[];
  skipped_over_limit?: string[];
  grounding_ledger?: Record<string, unknown>;
  display_rule?: string;
}

export interface MapTract {
  geo_id: string;
  lat: number;
  lng: number;
  total_pop?: number;
  households?: number;
  households_no_vehicle?: number;
  share_no_vehicle?: number;
  est_persons_no_vehicle?: number;
}

export interface MapShelter {
  shelter_id: string;
  name: string;
  address?: string | null;
  lat: number;
  lng: number;
  usage?: string;
  is_pre_landfall?: boolean;
  capacity?: number | null;
  capacity_implausible?: boolean;
  capacity_status?: "RECORDED" | "UNRECORDED";
  wheelchair: ThreeState;
  generator: ThreeState;
  surge: ThreeState;
  is_medical?: boolean;
}

export interface MapCareFacility {
  facility_id: string;
  facility_name?: string | null;
  city?: string | null;
  certified_beds?: number | null;
  latitude?: number | null;
  longitude?: number | null;
  km_to_generator_recorded_shelter?: number | null;
  km_to_medical_generator_recorded_shelter?: number | null;
}

export interface CountyMap extends Envelope {
  county: { fips: string; name: string; lat: number; lng: number };
  tracts: MapTract[];
  shelters: MapShelter[];
  care_facilities?: MapCareFacility[];
}

export interface Assignment {
  geo_id: string;
  shelter_id: string;
  persons: number;
  straight_line_km?: number | null;
  drive_minutes?: number | null;
  distance_basis?: string | null;
  mode: "WALK" | "PARATRANSIT" | "LONG_RUN" | string;
  from?: LatLng;
  to?: LatLng;
}

export interface AllocTract {
  geo_id: string;
  lat: number;
  lng: number;
  households_no_vehicle?: number;
  persons_per_household?: number;
  demand_persons?: number;
  assigned_persons?: number;
  unassigned_persons?: number;
}

export interface AllocShelter {
  shelter_id: string;
  name: string;
  lat: number;
  lng: number;
  county_fips?: string;
  capacity?: number | null;
  capacity_status?: "RECORDED" | "UNRECORDED";
  capacity_implausible?: boolean;
  is_pre_landfall?: boolean;
  assigned_persons?: number;
  utilization?: number;
  wheelchair?: ThreeState;
  generator?: ThreeState;
  surge?: ThreeState;
  is_medical?: boolean;
  excluded_reason?: null | "CAPACITY_IMPLAUSIBLE" | "DUPLICATE_RECORD" | "DOWN" | string;
}

export interface OperatorAssessment {
  shelter_id: string;
  shelter_name: string;
  county?: string;
  operator_name?: string;
  operator_statement?: string;
  audit_status: string;
  wheelchair_status: ThreeState;
  generator_status: ThreeState;
  detected_barriers?: string[];
  detected_power_gaps?: string[];
  follow_up_question?: string;
  summary?: string;
  timestamp_utc?: string;
}

export interface Allocation extends Envelope {
  solver?: string;
  status: string;
  params?: Record<string, unknown>;
  totals?: {
    demand_persons?: number;
    assigned_persons?: number;
    unassigned_persons?: number;
    usable_capacity?: number;
    shelters_used?: number;
  };
  tracts?: AllocTract[];
  shelters?: AllocShelter[];
  assignments: Assignment[];
  unassigned: { geo_id: string; persons: number; reason?: string; lat?: number; lng?: number }[];
  gaps?: FixItem[];
  could_not_tell_you?: CannotTell[];
  maps_used?: boolean;
  site_options?: SiteOption[];
}

/** v1.2: where one designated site would seat the most unplaced people (Allocator part 2). */
export interface SiteOption extends FixItem {
  persons_covered?: number;
  seats_needed?: number;
  tracts_covered?: string[];
}

export type Geometry =
  | { type: "Polygon"; coordinates: number[][][] }
  | { type: "MultiPolygon"; coordinates: number[][][][] };

export interface TrackPoint {
  lat: number;
  lng: number;
  valid_at?: string;
  tau_h?: number;
  wind_kt?: number;
  mslp_mb?: number;
  category?: string;
}

export interface Rehearsal extends Envelope {
  scenario: {
    id: string;
    name?: string;
    storm_id?: string;
    advisory?: string | number | null;
    issued_at?: string | null;
    landfall_at?: string;
    landfall?: LatLng;
    cone?: Geometry | null;
    source?: string;
  };
  shelters_down: {
    shelter_id: string;
    name?: string;
    lat?: number;
    lng?: number;
    reason: string;
    rule?: string;
    evidence?: string;
    in_plan?: boolean;
  }[];
  at_risk_not_in_plan?: { shelter_id: string; name?: string; lat?: number; lng?: number; reason?: string; rule?: string }[];
  before: Allocation;
  after: Allocation;
  diff: {
    reassigned: {
      geo_id: string;
      from_shelter_id: string;
      to_shelter_id: string;
      persons?: number;
      from?: LatLng;
      to_old?: LatLng;
      to_new?: LatLng;
    }[];
    newly_unassigned: { geo_id: string; persons: number; lat?: number; lng?: number }[];
    capacity_lost?: number;
    unassigned_before?: number;
    unassigned_after?: number;
  };
  maps_used: false;
  note?: string;
}

export interface StormTrack extends Envelope {
  storm_id: string;
  name: string;
  landfall_at?: string;
  landfall?: LatLng;
  source?: string;
  advisories: {
    advisory: string | number;
    issued_at: string;
    cone: Geometry | null;
    track: TrackPoint[];
  }[];
  best_track?: TrackPoint[];
}

// ---- ADK wire (camelCase; see contract §A1) -------------------------------------------------

export interface GroundingChunk {
  maps?: { uri?: string; title?: string; placeId?: string; place_id?: string };
  web?: { uri?: string; title?: string };
}

export interface GroundingSupport {
  segment?: { startIndex?: number; endIndex?: number; text?: string };
  groundingChunkIndices?: number[];
}

export interface GroundingMetadata {
  groundingChunks?: GroundingChunk[];
  groundingSupports?: GroundingSupport[];
}

export interface AdkPart {
  text?: string;
  thought?: boolean;
  functionCall?: { id?: string; name: string; args?: Record<string, unknown> };
  functionResponse?: { id?: string; name: string; response?: Record<string, unknown> };
}

export interface AdkEvent {
  id?: string;
  author?: string;
  invocationId?: string;
  timestamp?: number;
  partial?: boolean;
  turnComplete?: boolean;
  content?: { role?: string; parts?: AdkPart[] };
  groundingMetadata?: GroundingMetadata;
  errorCode?: string;
  errorMessage?: string;
  error?: string;
  error_details?: unknown;
}

export const KNOWN_PAYLOADS = [
  "readiness_brief.v1",
  "shelter_verification.v1",
  "county_map.v1",
  "allocation.v1",
  "rehearsal.v1",
  "storm_track.v1",
  "county_lookup.v1",
  "county_overview.v1",
  "tract_ranking.v1",
  "tract_destinations.v1",
  "shelter_lookup.v1",
  "care_facility_gaps.v1",
  "equity_audit.v1",
] as const;
