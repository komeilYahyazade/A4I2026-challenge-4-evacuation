import { useEffect } from "react";
import { BriefPanel } from "./components/BriefPanel";
import { ChatPanel } from "./components/ChatPanel";
import { MapView } from "./components/MapView";
import { Timeline } from "./components/Timeline";
import { MOCK_ONLY } from "./config";
import { FIXTURE_NOTE } from "./mock/lee";
import { useStore } from "./store";

export function App() {
  const { s, d, act } = useStore();
  useEffect(() => {
    void act.loadBase();
  }, [act]);
  const anyMock = Object.values(s.sources).includes("mock");
  const busy = s.busy.brief || s.busy.verify || s.busy.rehearsal || s.busy.allocation || s.busy.map;
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Lee County Readiness Console</h1>
          <p>
            Where verified, reachable shelters and para-transit runs are needed for residents who can't drive, before the September peak.
          </p>
        </div>
        <div className="beats" role="group" aria-label="Demo">
          <button className={`btn beat ${s.phase === "brief" && !s.rehearsalMode ? "active" : ""}`} onClick={() => act.briefBeat()} disabled={!!s.busy.brief || !!s.busy.verify}>
            1 · August brief
          </button>
          <button className={`btn beat ${s.rehearsalMode ? "active" : ""}`} onClick={() => act.rehearsalBeat()} disabled={!!s.busy.rehearsal}>
            2 · Ian rehearsal
          </button>
          <button className={`btn beat ${s.phase === "plan" ? "active" : ""}`} onClick={() => act.fixBeat()}>
            3 · Fix list
          </button>
        </div>
        <div className="status">
          {anyMock && (
            <span className="mock-badge big" title={FIXTURE_NOTE}>
              MOCK DATA{MOCK_ONLY ? " · mock mode" : " · live call failed"}
            </span>
          )}
          {!anyMock && Object.keys(s.sources).length > 0 && <span className="live-badge big">LIVE · readiness-agent</span>}
          {busy && <span className="spinner" aria-label="Loading" />}
          {s.busy.verify && <span className="small">Checking Google Maps…</span>}
          {s.busy.brief && !s.busy.verify && <span className="small">Building the brief from BigQuery (~1 min)…</span>}
        </div>
      </header>
      {s.rehearsalMode && <div className="rehearsal-strip">Tabletop rehearsal: no Google Maps grounding. Replaying Hurricane Ian (2022) from NHC advisories.</div>}
      <div className="errors" aria-live="polite">
        {s.errors.map((e) => (
          <div key={e.id} className="error" onClick={() => d({ type: "dismiss", id: e.id })}>
            {e.text} <small>(click to dismiss)</small>
          </div>
        ))}
      </div>
      <main className="layout">
        <Timeline />
        <MapView />
        <aside className="panel">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={s.tab === "brief"} className={s.tab === "brief" ? "active" : ""} onClick={() => d({ type: "tab", tab: "brief" })}>
              Brief
            </button>
            <button role="tab" aria-selected={s.tab === "chat"} className={s.tab === "chat" ? "active" : ""} onClick={() => d({ type: "tab", tab: "chat" })}>
              Chat{s.busy.chat ? " ⋯" : ""}
            </button>
          </div>
          <div className="tab-body">{s.tab === "brief" ? <BriefPanel /> : <ChatPanel />}</div>
        </aside>
      </main>
    </div>
  );
}
