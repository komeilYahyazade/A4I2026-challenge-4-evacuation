// Mirror of contract/schemas.py (v0.2). Ids are strings; recorded flags are three-state.

export type Flag = "yes" | "no" | "unrecorded";
export type Freshness = "historical" | "live";
export type VerificationStatus = "confirmed" | "renamed" | "closed" | "not_found" | "unclear" | "error";
export type MapsWheelchair = "yes" | "no" | "no_answer";
export type Agreement = "agree" | "disagree" | "record_blank" | "maps_blank" | "both_blank";
export type Verdict = "holds_up" | "verify_before_season" | "do_not_rely";
export type FixCategory =
  | "no_accessible_destination" | "unassessed_access" | "generator_gap"
  | "shelter_in_surge_zone" | "record_mismatch" | "medical_needs_gap";
export type Track = "shelter_readiness" | "getting_people_out" | "medically_dependent";

export interface ApiErrorBody { code: string; message: string; retryable: boolean }
export interface LatLon { lat: number; lon: number }
export interface Source { id: string; provider: "Google Maps"; title: string | null; uri: string | null; place_id: string | null }
export interface Segment { text: string; source_ids: string[]; freshness: Freshness }

export interface Shelter {
  shelter_id: string; name: string; address: string; county_fips: string | null; lat: number; lon: number;
  nearest_geo_id: string | null; usage: string | null; evacuation_capacity: number | null;
  post_impact_capacity: number | null; wheelchair_accessible: Flag; generator_onsite: Flag;
  in_surge_slosh_area: Flag; in_100_yr_floodplain: Flag; is_medical: boolean; place_id: string | null;
  source: "fema_nss"; freshness: "historical";
}
export interface ShelterDetail extends Shelter {
  pet_accommodations_code: string | null; population_code: string | null; shelter_status_code: string | null;
  incident_id: string | null; last_verification: ShelterVerification | null;
}
export interface ShelterVerification {
  shelter_id: string; checked_at: string; status: VerificationStatus; maps_name: string | null; record_name: string;
  place_type: string | null; open_now: boolean | null;
  wheelchair: { maps: MapsWheelchair; record: Flag; agreement: Agreement };
  drive: { from_geo_id: string | null; drive_minutes: number | null; drive_km: number | null } | null;
  summary: Segment[]; sources: Source[];
}

export interface Health { status: "ok"; mode: "mock" | "live"; model: string; state: string; dataset: string; version: string }
export interface County {
  county_fips: string; name: string; rank: number; need_score: number | null; exposure_score: number | null;
  total_pop: number | null; center: LatLon | null;
  shelters: { total: number; medical_needs: number; in_surge_zone: number; wheelchair: Record<Flag, number> };
}
export interface Tract {
  geo_id: string; lat: number; lon: number; total_pop: number | null; households_no_vehicle: number | null;
  pct_no_vehicle: number | null; pop_65_plus: number | null; pct_age_65_plus: number | null;
  pct_disability: number | null; mobile_homes: number | null; group_quarters_pop: number | null;
  need_score: number | null; nearest_shelter_km: number | null; nearest_wheelchair_yes_km: number | null;
  households?: number | null; exposure_score?: number | null; pct_mobility?: number | null;
}
export interface CareFacility { ccn: string; name: string; lat: number; lon: number; certified_beds: number | null; nearest_generator_yes_km?: number | null }
export interface PowerDependence { county_fips: string; power_dependent_dme: number | null; is_censored: boolean; scope: "county" }
export interface CountyMap {
  county_fips: string; center: LatLon | null; tracts: Tract[]; shelters: Shelter[];
  care_facilities: CareFacility[]; power_dependence: PowerDependence | null;
}

export type FlagName = "wheelchair_accessible" | "generator_onsite" | "self_sufficient_electricity" | "in_surge_slosh_area" | "in_100_yr_floodplain";
export interface FlagRow { scope: "all" | "evac"; flag: FlagName; yes: number; no: number; unrecorded: number }
export interface AccessStats {
  tracts: number; median_km_any: number | null; median_km_wheelchair_yes: number | null; people_over_10km_any: number | null;
  households_no_vehicle: number | null; households_no_vehicle_within_reach_any: number | null;
  households_no_vehicle_within_reach_wheelchair_yes: number | null; tracts_no_wheelchair_yes_within_reach: number;
}
export interface Baseline {
  pct_no_vehicle: number | null; pct_age_65_plus: number | null; pct_disability: number | null;
  pct_mobile_homes: number | null; poverty_rate: number | null; pct_mobility: number | null;
}
export interface CountyInsights {
  county_fips: string; name: string; reachable_km: number;
  shelters: { total: number; evac: number; medical_needs: number; capacity_recorded: number | null; capacity_unrecorded: number; capacity_implausible: number; verified_on_maps: number };
  flags: FlagRow[];
  access: { county: AccessStats; state: AccessStats };
  baseline: { county: Baseline; state: Baseline };
  care: { facilities: number; certified_beds: number | null; far_from_generator: number; certified_beds_far_from_generator: number | null; threshold_km: number };
  power: { power_dependent_dme: number | null; is_censored: boolean; medical_capacity: number | null; scope: "county" };
}

