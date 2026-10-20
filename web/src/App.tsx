import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "./api";
import { CONFIG } from "./config";
import type {
  Affected, Allocation, AuditResponse, BriefStatus, County, CountyInsights, CountyMap, Health, Rehearsal, Selection,
  ShelterVerification, Step, StormTrack, View,
} from "./types";
import { EMPTY_AFFECTED } from "./types";
import { AgentDrawer } from "./components/ChatPanel";
import { Explainer, Header, type BriefControls } from "./components/Header";
import { InsightsView } from "./components/InsightsView";
import { MapView } from "./components/MapView";
import { ShelterPanel } from "./components/ShelterPanel";
import { StepPanel, whoRows } from "./components/Steps";

const params = new URLSearchParams(window.location.search);
const asStep = (v: string | null): Step => (["1", "2", "3", "4"].includes(v ?? "") ? (Number(v) as Step) : 1);

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [counties, setCounties] = useState<County[]>([]);
  const [county, setCounty] = useState(params.get("county") ?? "12071");
  const [view, setView] = useState<View>(params.get("view") === "insights" ? "insights" : "map");
  const [step, setStep] = useState<Step>(asStep(params.get("step")));
  const [controls, setControls] = useState<BriefControls>({ track: "shelter_readiness", reachKm: 3, maxVerify: 12, routes: true });
  const [map, setMap] = useState<CountyMap | null>(null);
  const [ins, setIns] = useState<CountyInsights | null>(null);
  const [alloc, setAlloc] = useState<Allocation | null>(null);
  const [storm, setStorm] = useState<StormTrack | null>(null);
  const [advisory, setAdvisory] = useState("19");
  const [rehearsal, setRehearsal] = useState<Rehearsal | null>(null);
  const [briefId, setBriefId] = useState<string | null>(params.get("brief"));
  const [status, setStatus] = useState<BriefStatus | null>(null);
  const [audit, setAudit] = useState<AuditResponse | null>(null);
  const [verifications, setVerifications] = useState<Record<string, ShelterVerification>>({});
  const [selection, setSelection] = useState<Selection>(null);
  const [highlight, setHighlight] = useState<Affected>(EMPTY_AFFECTED);
  const [chatOpen, setChatOpen] = useState(false);
  const [explain, setExplain] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const brief = status?.status === "complete" ? status.brief : null;
  const fail = (what: string) => (e: unknown) => setError(`${what}: ${(e as ApiError).message}`);

  useEffect(() => {
    api.health().then(setHealth).catch(fail("API"));
    api.counties().then((r) => setCounties(r.counties)).catch(fail("Counties"));
    api.storm().then(setStorm).catch(fail("Ian storm track"));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setMap(null); setAlloc(null); setRehearsal(null); setSelection(null); setHighlight(EMPTY_AFFECTED);
    api.countyMap(county).then(setMap).catch(fail("Map"));
    api.allocation(county).then(setAlloc).catch(fail("Seat plan"));
  }, [county]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const t = setTimeout(() => { api.insights(county, controls.reachKm).then(setIns).catch(fail("Insights")); }, 250);
    return () => clearTimeout(t);
  }, [county, controls.reachKm]); // eslint-disable-line react-hooks/exhaustive-deps

  // The rehearsal is a deterministic re-solve (no LLM, no Maps): load it when step 3 is open.
  useEffect(() => {
    if (step !== 3 || view !== "map") return;
    let live = true;
    setRehearsal(null);
    api.rehearsal(county, advisory).then((r) => live && setRehearsal(r)).catch(fail("Ian rehearsal"));
    return () => { live = false; };
  }, [step, view, county, advisory]); // eslint-disable-line react-hooks/exhaustive-deps

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
        else if (s.status === "complete") {
          if (s.brief) setControls((c) => ({ ...c, track: s.brief!.track }));
          api.audit(briefId).then(setAudit).catch(() => setAudit(null));
        }
      } catch (e) { if (!stop) { fail("Brief")(e); setBriefId(null); } }
    };
    setAudit(null);
    void tick();
    return () => { stop = true; };
  }, [briefId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const p = new URLSearchParams({ county, view, step: String(step) });
    if (briefId) p.set("brief", briefId);
    window.history.replaceState(null, "", `?${p}`);
  }, [county, view, step, briefId]);

  const generate = async () => {
    setStarting(true); setError(null);
    try {
      const r = await api.createBrief(county, controls.track, controls.reachKm, controls.maxVerify, controls.routes);
      setStatus(null); setBriefId(r.brief_id);
    } catch (e) { fail("Could not start the brief")(e); } finally { setStarting(false); }
  };
  const select = useCallback((s: Selection) => setSelection(s), []);
  const changeCounty = (fips: string) => {
    setCounty(fips);
    if (status?.brief && status.brief.county.county_fips !== fips) { setBriefId(null); setStatus(null); }
  };
  const goStep = (s: Step) => { setStep(s); setSelection(null); setHighlight(EMPTY_AFFECTED); };
  const countyName = counties.find((c) => c.county_fips === county)?.name ?? "";

  const ranks = useMemo(() => whoRows(brief, controls.track, map).rows.map((r) => ({ id: r.id, kind: r.kind, rank: r.rank, lat: r.lat, lon: r.lon })),
    [brief, controls.track, map]);
  const stepData = { step, track: controls.track, reachKm: controls.reachKm, county, countyName, map, ins, alloc, brief, briefId, audit,
    storm, rehearsal, advisory, verifications, selection };
  const actions = { goStep, select, highlight: setHighlight, setAdvisory, generate,
    onVerified: (v: ShelterVerification) => setVerifications((m) => ({ ...m, [v.shelter_id]: v })) };
  const drawer = chatOpen && <AgentDrawer county={county} briefId={brief ? briefId : null} onHighlight={setHighlight} onClose={() => setChatOpen(false)} />;

  return (
    <div className="app">
      <Header counties={counties} county={county} onCounty={changeCounty} controls={controls} setControls={setControls}
        onGenerate={generate} status={status} starting={starting} view={view} setView={setView} step={step} goStep={goStep}
        health={health} onExplain={() => setExplain(true)} chatOpen={chatOpen} toggleChat={() => setChatOpen(!chatOpen)} />
      {error && <div className="errors"><div className="error"><span>{error}</span><button className="icon-btn" aria-label="Dismiss" onClick={() => setError(null)}>✕</button></div></div>}
      {view === "insights" ? (
        <main className="layout insights">
          <div className="main scroll">
            <InsightsView ins={ins} map={map} counties={counties} county={county} brief={brief} reachKm={controls.reachKm}
              onSelectTract={(id) => { setView("map"); setStep(1); select({ kind: "tract", id }); }} onCounty={changeCounty} />
          </div>
          {drawer && <aside className="panel floating">{drawer}</aside>}
        </main>
      ) : (
        <main className="layout">
          <div className="main">
            <MapView cfg={CONFIG} data={map} brief={brief} verifications={verifications} highlight={highlight} selection={selection} onSelect={select}
              overlay={{ step, ranks, rehearsal, storm, advisory, alloc }} />
          </div>
          <aside className="panel" aria-label="Readiness brief">
            {drawer || (<>
              {selection && (
                <div className="details">
                  <header className="drawer-head"><h2>Details</h2><button className="icon-btn" aria-label="Close details" onClick={() => setSelection(null)}>✕</button></header>
                  <div className="details-body">
                    <ShelterPanel selection={selection} map={map} brief={brief} ins={ins} verifications={verifications} reachKm={controls.reachKm}
                      onVerified={actions.onVerified} onSelect={select} />
                  </div>
                </div>
              )}
              <StepPanel d={stepData} a={actions} />
            </>)}
          </aside>
        </main>
      )}
      {explain && <Explainer onClose={() => setExplain(false)} />}
    </div>
  );
}
