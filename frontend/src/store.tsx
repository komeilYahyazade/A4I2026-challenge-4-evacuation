import { createContext, useContext, useMemo, useReducer, useRef, type ReactNode } from "react";
import { createSession, runSse } from "./api/adk";
import { api } from "./api/rest";
import { CONFIG, MOCK_ONLY } from "./config";
import type {
  AdkEvent,
  Allocation,
  CountyMap,
  LatLng,
  MapsSource,
  ReadinessBrief,
  Rehearsal,
  ShelterVerification,
  StormTrack,
} from "./contract";
import { coords, collectSources, mergeVerification } from "./derive";
import { mockAllocation, mockBrief, mockMap, mockRehearsal, mockStorm, mockVerify } from "./mock/lee";

export type Phase = "inventory" | "plan" | "brief" | "watch" | "activate" | "learn";
export type Source = "live" | "mock";
export type DataKey = "map" | "brief" | "allocation" | "storm" | "rehearsal" | "verify";
export type LayerKey = "tracts" | "shelters" | "lines" | "gaps" | "storm";

export interface ToolChip {
  id: string;
  name: string;
  status: "calling" | "done";
  payloadType?: string;
}

export interface ChatMsg {
  id: string;
  role: "user" | "agent" | "error";
  author?: string;
  text: string;
  invocationId?: string;
  tools: ToolChip[];
  groundingSources: MapsSource[];
  toolSources: MapsSource[];
}

export type Step = 1 | 2 | 3 | 4;

export interface State {
  countyFips: string;
  step: Step;
  chatOpen: boolean;
  explain: boolean;
  hoverTract: string | null;
  phase: Phase;
  tab: "brief" | "chat";
  rehearsalMode: boolean;
  sources: Partial<Record<DataKey, Source>>;
  map: CountyMap | null;
  brief: ReadinessBrief | null;
  allocation: Allocation | null;
  storm: StormTrack | null;
  rehearsal: Rehearsal | null;
  extras: Record<string, unknown>;
  busy: Partial<Record<DataKey | "chat", boolean>>;
  verifying: string[];
  errors: { id: number; text: string }[];
  chat: ChatMsg[];
  selected: { kind: "shelter" | "tract" | "fix"; id: string; from?: "map" | "list" } | null;
  focus: { at: LatLng; zoom?: number; n: number } | null;
  layers: Record<LayerKey, boolean>;
  advisoryIdx: number;
  highlightFix: number | null;
}

type Action =
  | { type: "step"; step: Step }
  | { type: "chat"; open: boolean }
  | { type: "explain"; on: boolean }
  | { type: "hoverTract"; id: string | null }
  | { type: "county"; fips: string }
  | { type: "phase"; phase: Phase }
  | { type: "tab"; tab: State["tab"] }
  | { type: "rehearsalMode"; on: boolean }
  | { type: "load"; key: Exclude<DataKey, "verify">; value: unknown; source: Source }
  | { type: "verified"; value: ShelterVerification; source: Source }
  | { type: "busy"; key: DataKey | "chat"; on: boolean }
  | { type: "verifying"; ids: string[]; on: boolean }
  | { type: "error"; text: string }
  | { type: "dismiss"; id: number }
  | { type: "chatAdd"; msg: ChatMsg }
  | { type: "chatPatch"; id: string; patch: Partial<ChatMsg> }
  | { type: "extra"; payloadType: string; value: unknown }
  | { type: "select"; sel: State["selected"] }
  | { type: "focus"; at: LatLng; zoom?: number }
  | { type: "layer"; key: LayerKey; on: boolean }
  | { type: "advisory"; idx: number }
  | { type: "highlightFix"; idx: number | null };

const PHASE_LAYERS: Record<Phase, Record<LayerKey, boolean>> = {
  inventory: { tracts: false, shelters: true, lines: false, gaps: false, storm: false },
  plan: { tracts: true, shelters: true, lines: true, gaps: true, storm: false },
  brief: { tracts: true, shelters: true, lines: true, gaps: true, storm: false },
  watch: { tracts: false, shelters: true, lines: true, gaps: false, storm: true },
  activate: { tracts: false, shelters: true, lines: false, gaps: false, storm: false },
  learn: { tracts: false, shelters: true, lines: false, gaps: false, storm: false },
};

