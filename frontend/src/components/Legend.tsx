import { useState, type ReactNode } from "react";

export type LegendKey =
  | "tractNeed"
  | "tractRank"
  | "tractMust"
  | "nursing"
  | "confirmed"
  | "renamed"
  | "conflict"
  | "dropped"
  | "offline"
  | "recYes"
  | "recNo"
  | "unrec"
  | "inactive"
  | "context"
  | "line"
  | "reassigned"
  | "oldRun"
  | "cone"
  | "track"
  | "fix"
  | "site"
  | "unassigned";

const ITEMS: Record<LegendKey, [ReactNode, string]> = {
  tractNeed: [<span className="lg-circle lg-tract" />, "Neighbourhood; size = households without a car"],
  tractRank: [<span className="m-rank lg-small">1</span>, "Top-ranked neighbourhood (number = rank)"],
  tractMust: [<span className="lg-circle lg-must" />, "Must leave, may need help"],
  nursing: [<span className="m-nursing lg-small">+</span>, "Nursing home (CMS)"],
  confirmed: [<span className="m-confirmed lg-small"><span>✓</span></span>, "Confirmed on Google Maps"],
  renamed: [<span className="m-renamed lg-small"><span>↻</span></span>, "Confirmed, now under another name"],
  conflict: [<span className="m-conflict lg-small"><span>!</span></span>, "Checked, still unconfirmed"],
  dropped: [<span className="m-dropped lg-small"><span>✕</span></span>, "Dropped after the live check"],
  offline: [<span className="m-offline lg-small"><span>✕</span></span>, "Offline in the rehearsal"],
  recYes: [<span className="m-recYes" />, "Wheelchair access recorded yes"],
  recNo: [<span className="m-recNo" />, "Wheelchair access recorded no"],
  unrec: [<span className="m-unrec" />, "Wheelchair access not recorded (not NO)"],
  inactive: [<span className="m-tag lg-tag">INACTIVE</span>, "FEMA's own name says inactive"],
  context: [<span className="m-dot m-context" />, "Other shelters"],
  line: [<span className="lg-line lg-accent" />, "Planned pickup run (hover a card to highlight)"],
  reassigned: [<span className="lg-line lg-warn" />, "Run re-routed in the rehearsal"],
  oldRun: [<span className="lg-line lg-dashed" />, "Run it replaces"],
  cone: [<span className="lg-cone" />, "NHC forecast cone"],
  track: [<span className="lg-line lg-text" />, "Forecast track"],
  fix: [<span className="m-fix sev-high lg-small">▲</span>, "High-priority fix (select any fix to pin it)"],
  site: [<span className="m-site lg-small">★</span>, "Where one new site seats the most people"],
  unassigned: [<span className="lg-circle lg-danger" />, "People with no shelter seat within 15 km"],
};

/** Lists exactly the symbols on the map right now. */
export function Legend({ keys }: { keys: LegendKey[] }) {
  const [open, setOpen] = useState(true);
  if (!keys.length) return null;
  return (
    <div className={`legend ${open ? "" : "closed"}`} aria-label="Map legend">
      <button className="legend-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "Legend ▾" : "Legend ▸"}
      </button>
      {open && (
        <ul>
          {keys.map((k) => (
            <li key={k}>
              <span className="lg-sym">{ITEMS[k][0]}</span>
              {ITEMS[k][1]}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