export interface Driver { metric: string; value: number | null; unit: string; source: string }
export interface WhoCannotLeave {
  rank: number; kind: "tract" | "care_facility"; id: string; label: string; lat: number; lon: number;
  people: number | null; drivers: Driver[]; reason: string;
}
export interface Affected { tracts: string[]; shelters: string[]; care_facilities: string[] }
export interface Destination {
  shelter: Shelter; serves: string[]; straight_line_km: number | null; verification: ShelterVerification | null;
  verdict: Verdict; verdict_reason: Segment[];
}
export interface Fix { priority: number; category: FixCategory; title: string; recommendation: string; affected: Affected; source_ids: string[] }
export interface Unknown { topic: string; count: number | null; of: number | null; unit: string; why: string; how_to_close: string }
export interface Brief {
  county: { county_fips: string; name: string; state: string }; track: Track; generated_at: string; model: string;
  headline: string; assumptions: Record<string, string | number | null>;
  who_cannot_leave: WhoCannotLeave[];
  power_dependence: { power_dependent_dme: number | null; is_censored: boolean; caveat: string } | null;
  destinations: Destination[]; fixes: Fix[]; unknowns: Unknown[]; sources: Source[];
}
export interface BriefStatus {
  brief_id: string; status: "running" | "complete" | "failed";
  progress: { step: string; message: string; done: number; total: number };
  brief: Brief | null; error: ApiErrorBody | null;
}
export interface AuditRow { metric: string; unit: string; recommended: number | null; county: number | null; state: number | null; used_as_input: boolean }
export interface AuditResponse { brief_id: string; compares: string; rows: AuditRow[]; finding: string | null }
export interface ChatResponse {
  session_id: string; reply: Segment[]; sources: Source[]; highlight: Affected;
  tool_calls: { name: string; duration_ms: number }[]; refused: boolean;
}

export type Selection = { kind: "shelter" | "tract" | "care"; id: string } | null;
export const EMPTY_AFFECTED: Affected = { tracts: [], shelters: [], care_facilities: [] };
export interface CountiesResponse { state: string; counties: County[] }

// ---- v0.3: allocation, Ian rehearsal, storm track (no Google Maps in any of these) -------------
export interface AllocTotals { demand_persons: number; assigned_persons: number; unassigned_persons: number; usable_capacity: number; shelters_used: number }
export interface AllocTract { geo_id: string; lat: number | null; lon: number | null; households_no_vehicle: number; persons_per_household: number | null; demand_persons: number; assigned_persons: number; unassigned_persons: number }
export interface AllocShelter {
  shelter_id: string; name: string; lat: number | null; lon: number | null; county_fips: string | null; capacity: number | null;
  capacity_implausible: boolean; wheelchair_accessible: Flag; generator_onsite: Flag; in_surge_slosh_area: Flag; is_medical: boolean;
  excluded_reason: string | null; assigned_persons: number; utilization: number | null;
}
export interface Assignment { geo_id: string; shelter_id: string; persons: number; straight_line_km: number; mode: "WALK" | "PARATRANSIT" }
export interface Unplaced { geo_id: string; persons: number; lat: number | null; lon: number | null; reason: "CAPACITY_EXHAUSTED" | "NO_SHELTER_WITHIN_MAX_KM" }
export interface SiteOption { geo_id: string; lat: number | null; lon: number | null; persons_covered: number; tracts_covered: string[] }
export interface Allocation {
  county_fips: string; status: string; max_km: number; shelter_seeking_share: number; totals: AllocTotals; tracts: AllocTract[];
  shelters: AllocShelter[]; assignments: Assignment[]; unassigned: Unplaced[]; site_options: SiteOption[];
  excluded_counts: Record<string, number>; rules: Record<string, string>;
}
export interface DownShelter { shelter_id: string; name: string; lat: number | null; lon: number | null; reason: "IN_SURGE_ZONE" | "IN_CONE_AND_FLOOD_EXPOSED"; in_plan: boolean; capacity: number | null; evidence: string }
export interface Reassignment { geo_id: string; from_shelter_id: string; to_shelter_id: string; persons: number; tract_lat: number | null; tract_lon: number | null; to_lat: number | null; to_lon: number | null; from_lat: number | null; from_lon: number | null }
export interface Rehearsal {
  county_fips: string;
  scenario: { id: string; name: string; storm_id: string; landfall_at: string; source: string; advisory: string; issued_at: string | null; hours_before_landfall: number | null };
  at_risk_total: number; shelters_down: DownShelter[]; at_risk_not_in_plan: DownShelter[]; before: AllocTotals; after: AllocTotals;
  capacity_lost: number; reassigned: Reassignment[]; reassigned_total: number; newly_unassigned: Unplaced[];
  cone: GeoJsonGeometry | null; note: string;
}
export interface GeoJsonGeometry { type: string; coordinates: unknown }
export interface TrackPoint { lat: number; lon: number; valid_at?: string | null; wind_kt?: number | null }
export interface StormTrack {
  storm_id: string; name: string; landfall_at: string; landfall: LatLon; source: string;
  advisories: { advisory: string; issued_at: string | null; hours_before_landfall: number | null; cone: GeoJsonGeometry | null; track: TrackPoint[] }[];
  best_track: TrackPoint[];
}
export type Step = 1 | 2 | 3 | 4;
export type View = "map" | "insights";
