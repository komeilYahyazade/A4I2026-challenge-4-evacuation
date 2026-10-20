import { useState } from "react";
import type { BriefStatus, County, Health, Step, Track, View } from "../types";
import { TRACKS } from "../tracks";
import { STEPS } from "./Steps";

export interface BriefControls { track: Track; reachKm: number; maxVerify: number; routes: boolean }

export function Header({ counties, county, onCounty, controls, setControls, onGenerate, status, starting, view, setView,
  step, goStep, health, onExplain, chatOpen, toggleChat }: {
  counties: County[]; county: string; onCounty: (f: string) => void; controls: BriefControls; setControls: (c: BriefControls) => void;
  onGenerate: () => void; status: BriefStatus | null; starting: boolean; view: View; setView: (v: View) => void;
  step: Step; goStep: (s: Step) => void; health: Health | null; onExplain: () => void; chatOpen: boolean; toggleChat: () => void;
}) {
  const [settings, setSettings] = useState(false);
  const running = status?.status === "running";
  const p = status?.progress;
  const name = counties.find((c) => c.county_fips === county)?.name ?? "County";
  return (
    <>
      <div className="public-banner" role="note">
        A planning tool for county emergency managers. Not a list of open shelters and not for use during an emergency. Follow your county
        emergency management office; in danger, call 911.
      </div>
      <header className="topbar">
        <div className="brand">
          <h1>{name.replace(/ County$/, "")} County <span className="muted">· evacuation readiness</span></h1>
          <div className="brand-controls">
            <select className="select" aria-label="County" value={county} onChange={(e) => onCounty(e.target.value)} disabled={running}>
              {[...counties].sort((a, b) => a.rank - b.rank).map((c) => <option key={c.county_fips} value={c.county_fips}>#{c.rank} {c.name}</option>)}
            </select>
            <select className="select" aria-label="Brief track" value={controls.track} title={TRACKS[controls.track].question}
              onChange={(e) => setControls({ ...controls, track: e.target.value as Track })} disabled={running}>
              {(Object.keys(TRACKS) as Track[]).map((t) => <option key={t} value={t}>{TRACKS[t].label}</option>)}
            </select>
            <button className="btn primary" onClick={onGenerate} disabled={running || starting}>{running ? "Brief running…" : "Generate brief"}</button>
            <div className="popover-wrap">
              <button className="btn btn-icon" aria-label="Brief settings" aria-expanded={settings} onClick={() => setSettings(!settings)}>⚙</button>
              {settings && (
                <div className="popover" role="dialog" aria-label="Brief settings">
                  <label>Reachable without a car: <b>{controls.reachKm} km</b> straight line
                    <input type="range" min={1} max={10} step={0.5} value={controls.reachKm} onChange={(e) => setControls({ ...controls, reachKm: Number(e.target.value) })} />
                    <span className="muted small">3 km ≈ a 40-minute walk for an older adult.</span>
                  </label>
                  <label>Shelters to check on Google Maps
                    <div className="seg-btns">{[6, 12, 20].map((n) => (
                      <button key={n} type="button" aria-pressed={controls.maxVerify === n} onClick={() => setControls({ ...controls, maxVerify: n })}>{n}</button>))}</div>
                    <span className="muted small">Each check is one live call (~15 s); 6 run at once. 6 roughly halves the brief time.</span>
                  </label>
                  <label className="row"><input type="checkbox" checked={controls.routes} onChange={(e) => setControls({ ...controls, routes: e.target.checked })} /> Drive times from Maps routing</label>
                </div>
              )}
            </div>
          </div>
        </div>
        <nav className="stepper" aria-label="Readiness brief steps">
          <ol>
            {STEPS.map((st) => (
              <li key={st.n}>
                <button className={`step${view === "map" && step === st.n ? " current" : ""}${step > st.n ? " done" : ""}`}
                  aria-current={view === "map" && step === st.n ? "step" : undefined} onClick={() => { setView("map"); goStep(st.n); }}>
                  <span className="step-num">{st.n}</span><span className="step-label">{st.label}</span>
                </button>
              </li>
            ))}
          </ol>
        </nav>
        <div className="status">
          {health && <span className={health.mode === "live" ? "live-badge" : "mock-badge"} title={`${health.model} · ${health.dataset}`}>{health.mode === "live" ? "● Live data" : "MOCK DATA"}</span>}
          <button className="btn btn-ghost btn-icon" onClick={onExplain} aria-label="What the evidence labels mean" title="What the evidence labels mean">?</button>
          <button className={`btn${chatOpen ? " primary" : ""}`} onClick={toggleChat} aria-expanded={chatOpen}>Ask the agent</button>
        </div>
      </header>
      <div className="subbar">
        <div className="view-toggle" role="tablist" aria-label="View">
          <button role="tab" aria-selected={view === "map"} className={view === "map" ? "active" : ""} onClick={() => setView("map")}>🗺 Map</button>
          <button role="tab" aria-selected={view === "insights"} className={view === "insights" ? "active" : ""} onClick={() => setView("insights")}>📊 Insights</button>
        </div>
        <div className="subbar-status">
          <span className="muted small"><b>{TRACKS[controls.track].label}:</b> {TRACKS[controls.track].question}</span>
          {running && p && (
            <span className="brief-progress"><span className="progress"><span style={{ width: `${p.total ? (100 * p.done) / p.total : 8}%` }} /></span>
              <span className="small">{p.message} ({p.done}/{p.total})</span></span>
          )}
          {status?.status === "complete" && status.brief && <span className="small ok-text">✓ Brief ready · {TRACKS[status.brief.track].label}</span>}
          {status?.status === "failed" && <span className="small err">Brief failed: {status.error?.message}</span>}
        </div>
      </div>
    </>
  );
}

export function Explainer({ onClose }: { onClose: () => void }) {
  const rows: [string, string, string][] = [
    ["recorded", "RECORDED", "A federal record (FEMA, ACS, CDC SVI, CMS, HHS). Historical: it says what was filed, not what is true today."],
    ["unrecorded", "NOT RECORDED", "Nobody filled the field in. Not recorded is not no."],
    ["approximate", "APPROXIMATE", "Derived from records with a stated rule (people = households × persons per household; flood exposure of the nearest tract)."],
    ["modeled", "MODELED", "CDC PLACES modeled prevalence, not a count of people."],
    ["county", "COUNTY-LEVEL", "Published per county only (HHS emPOWER): how many, never which block."],
    ["live", "VERIFIED LIVE", "Checked on Google Maps just now, with the time and the sources shown under the claim."],
  ];
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" role="dialog" aria-label="Evidence labels" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head"><h2>What the labels mean</h2><button className="icon-btn" onClick={onClose} aria-label="Close">✕</button></header>
        {rows.map(([k, label, text]) => <div key={k} className="explain-row"><span className={`tag ${k}`}>{label}</span><span>{text}</span></div>)}
        <p className="small muted">Distances are straight-line km unless a drive time came back from Google Maps routing. Planning only: Google Maps grounding is not used for emergency response.</p>
      </div>
    </div>
  );
}
