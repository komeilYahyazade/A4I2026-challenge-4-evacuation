import type { BriefStatus, Track } from "../types";

const MONTHS = ["O", "N", "D", "J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N"];
const PHASES = [
  { name: "Inventory", when: "Oct – Mar", text: "Who can't leave; check every shelter against Google Maps, one building at a time." },
  { name: "Plan & invest", when: "Apr – Jul", text: "Match vulnerable tracts to verified shelters within capacity; choose what to fix." },
  { name: "Readiness brief", when: "August", text: "Who can't leave, where they'd go and whether it holds up, what to fix, what we can't tell you.", on: true },
  { name: "Watch", when: "Jun – Nov · every 6 h", text: "Official NHC advisories set the phase. This tool never forecasts storms." },
  { name: "Activate", when: "~5 days to landfall", text: "Not built: Google Maps grounding is not permitted for emergency response.", off: true },
  { name: "Learn", when: "After the storm", text: "Which shelters really opened, real capacities; feeds next year's inventory." },
];

export interface BriefControls { track: Track; reachKm: number; maxVerify: number; routes: boolean }

export function Rail({ controls, setControls, onGenerate, status, busy }: {
  controls: BriefControls; setControls: (c: BriefControls) => void; onGenerate: () => void;
  status: BriefStatus | null; busy: boolean;
}) {
  const running = status?.status === "running";
  const p = status?.progress;
  return (
    <aside className="rail">
      <h3>Hurricane season</h3>
      <div className="months" aria-label="Season timeline, August highlighted">
        {MONTHS.map((m, i) => <span key={i} className={i === 10 ? "on" : ""}>{m}</span>)}
      </div>
      {PHASES.map((ph) => (
        <div key={ph.name} className={`phase${ph.on ? " on" : ""}${ph.off ? " off" : ""}`}>
          <b>{ph.name}</b><span className="when">{ph.when}</span><p>{ph.text}</p>
        </div>
      ))}

      <div className="controls">
        <h3>August brief</h3>
        <label>Track
          <select value={controls.track} onChange={(e) => setControls({ ...controls, track: e.target.value as Track })}>
            <option value="shelter_readiness">Shelter readiness</option>
            <option value="getting_people_out">Getting people out</option>
            <option value="medically_dependent">Medically dependent</option>
          </select>
        </label>
        <label>Reachable without a car: <b>{controls.reachKm} km</b> straight line
          <input type="range" min={1} max={10} step={0.5} value={controls.reachKm}
            onChange={(e) => setControls({ ...controls, reachKm: Number(e.target.value) })} />
          <span className="muted small">3 km ≈ a 40-minute walk for an older adult.</span>
        </label>
        <label>Shelters to check on Google Maps
          <div className="seg-btns">
            {[6, 12, 20].map((n) => (
              <button key={n} type="button" aria-pressed={controls.maxVerify === n} onClick={() => setControls({ ...controls, maxVerify: n })}>{n}</button>
            ))}
          </div>
          <span className="muted small">Each check is one live call (~15 s); 6 run at once.</span>
        </label>
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={controls.routes} onChange={(e) => setControls({ ...controls, routes: e.target.checked })} />
          Drive times from Maps routing
        </label>
        <button className="btn primary" onClick={onGenerate} disabled={running || busy}>{running ? "Brief running…" : "Generate brief"}</button>
        {running && p && (
          <div>
            <div className="progress"><div style={{ width: `${p.total ? (100 * p.done) / p.total : 8}%` }} /></div>
            <div className="small muted" style={{ marginTop: 4 }}>{p.message} ({p.done}/{p.total})</div>
          </div>
        )}
        {status?.status === "failed" && <div className="err">Brief failed: {status.error?.message}</div>}
      </div>
    </aside>
  );
}
