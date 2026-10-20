import { useStore, type Phase } from "../store";

const PHASES: { id: Phase; name: string; when: string; what: string; disabled?: string }[] = [
  { id: "inventory", name: "Inventory", when: "Oct – Mar", what: "Who can't leave; check every shelter against Google Maps, one building at a time." },
  { id: "plan", name: "Plan & invest", when: "Apr – Jul", what: "Match vulnerable tracts to verified shelters within capacity; list what to fix." },
  { id: "brief", name: "Readiness brief", when: "August", what: "Who can't leave, where they'd go and whether it holds up, what to fix, what we can't tell you." },
  { id: "watch", name: "Watch", when: "Jun – Nov · every 6 h", what: "Official NHC advisories set the phase. Tabletop rehearsal replays Hurricane Ian." },
  {
    id: "activate",
    name: "Activate",
    when: "~5 days to landfall",
    what: "Run the pre-agreed playbook from county-reported shelter status.",
    disabled: "Not built: Maps grounding is not permitted for emergency response",
  },
  { id: "learn", name: "Learn", when: "After the storm", what: "After-action: which shelters really opened, real capacities, fed into next year's inventory." },
];

export function Timeline() {
  const { s, d } = useStore();
  return (
    <nav className="timeline" aria-label="Season phases">
      <div className="tl-title">Hurricane season</div>
      <div className="tl-months" aria-hidden>
        {["O", "N", "D", "J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N"].map((m, i) => (
          <span key={i} className={i === 10 ? "now" : ""}>
            {m}
          </span>
        ))}
      </div>
      <ol>
        {PHASES.map((p) => (
          <li key={p.id}>
            <button
              className={`tl-phase ${s.phase === p.id ? "active" : ""} ${p.disabled ? "disabled" : ""}`}
              disabled={!!p.disabled}
              aria-disabled={!!p.disabled}
              title={p.disabled || p.what}
              onClick={() => !p.disabled && d({ type: "phase", phase: p.id })}
            >
              <span className="tl-name">{p.name}</span>
              <span className="tl-when">{p.when}</span>
              <span className="tl-what">{p.disabled || p.what}</span>
            </button>
          </li>
        ))}
      </ol>
      <p className="tl-foot">
        Storm Watch consumes the official National Hurricane Center forecast. It does not predict storms.
      </p>
    </nav>
  );
}
