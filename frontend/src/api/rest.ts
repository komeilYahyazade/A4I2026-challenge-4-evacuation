// Deterministic REST routes (contract §A2). No LLM in the loop; the demo beats run on these.
import { CONFIG } from "../config";
import type { Allocation, CountyMap, OperatorAssessment, ReadinessBrief, Rehearsal, ShelterVerification, StormTrack } from "../contract";

export class ApiError extends Error {
  constructor(public status: number, public code: string, detail: string) {
    super(detail || code);
  }
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown, timeoutMs = 60_000): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(`${CONFIG.agentUrl}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctl.signal,
    });
    const text = await res.text();
    let json: unknown = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      /* non-JSON error page */
    }
    if (!res.ok) {
      const j = (json || {}) as { error?: string; detail?: string };
      throw new ApiError(res.status, j.error || `HTTP_${res.status}`, friendlyHttp(res, j.detail || text.slice(0, 300)));
    }
    return json as T;
  } catch (e) {
    if ((e as Error).name === "AbortError") throw new ApiError(0, "TIMEOUT", `${path} took longer than ${timeoutMs / 1000}s`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/** Public-demo limits read as plain language rather than raw status codes. */
export function friendlyHttp(res: Response, detail: string): string {
  if (res.status === 429) {
    const wait = Number(res.headers.get("retry-after") || 0);
    return `Demo limit reached, try again in ${wait > 0 ? wait : 60} s.`;
  }
  if (res.status === 413) return "That request is too large for the demo.";
  if (res.status === 403 && /PUBLIC_DEMO_READ_ONLY|signed-in/i.test(detail)) return detail;
  return detail;
}

const fips = CONFIG.countyFips;

export const api = {
  health: () => call<{ ok: boolean; contract_version?: string }>("GET", "/api/health", undefined, 10_000),
  map: (countyFips = fips) => call<CountyMap>("GET", `/api/map?county_fips=${countyFips}`, undefined, 30_000),
  brief: (verifyTopN = 0, topTracts = 5, countyFips = fips) =>
    call<ReadinessBrief>("POST", "/api/brief", { county_fips: countyFips, top_tracts: topTracts, verify_top_n: verifyTopN }, 120_000),
  verify: (shelterIds: string[], originGeoId?: string) =>
    call<ShelterVerification>(
      "POST",
      "/api/verify",
      { shelter_ids: shelterIds, ...(originGeoId ? { origin_geo_id: originGeoId } : {}) },
      90_000,
    ),
  allocate: (sheltersDown: string[] = [], countyFips = fips) =>
    call<Allocation>("POST", "/api/allocate", { county_fips: countyFips, shelters_down: sheltersDown, max_km: 15, shelter_seeking_share: null }),
  rehearse: (advisory: string | number | null = null, sheltersDown: string[] | null = null, countyFips = fips) =>
    call<Rehearsal>("POST", "/api/rehearse", { county_fips: countyFips, scenario: "ian_2022", advisory, shelters_down: sheltersDown }),
  storm: () => call<StormTrack>("GET", "/api/storm/ian_2022", undefined, 30_000),
  // v1.3 plans: a person approves before anything is published. Approval is REST only, never the agent.
  planCreate: (kind: "allocation" | "rehearsal", createdBy: string, countyFips = fips) =>
    call<PlanRef>("POST", "/api/plans", { kind, county_fips: countyFips, created_by: createdBy }, 60_000),
  planApprove: (planId: string, approvedBy: string) =>
    call<PlanRef>("POST", `/api/plans/${encodeURIComponent(planId)}/approve`, { approved_by: approvedBy }, 30_000),
  plans: (countyFips = fips) => call<{ plans: PlanRef[] }>("GET", `/api/plans?county_fips=${countyFips}`, undefined, 30_000),
  assessments: (county?: string) =>
    call<{ status: string; assessments: OperatorAssessment[] }>(
      "GET",
      `/api/assessments${county ? `?county=${encodeURIComponent(county)}` : ""}`,
      undefined,
      15_000,
    ),
  assessShelter: (body: {
    shelter_id: string;
    shelter_name: string;
    county?: string;
    operator_name?: string;
    operator_statement: string;
    unaccompanied_entry?: boolean;
    medical_power_outlets?: boolean;
  }) =>
    call<{ status: string; assessment: OperatorAssessment }>("POST", "/api/assess-shelter", body, 30_000),
};

export interface PlanRef {
  plan_id: string;
  version?: number;
  mode?: string;
  status: "DRAFT" | "APPROVED" | string;
  created_by?: string;
  approved_by?: string | null;
  approved_at?: string | null;
  payload_type?: string;
  totals?: Record<string, number>;
}
