// Deterministic REST routes (contract §A2). No LLM in the loop; the demo beats run on these.
import { CONFIG } from "../config";
import type { Allocation, CountyMap, ReadinessBrief, Rehearsal, ShelterVerification, StormTrack } from "../contract";

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
      throw new ApiError(res.status, j.error || `HTTP_${res.status}`, j.detail || text.slice(0, 300));
    }
    return json as T;
  } catch (e) {
    if ((e as Error).name === "AbortError") throw new ApiError(0, "TIMEOUT", `${path} took longer than ${timeoutMs / 1000}s`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

const fips = CONFIG.countyFips;

export const api = {
  health: () => call<{ ok: boolean; contract_version?: string }>("GET", "/api/health", undefined, 10_000),
  map: () => call<CountyMap>("GET", `/api/map?county_fips=${fips}`, undefined, 30_000),
  brief: (verifyTopN = 0, topTracts = 5) =>
    call<ReadinessBrief>("POST", "/api/brief", { county_fips: fips, top_tracts: topTracts, verify_top_n: verifyTopN }, 120_000),
  verify: (shelterIds: string[], originGeoId?: string) =>
    call<ShelterVerification>(
      "POST",
      "/api/verify",
      { shelter_ids: shelterIds, ...(originGeoId ? { origin_geo_id: originGeoId } : {}) },
      90_000,
    ),
  allocate: (sheltersDown: string[] = []) =>
    call<Allocation>("POST", "/api/allocate", { county_fips: fips, shelters_down: sheltersDown, max_km: 15, shelter_seeking_share: null }),
  rehearse: (advisory: string | number | null = null, sheltersDown: string[] | null = null) =>
    call<Rehearsal>("POST", "/api/rehearse", { county_fips: fips, scenario: "ian_2022", advisory, shelters_down: sheltersDown }),
  storm: () => call<StormTrack>("GET", "/api/storm/ian_2022", undefined, 30_000),
};
