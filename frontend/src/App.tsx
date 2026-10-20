import { useEffect } from "react";
import { ChatPanel } from "./components/ChatPanel";
import { MapView } from "./components/MapView";
import { StepPanel } from "./components/StepPanel";
import { VoiceBar, useVoiceAvailable } from "./components/VoiceBar";
import { Explainer } from "./components/ui";
import { CONFIG, MOCK_ONLY } from "./config";
import { FIXTURE_NOTE } from "./mock/lee";
import { useStore, type Step } from "./store";

const FL_COUNTIES: { fips: string; name: string }[] = [
  { fips: "12071", name: "Lee County" },
  { fips: "12086", name: "Miami-Dade County" },
  { fips: "12011", name: "Broward County" },
  { fips: "12057", name: "Hillsborough County" },
  { fips: "12103", name: "Pinellas County" },
  { fips: "12095", name: "Orange County" },
  { fips: "12087", name: "Monroe County" },
  { fips: "12031", name: "Duval County" },
];

const STEPS: { n: Step; label: string }[] = [
  { n: 1, label: "Who can't leave" },
  { n: 2, label: "Where they'd go" },
  { n: 3, label: "Hurricane Ian rehearsal" },
  { n: 4, label: "What to fix" },
];

export function App() {
  const { s, d, act } = useStore();
  const voice = useVoiceAvailable();
  useEffect(() => {
    void act.loadBase();
  }, [act]);
  const anyMock = Object.values(s.sources).includes("mock");
  const busy = s.busy.brief || s.busy.verify || s.busy.rehearsal || s.busy.allocation || s.busy.map;
  const countyName = s.map?.county?.name || s.brief?.county?.name || FL_COUNTIES.find((c) => c.fips === s.countyFips)?.name || `County ${s.countyFips}`;
  return (
    <div className="app">
      {CONFIG.publicDemo && (
        <div className="public-banner" role="note">
          A planning tool for county emergency managers. Not a list of open shelters and not for use during an emergency. Follow your county
          emergency management office; in danger, call 911.
        </div>
      )}
      <header className="topbar">
        <div className="brand">
          <h1>
            {countyName.replace(/ County$/, "")} County <span className="muted">· evacuation readiness</span>
          </h1>
          <select aria-label="County" value={s.countyFips} onChange={(e) => void act.setCounty(e.target.value)} disabled={!!busy} className="select">
            {FL_COUNTIES.map((c) => (
              <option key={c.fips} value={c.fips}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <nav className="stepper" aria-label="Readiness brief steps">
          <ol>
            {STEPS.map((st) => (
              <li key={st.n}>
                <button
                  className={`step ${s.step === st.n ? "current" : ""} ${s.step > st.n ? "done" : ""}`}
                  aria-current={s.step === st.n ? "step" : undefined}
                  onClick={() => void act.goStep(st.n)}
                >
                  <span className="step-num">{st.n}</span>
                  <span className="step-label">{st.label}</span>
                </button>
              </li>
            ))}
          </ol>
        </nav>
        <div className="status">
          {anyMock && (
            <span className="mock-badge" title={FIXTURE_NOTE}>
              MOCK DATA{MOCK_ONLY ? "" : " · live call failed"}
            </span>
          )}
          {!anyMock && Object.keys(s.sources).length > 0 && <span className="live-badge">● Live data</span>}
          {busy && <span className="spinner" aria-label="Loading" />}
          <button className="btn btn-ghost btn-icon" onClick={() => d({ type: "explain", on: true })} aria-label="What the evidence labels mean" title="What the evidence labels mean">
            ?
          </button>
          <button className={`btn ${s.chatOpen ? "btn-primary" : ""}`} onClick={() => d({ type: "chat", open: !s.chatOpen })} aria-expanded={s.chatOpen}>
            Ask the agent{s.busy.chat ? " ⋯" : ""}
          </button>
        </div>
      </header>
      <SeasonStrip step={s.step} />
      {s.errors.length > 0 && (
        <div className="errors" aria-live="polite">
          {s.errors.map((e) => (
            <div key={e.id} className="error">
              <span>{e.text}</span>
              <button className="icon-btn" aria-label="Dismiss" onClick={() => d({ type: "dismiss", id: e.id })}>
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
      <main className="layout">
        <MapView />
        <aside className="panel" aria-label="Readiness brief">
          <StepPanel />
          {s.chatOpen && (
            <section className="chat-drawer" aria-label="Ask the agent">
              <header className="drawer-head">
                <h2>Ask the agent</h2>
                <button className="icon-btn" aria-label="Close chat" onClick={() => d({ type: "chat", open: false })}>
                  ✕
                </button>
              </header>
              {voice && <VoiceBar />}
              <ChatPanel />
            </section>
          )}
        </aside>
      </main>
      <Explainer />
    </div>
  );
}

const SEASON: { id: string; label: string; when: string; steps: Step[]; disabled?: string }[] = [
  { id: "inventory", label: "Inventory", when: "Oct–Mar", steps: [] },
  { id: "plan", label: "Plan & invest", when: "Apr–Jul", steps: [4] },
  { id: "brief", label: "Readiness brief", when: "August", steps: [1, 2] },
  { id: "watch", label: "Watch", when: "Jun–Nov", steps: [3] },
  { id: "activate", label: "Activate", when: "~5 days out", steps: [], disabled: "Not built: Maps grounding is not permitted for emergency response" },
  { id: "learn", label: "Learn", when: "After the storm", steps: [] },
];

/** Context, not navigation: where in the season this step lives. */
function SeasonStrip({ step }: { step: Step }) {
  return (
    <div className="season" aria-label="Where this sits in the hurricane season">
      <span className="season-title">Season</span>
      {SEASON.map((p) => (
        <span
          key={p.id}
          className={`season-seg ${p.steps.includes(step) ? "on" : ""} ${p.disabled ? "off" : ""}`}
          title={p.disabled || `${p.label}, ${p.when}`}
        >
          <b>{p.label}</b> <span className="muted">{p.when}</span>
          {p.disabled && <span className="sr-only">. {p.disabled}</span>}
        </span>
      ))}
    </div>
  );
}