const initial: State = {
  countyFips: CONFIG.countyFips || "12071",
  step: 1,
  chatOpen: false,
  explain: false,
  hoverTract: null,
  phase: "brief",
  tab: "brief",
  rehearsalMode: false,
  sources: {},
  map: null,
  brief: null,
  allocation: null,
  storm: null,
  rehearsal: null,
  extras: {},
  busy: {},
  verifying: [],
  errors: [],
  chat: [],
  selected: null,
  focus: null,
  layers: PHASE_LAYERS.brief,
  advisoryIdx: 0,
  highlightFix: null,
};

let errSeq = 0;

const STEP_PHASE: Record<Step, Phase> = { 1: "brief", 2: "brief", 3: "watch", 4: "plan" };

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "step":
      return {
        ...s,
        step: a.step,
        phase: STEP_PHASE[a.step],
        layers: PHASE_LAYERS[STEP_PHASE[a.step]],
        rehearsalMode: a.step === 3,
        selected: null,
        highlightFix: null,
      };
    case "chat":
      return { ...s, chatOpen: a.open };
    case "explain":
      return { ...s, explain: a.on };
    case "hoverTract":
      return { ...s, hoverTract: a.id };
    case "county":
      return {
        ...s,
        countyFips: a.fips,
        selected: null,
        highlightFix: null,
      };
    case "phase":
      return { ...s, phase: a.phase, layers: PHASE_LAYERS[a.phase], rehearsalMode: a.phase === "watch" ? s.rehearsalMode : false };
    case "tab":
      return { ...s, tab: a.tab };
    case "rehearsalMode":
      return { ...s, rehearsalMode: a.on };
    case "load": {
      let value = a.value;
      // A brief that lands after live checks (slow first load) must not wipe them: carry them over.
      if (a.key === "brief" && s.brief && value) {
        const next = value as ReadinessBrief;
        const checked = s.brief.where_they_would_go.filter((r) => r.live);
        if (checked.length && next.county?.fips === s.brief.county?.fips) value = mergeVerification(next, checked);
      }
      return { ...s, [a.key]: value, sources: { ...s.sources, [a.key]: a.source } };
    }
    case "verified": {
      if (!s.brief) return s;
      return { ...s, brief: mergeVerification(s.brief, a.value.results), sources: { ...s.sources, verify: a.source } };
    }
    case "busy":
      return { ...s, busy: { ...s.busy, [a.key]: a.on } };
    case "verifying":
      return {
        ...s,
        verifying: a.on ? [...new Set([...s.verifying, ...a.ids])] : s.verifying.filter((x) => !a.ids.includes(x)),
      };
    case "error":
      return { ...s, errors: [...s.errors.slice(-3), { id: ++errSeq, text: a.text }] };
    case "dismiss":
      return { ...s, errors: s.errors.filter((e) => e.id !== a.id) };
    case "chatAdd":
      return { ...s, chat: [...s.chat, a.msg] };
    case "chatPatch":
      return { ...s, chat: s.chat.map((m) => (m.id === a.id ? { ...m, ...a.patch } : m)) };
    case "extra":
      return { ...s, extras: { ...s.extras, [a.payloadType]: a.value } };
    case "select":
      return { ...s, selected: a.sel };
    case "focus":
      return { ...s, focus: { at: a.at, zoom: a.zoom, n: (s.focus?.n || 0) + 1 } };
    case "layer":
      return { ...s, layers: { ...s.layers, [a.key]: a.on } };
    case "advisory":
      return { ...s, advisoryIdx: a.idx };
    case "highlightFix":
      return { ...s, highlightFix: a.idx };
  }
}

interface Ctx {
  s: State;
  d: React.Dispatch<Action>;
  act: ReturnType<typeof makeActions>;
}

const StoreCtx = createContext<Ctx | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [s, d] = useReducer(reducer, initial);
  const ref = useRef(s);
  ref.current = s;
  const act = useMemo(() => makeActions(d, () => ref.current), []);
  return <StoreCtx.Provider value={{ s, d, act }}>{children}</StoreCtx.Provider>;
}

export function useStore(): Ctx {
  const c = useContext(StoreCtx);
  if (!c) throw new Error("StoreProvider missing");
  return c;
}

