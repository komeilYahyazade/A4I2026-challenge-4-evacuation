// One function per /v1 route. Same origin: server.py forwards /v1 to the private readiness-api.
import type {
  ApiErrorBody, AssessResponse, AuditResponse, BriefStatus, ChatResponse, CountiesResponse, CountyInsights, CountyMap,
  Health, ShelterDetail, ShelterVerification, Track,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(503, { code: "api_unreachable", message: "Could not reach the API.", retryable: true });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (data && data.error) || { code: "error", message: `HTTP ${res.status}`, retryable: res.status >= 500 };
    throw new ApiError(res.status, err);
  }
  return data as T;
}

export const api = {
  health: () => call<Health>("GET", "/v1/health"),
  counties: () => call<CountiesResponse>("GET", "/v1/counties"),
  countyMap: (fips: string) => call<CountyMap>("GET", `/v1/counties/${fips}/map`),
  insights: (fips: string, reachKm: number) =>
    call<CountyInsights>("GET", `/v1/counties/${fips}/insights?reachable_km=${reachKm}`),
  shelter: (id: string) => call<ShelterDetail>("GET", `/v1/shelters/${id}`),
  verify: (id: string, fromGeoId: string | null) =>
    call<ShelterVerification>("POST", `/v1/shelters/${id}/verify`, { from_geo_id: fromGeoId, include_route: !!fromGeoId }),
  createBrief: (fips: string, track: Track, reachKm: number, maxVerify: number, routes: boolean) =>
    call<{ brief_id: string; status: string; poll_url: string }>("POST", "/v1/briefs", {
      county_fips: fips, track,
      options: { reachable_km_no_vehicle: reachKm, max_shelters_to_verify: maxVerify, include_routes: routes },
    }),
  brief: (id: string) => call<BriefStatus>("GET", `/v1/briefs/${id}`),
  refreshBrief: (id: string) => call<BriefStatus>("POST", `/v1/briefs/${id}/refresh`),
  assess: (shelterId: string, message: string, sessionId: string | null) =>
    call<AssessResponse>("POST", `/v1/shelters/${shelterId}/assess`, { message, session_id: sessionId }),
  audit: (id: string) => call<AuditResponse>("GET", `/v1/briefs/${id}/audit`),
  chat: (message: string, sessionId: string | null, fips: string | null, briefId: string | null) =>
    call<ChatResponse>("POST", "/v1/chat", { message, session_id: sessionId, county_fips: fips, brief_id: briefId }),
};
