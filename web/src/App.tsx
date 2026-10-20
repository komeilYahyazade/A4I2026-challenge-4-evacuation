import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api";
import type {
  Affected, AuditResponse, BriefStatus, County, CountyInsights, CountyMap, Health, Selection, ShelterVerification,
} from "./types";
import { EMPTY_AFFECTED } from "./types";
import { BriefPanel, CountySnapshot } from "./components/BriefPanel";
import { ChatPanel } from "./components/ChatPanel";
import { FixList } from "./components/FixList";
import { InsightsView } from "./components/InsightsView";
import { MapView, type MapConfig } from "./components/MapView";
import { Rail, type BriefControls } from "./components/Rail";
import { ShelterPanel } from "./components/ShelterPanel";

type Mode = "brief" | "insights" | "fixes";
type Tab = "main" | "details" | "chat";
const cfg: MapConfig = (window as unknown as { __CONFIG__?: MapConfig }).__CONFIG__ ?? {};
const params = new URLSearchParams(window.location.search);

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [counties, setCounties] = useState<County[]>([]);
  const [county, setCounty] = useState(params.get("county") ?? "12071");
  const [mode, setMode] = useState<Mode>((params.get("mode") as Mode) ?? "brief");
  const [tab, setTab] = useState<Tab>("main");
  const [map, setMap] = useState<CountyMap | null>(null);
  const [ins, setIns] = useState<CountyInsights | null>(null);
  const [controls, setControls] = useState<BriefControls>({ track: "shelter_readiness", reachKm: 3, maxVerify: 12, routes: true });
  const [briefId, setBriefId] = useState<string | null>(params.get("brief"));
  const [status, setStatus] = useState<BriefStatus | null>(null);
  const [audit, setAudit] = useState<AuditResponse | null>(null);
  const [verifications, setVerifications] = useState<Record<string, ShelterVerification>>({});
  const [selection, setSelection] = useState<Selection>(null);
  const [highlight, setHighlight] = useState<Affected>(EMPTY_AFFECTED);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const brief = status?.status === "complete" ? status.brief : null;

  useEffect(() => {
    api.health().then(setHealth).catch((e: ApiError) => setError(`API: ${e.message}`));
    api.counties().then((r) => setCounties(r.counties)).catch((e: ApiError) => setError(`Counties: ${e.message}`));
  }, []);

  useEffect(() => {
    setMap(null); setSelection(null); setHighlight(EMPTY_AFFECTED);
    api.countyMap(county).then(setMap).catch((e: ApiError) => setError(`Map: ${e.message}`));
  }, [county]);

  useEffect(() => {
    const t = setTimeout(() => {
      api.insights(county, controls.reachKm).then(setIns).catch((e: ApiError) => setError(`Insights: ${e.message}`));
    }, 250);
    return () => clearTimeout(t);
  }, [county, controls.reachKm]);

  // Poll the brief every 2 s until it completes or fails.
  useEffect(() => {
    if (!briefId) { setStatus(null); return; }
    let stop = false;
    const tick = async () => {
      try {
        const s = await api.brief(briefId);
        if (stop) return;
        setStatus(s);
        if (s.status === "running") setTimeout(tick, 2000);
        else if (s.status === "complete") api.audit(briefId).then(setAudit).catch(() => setAudit(null));
      } catch (e) {
        if (!stop) { setError(`Brief: ${(e as ApiError).message}`); setBriefId(null); }
      }
    };
    setAudit(null);
    tick();
    return () => { stop = true; };
  }, [briefId]);

  // Keep county, brief and mode in the URL so a refresh reopens the same view.
  useEffect(() => {
    const p = new URLSearchParams({ county, mode });
    if (briefId) p.set("brief", briefId);
    window.history.replaceState(null, "", `?${p}`);
  }, [county, briefId, mode]);

  const generate = async () => {
    setStarting(true); setError(null);
    try {
      const r = await api.createBrief(county, controls.track, controls.reachKm, controls.maxVerify, controls.routes);
      setStatus(null); setBriefId(r.brief_id); setMode("brief"); setTab("main");
    } catch (e) { setError(`Could not start the brief: ${(e as ApiError).message}`); } finally { setStarting(false); }
  };

  const select = useCallback((s: Selection) => { setSelection(s); setTab("details"); }, []);
  const changeCounty = (fips: string) => {
    setCounty(fips);
    if (status?.brief && status.brief.county.county_fips !== fips) { setBriefId(null); setStatus(null); }
  };
  const countyName = counties.find((c) => c.county_fips === county)?.name ?? map?.county_fips ?? "";

  return (
    <div className="app">
      <header className="header">
        <div className="title">
          <h1>{countyName ? `${countyName} Readiness Console` : "Readiness Console"}</h1>
          <p>Where verified, reachable shelters and pickup runs are needed for residents who cannot drive, before the September peak of hurricane season. Planning only.</p>
        </div>
        <nav className="modes" aria-label="Views">
          <button className="mode-btn" aria-pressed={mode === "brief"} onClick={() => { setMode("brief"); setTab("main"); }}>1 · August brief</button>
          <button className="mode-btn" aria-pressed={mode === "insights"} onClick={() => setMode("insights")}>2 · Insights</button>
          <button className="mode-btn" aria-pressed={mode === "fixes"} onClick={() => { setMode("fixes"); setTab("main"); }}>3 · Fix list</button>
          <button className="mode-btn" disabled title="Tier 2: re-match tracts with surge-zone shelters removed (no Google Maps calls). Not built yet.">4 · Ian rehearsal</button>
        </nav>
        <div className="right">
          <select value={county} onChange={(e) => changeCounty(e.target.value)} aria-label="County">
            {[...counties].sort((a, b) => a.rank - b.rank).map((c) => <option key={c.county_fips} value={c.county_fips}>#{c.rank} {c.name}</option>)}
          </select>
          {health && <span className={`badge ${health.mode}`} title={`${health.model} · ${health.dataset}`}>{health.mode === "live" ? "LIVE" : "MOCK"} · {health.model}</span>}
        </div>
      </header>

      <div className={`body${mode === "insights" ? " wide" : ""}`}>
        <Rail controls={controls} setControls={setControls} onGenerate={generate} status={status} busy={starting} />
        <main className={`main${mode === "insights" ? " scroll" : ""}`}>
          {error && <div className="map-overlay err" style={{ top: 10, right: 10, zIndex: 9 }} onClick={() => setError(null)}>{error} ✕</div>}
          {mode === "insights" ? (
            <InsightsView ins={ins} map={map} counties={counties} county={county} brief={brief} reachKm={controls.reachKm}
              onSelectTract={(id) => { setMode("brief"); select({ kind: "tract", id }); }} onCounty={changeCounty} />
          ) : (
            <MapView cfg={cfg} data={map} brief={brief} verifications={verifications} highlight={highlight} selection={selection} onSelect={select} />
          )}
        </main>
        {mode !== "insights" && (
          <section className="panel" aria-label="Details">
            <div className="tabs" role="tablist">
              <button className="tab" role="tab" aria-selected={tab === "main"} onClick={() => setTab("main")}>{mode === "fixes" ? "Fix list" : "Brief"}</button>
              <button className="tab" role="tab" aria-selected={tab === "details"} onClick={() => setTab("details")}>Details</button>
              <button className="tab" role="tab" aria-selected={tab === "chat"} onClick={() => setTab("chat")}>Chat</button>
            </div>
            {tab === "chat" ? (
              <ChatPanel county={county} briefId={brief ? briefId : null} onHighlight={setHighlight} />
            ) : (
              <div className="panel-body">
                {tab === "details" && (
                  <ShelterPanel selection={selection} map={map} brief={brief} ins={ins} verifications={verifications} reachKm={controls.reachKm}
                    onVerified={(v) => setVerifications((m) => ({ ...m, [v.shelter_id]: v }))} onSelect={select} />
                )}
                {tab === "main" && mode === "fixes" && <FixList brief={brief} map={map} onHighlight={setHighlight} onSelect={select} />}
                {tab === "main" && mode === "brief" && (brief ? (
                  <BriefPanel brief={brief} ins={ins} map={map} audit={audit} reachKm={controls.reachKm} selection={selection}
                    onSelect={select} onHighlight={setHighlight} onFixList={() => { setMode("fixes"); setTab("main"); }} />
                ) : (<>
                  {status?.status === "running" && (
                    <div className="card"><b>Brief running</b> · {status.progress.message} ({status.progress.done}/{status.progress.total})
                      <div className="progress" style={{ marginTop: 6 }}><div style={{ width: `${status.progress.total ? (100 * status.progress.done) / status.progress.total : 8}%` }} /></div>
                      <div className="small muted" style={{ marginTop: 4 }}>About two minutes: each shelter is checked on Google Maps one building at a time.</div></div>
                  )}
                  <CountySnapshot ins={ins} map={map} county={countyName} />
                </>))}
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