function makeActions(d: React.Dispatch<Action>, get: () => State) {
  /** Live first; on failure (or with no AGENT_URL) fall back to the synthetic mock and say so. */
  async function load<T>(key: Exclude<DataKey, "verify">, live: () => Promise<T>, mock: () => T): Promise<T> {
    d({ type: "busy", key, on: true });
    try {
      if (MOCK_ONLY) {
        const v = mock();
        d({ type: "load", key, value: v, source: "mock" });
        return v;
      }
      try {
        const v = await live();
        d({ type: "load", key, value: v, source: "live" });
        return v;
      } catch (e) {
        d({ type: "error", text: `${key}: ${(e as Error).message}. Showing MOCK data instead.` });
        const v = mock();
        d({ type: "load", key, value: v, source: "mock" });
        return v;
      }
    } finally {
      d({ type: "busy", key, on: false });
    }
  }

  let inFlight = 0;
  async function verify(ids: string[], origin?: string) {
    if (!ids.length) return;
    inFlight++;
    d({ type: "verifying", ids, on: true });
    d({ type: "busy", key: "verify", on: true });
    try {
      let v: ShelterVerification;
      let src: Source = "live";
      if (MOCK_ONLY || get().sources.brief === "mock") {
        await new Promise((r) => setTimeout(r, 1200));
        v = mockVerify(ids, get().brief?.where_they_would_go || []);
        src = "mock";
      } else {
        try {
          v = await api.verify(ids, origin);
        } catch (e) {
          d({ type: "error", text: `verify: ${(e as Error).message}` });
          return;
        }
      }
      d({ type: "verified", value: v, source: src });
      const dropped = new Set<string>();
      for (const r of get().brief?.where_they_would_go || []) {
        if (r.verdict === "DROP") dropped.add(r.shelter_id);
      }
      for (const r of v.results || []) {
        if (r.verdict === "DROP") dropped.add(r.shelter_id);
      }
      if (dropped.size > 0 && (v.results || []).some((r) => r.verdict === "DROP")) {
        void loadAllocation([...dropped]);
      }
    } finally {
      inFlight--;
      d({ type: "verifying", ids, on: false });
      d({ type: "busy", key: "verify", on: inFlight > 0 });
    }
  }

  const loadMap = (fips = get().countyFips) => load("map", () => api.map(fips), () => mockMap);
  const loadAllocation = (down: string[] = [], fips = get().countyFips) =>
    load("allocation", () => api.allocate(down, fips), () => mockAllocation);
  // One brief request at a time: step 2 waits for the in-flight first load instead of starting another.
  let briefInFlight: { fips: string; p: Promise<ReadinessBrief> } | null = null;
  const loadBrief = (fips = get().countyFips): Promise<ReadinessBrief> => {
    if (briefInFlight && briefInFlight.fips === fips) return briefInFlight.p;
    const p = load("brief", () => api.brief(0, 5, fips), () => mockBrief).finally(() => {
      if (briefInFlight?.p === p) briefInFlight = null;
    });
    briefInFlight = { fips, p };
    return p;
  };
  const loadStorm = () => load("storm", api.storm, () => mockStorm);
  let verifiedFor: string | null = null;

  const actions = {
    loadBase: async (fips?: string) => {
      const target = fips || get().countyFips;
      await Promise.allSettled([loadMap(target), loadAllocation([], target), loadBrief(target)]);
    },

    setCounty: async (fips: string) => {
      d({ type: "county", fips });
      const [m] = await Promise.allSettled([loadMap(fips), loadAllocation([], fips), loadBrief(fips)]);
      if (m.status === "fulfilled" && m.value?.county) {
        d({ type: "focus", at: { lat: m.value.county.lat, lng: m.value.county.lng }, zoom: 10 });
      }
    },

    /** The one primary flow: each step sets the map layers and the panel, and loads what it needs. */
    goStep: async (n: Step) => {
      d({ type: "step", step: n });
      const st = get();
      if (n === 1) {
        const c = st.map?.county;
        if (c) d({ type: "focus", at: { lat: c.lat, lng: c.lng }, zoom: 10 });
      }
      if (n === 2 && verifiedFor !== st.countyFips) {
        verifiedFor = st.countyFips;
        await verifyTwo();
      }
      if (n === 3) {
        d({ type: "focus", at: { lat: 26.45, lng: -82.0 }, zoom: 9 });
        if (!st.storm) void loadStorm();
        if (!st.rehearsal) await load("rehearsal", () => api.rehearse(null, null, st.countyFips), mockRehearsal);
      }
      if (n === 4) {
        const at = coords(st.allocation?.site_options?.[0]?.ref);
        if (at) d({ type: "focus", at, zoom: 11 });
      }
    },

    /** Re-run the live check of two shelters (step 2). */
    verifyTwo: () => verifyTwo(),

    /** Demo beat 1: the August brief, then two shelters checked live against Google Maps. */
    briefBeat: async () => {
      d({ type: "step", step: 2 });
      await verifyTwo();
    },
  };

  /** Two shelters against live Google Maps: the one whose FEMA name says INACTIVE first, then brief order. */
  async function verifyTwo() {
    {
      const st = get();
      const brief = st.brief ?? (await loadBrief());
      if (!st.map) void loadMap();
      if (!st.allocation) void loadAllocation();
      // Brief order is the agent's own verification priority; a record whose name says INACTIVE goes first.
      const seen = new Set<string>();
      const candidates = brief.where_they_would_go
        .filter((r) => r.verdict === "NOT_VERIFIED_THIS_RUN" && r.record?.usage !== "POST")
        .filter((r) => (seen.has(r.shelter_id) ? false : (seen.add(r.shelter_id), true)))
        .map((r, i) => ({ r, i, inactive: r.record?.name_says_inactive ? 0 : 1 }))
        .sort((a, b) => a.inactive - b.inactive || a.i - b.i)
        .map((x) => x.r);
      const inactiveFix = brief.fix_before_season.find((f) => f.type === "NAME_SAYS_INACTIVE" && f.ref?.kind === "shelter");
      const jobs: [string, string | undefined][] = [];
      if (inactiveFix?.ref) {
        jobs.push([inactiveFix.ref.id, undefined]);
        const at = coords(inactiveFix.ref);
        if (at) d({ type: "focus", at, zoom: 12 });
      }
      for (const r of candidates) if (jobs.length < 2 && !jobs.some(([id]) => id === r.shelter_id)) jobs.push([r.shelter_id, r.for_tract]);
      await Promise.allSettled(jobs.map(([id, origin]) => verify([id], origin)));
    }
  }

  const rest = {
    /** Voice (run_live) tool results go through exactly the same dispatch as chat; returns their Maps sources. */
    ingestToolResponse: (response: Record<string, unknown> | undefined): { payloadType?: string; sources: MapsSource[] } => {
      const resp = unwrap(response);
      const pt = typeof resp?.payload_type === "string" ? (resp.payload_type as string) : undefined;
      if (!resp || !pt) return { sources: [] };
      dispatchPayload(pt, resp);
      return { payloadType: pt, sources: sourcesFromPayload(pt, resp) };
    },

    /** Demo beat 2: Ian tabletop rehearsal. Never calls Maps. */
    rehearsalBeat: async () => {
      d({ type: "phase", phase: "watch" });
      d({ type: "rehearsalMode", on: true });
      d({ type: "tab", tab: "brief" });
      d({ type: "focus", at: { lat: 26.45, lng: -82.0 }, zoom: 9 });
      const st = get();
      if (!st.storm) void loadStorm();
      await load("rehearsal", () => api.rehearse(null, null, st.countyFips), mockRehearsal);
    },

    /** Demo beat 3: what to fix before the season. */
    fixBeat: () => {
      d({ type: "phase", phase: "plan" });
      d({ type: "tab", tab: "brief" });
      d({ type: "highlightFix", idx: null });
      const site = get().allocation?.site_options?.[0];
      const at = coords(site?.ref);
      if (at) d({ type: "focus", at, zoom: 11 });
      setTimeout(() => document.getElementById("seg-fix")?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    },

    loadStorm,
    verify,

    focusOn: (at: LatLng | null, zoom?: number) => {
      if (at) d({ type: "focus", at, zoom });
    },

    sendChat: async (text: string, sessionRef: { current: string | null }) => {
      const uid = `u${Date.now()}`;
      d({ type: "chatAdd", msg: { id: uid, role: "user", text, tools: [], groundingSources: [], toolSources: [] } });
      d({ type: "tab", tab: "chat" });
      if (MOCK_ONLY) {
        d({
          type: "chatAdd",
          msg: { id: `e${uid}`, role: "error", text: "Chat needs the live agent (MODE=mock). The brief and rehearsal buttons work on MOCK data.", tools: [], groundingSources: [], toolSources: [] },
        });
        return;
      }
      d({ type: "busy", key: "chat", on: true });
      // Messages of this invocation, so Maps sources from tool results attach to the final text.
      const invocationSources: MapsSource[] = [];
      let lastTextId: string | null = null;
      const toolMsgId = `t${uid}`;
      const tools: ToolChip[] = [];
      let toolMsgAdded = false;
      try {
        if (!sessionRef.current) sessionRef.current = await createSession({ county_fips: get().countyFips });
        // The manager is looking at one county; say so, so the agent never has to ask. The chat shows their own words.
        const st = get();
        const countyLabel = st.map?.county?.name || st.brief?.county?.name || "";
        const sent = `[Context: ${countyLabel ? `${countyLabel.replace(/ County$/, "")} County, ` : ""}Florida, county FIPS ${st.countyFips}] ${text}`;
        try {
          for await (const ev of runSse(sessionRef.current, sent)) handleEvent(ev);
        } catch (e) {
          // Sessions live in the agent's memory; after a redeploy the old one 404s. Start a new one once.
          if (!/HTTP 404/.test((e as Error).message)) throw e;
          sessionRef.current = await createSession({ county_fips: get().countyFips });
          for await (const ev of runSse(sessionRef.current, sent)) handleEvent(ev);
        }
      } catch (e) {
        d({ type: "chatAdd", msg: { id: `x${uid}`, role: "error", text: (e as Error).message, tools: [], groundingSources: [], toolSources: [] } });
      } finally {
        d({ type: "busy", key: "chat", on: false });
      }

      function handleEvent(ev: AdkEvent) {
        if (ev.error || ev.errorMessage) {
          d({ type: "chatAdd", msg: { id: `x${uid}${Math.random()}`, role: "error", text: ev.error || ev.errorMessage || "error", tools: [], groundingSources: [], toolSources: [] } });
          return;
        }
        const parts = ev.content?.parts || [];
        let text = "";
        for (const p of parts) {
          if (p.functionCall) {
            tools.push({ id: p.functionCall.id || p.functionCall.name, name: p.functionCall.name, status: "calling" });
          }
          if (p.functionResponse) {
            const resp = unwrap(p.functionResponse.response);
            const chip = tools.find((t) => t.id === (p.functionResponse!.id || p.functionResponse!.name) && t.status === "calling") ||
              tools.find((t) => t.name === p.functionResponse!.name && t.status === "calling");
            const pt = typeof resp?.payload_type === "string" ? (resp.payload_type as string) : undefined;
            if (chip) {
              chip.status = "done";
              chip.payloadType = pt;
            } else tools.push({ id: p.functionResponse.name, name: p.functionResponse.name, status: "done", payloadType: pt });
            if (resp && pt) {
              dispatchPayload(pt, resp);
              invocationSources.push(...sourcesFromPayload(pt, resp));
            }
          }
          if (p.text && !p.thought) text += p.text;
        }
        if (tools.length) {
          if (!toolMsgAdded) {
            toolMsgAdded = true;
            d({ type: "chatAdd", msg: { id: toolMsgId, role: "agent", author: "tools", text: "", tools: [...tools], groundingSources: [], toolSources: [] } });
          } else d({ type: "chatPatch", id: toolMsgId, patch: { tools: [...tools] } });
        }
        if (text.trim()) {
          const id = `a${uid}${ev.id || Math.random()}`;
          lastTextId = id;
          d({
            type: "chatAdd",
            msg: {
              id,
              role: "agent",
              author: ev.author,
              invocationId: ev.invocationId,
              text,
              tools: [],
              groundingSources: groundingToSources(ev),
              toolSources: dedupe(invocationSources),
            },
          });
        } else if (ev.groundingMetadata && lastTextId) {
          d({ type: "chatPatch", id: lastTextId, patch: { groundingSources: groundingToSources(ev) } });
        }
      }
    },
  };

  return Object.assign(actions, rest);

  /**
   * v1.4: chat tools return compact payloads ({compact:true, full:{method,path,body}}) to stay under the
   * model's input cap. Never draw a compact payload as the full plan: fetch the full one through the
   * same-origin proxy (allowlisted paths only; the pointer comes from tool output), then draw that.
   */
  async function expandCompact(pt: string, resp: Record<string, unknown>) {
    const full = resp.full as { method?: string; path?: string; body?: unknown } | undefined;
    const allowed = new Set(["/api/allocate", "/api/rehearse"]);
    const key = pt === "rehearsal.v1" ? "rehearsal" : "allocation";
    if (!full || full.method !== "POST" || !full.path || !allowed.has(full.path)) {
      d({ type: "error", text: `${pt}: chat returned a summary only; the map keeps the last full plan.` });
      return;
    }
    d({ type: "busy", key, on: true });
    try {
      const res = await fetch(`${CONFIG.agentUrl}${full.path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(full.body ?? {}),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const payload = (await res.json()) as Record<string, unknown>;
      dispatchPayload(String(payload.payload_type || pt), payload);
    } catch (e) {
      d({ type: "error", text: `${pt}: could not load the full payload (${(e as Error).message}); the map keeps the last full plan.` });
    } finally {
      d({ type: "busy", key, on: false });
    }
  }

  function dispatchPayload(pt: string, resp: Record<string, unknown>) {
    if (resp.compact === true && (pt === "allocation.v1" || pt === "rehearsal.v1")) {
      if (pt === "rehearsal.v1") d({ type: "step", step: 3 });
      void expandCompact(pt, resp);
      return;
    }
    switch (pt) {
      case "readiness_brief.v1":
        d({ type: "load", key: "brief", value: resp, source: "live" });
        return;
      case "shelter_verification.v1":
        d({ type: "verified", value: resp as unknown as ShelterVerification, source: "live" });
        return;
      case "county_map.v1":
        d({ type: "load", key: "map", value: resp, source: "live" });
        return;
      case "allocation.v1":
        d({ type: "load", key: "allocation", value: resp, source: "live" });
        return;
      case "rehearsal.v1":
        d({ type: "load", key: "rehearsal", value: resp, source: "live" });
        d({ type: "step", step: 3 });
        return;
      case "storm_track.v1":
        d({ type: "load", key: "storm", value: resp, source: "live" });
        return;
      default:
        d({ type: "extra", payloadType: pt, value: resp });
        focusFromExtra(pt, resp);
    }
  }

  function focusFromExtra(pt: string, resp: Record<string, unknown>) {
    // Pan to the first thing a conversational tool talked about.
    const first =
      pt === "tract_ranking.v1"
        ? (resp.tracts as unknown[] | undefined)?.[0]
        : pt === "tract_destinations.v1"
          ? (resp.shelters as unknown[] | undefined)?.[0]
          : pt === "shelter_lookup.v1"
            ? (resp.matches as unknown[] | undefined)?.[0]
            : undefined;
    const c = coords(first);
    if (c) d({ type: "focus", at: c, zoom: 12 });
  }
}

function unwrap(r: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
  if (!r) return r;
  if (typeof r.payload_type === "string") return r;
  const inner = r.result as Record<string, unknown> | undefined;
  return inner && typeof inner === "object" && typeof inner.payload_type === "string" ? inner : r;
}

function sourcesFromPayload(pt: string, resp: Record<string, unknown>): MapsSource[] {
  if (pt === "readiness_brief.v1") return ((resp.maps_sources as MapsSource[]) || []).slice();
  if (pt === "shelter_verification.v1") return collectSources(((resp.results as never[]) || []) as never);
  return [];
}

export function groundingToSources(ev: AdkEvent): MapsSource[] {
  const chunks = ev.groundingMetadata?.groundingChunks || [];
  return chunks
    .filter((c) => c.maps)
    .map((c) => ({ title: c.maps!.title, uri: c.maps!.uri, place_id: c.maps!.placeId || c.maps!.place_id }));
}

export function dedupe(src: MapsSource[]): MapsSource[] {
  const m = new Map<string, MapsSource>();
  for (const s of src) m.set(s.place_id || s.uri || s.title || "", s);
  return [...m.values()];
}
