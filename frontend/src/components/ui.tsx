import { useEffect, useState, type ReactNode } from "react";
import type { EvidenceLabel } from "../contract";
import { useStore } from "../store";

/** "12071020801" -> "Census tract 208.01" (last six digits as NNNN.NN, leading zeros dropped). */
export function tractName(geoId?: string | null): string {
  const g = String(geoId || "");
  if (g.length < 6) return `Census tract ${g}`;
  const six = g.slice(-6);
  const whole = String(Number(six.slice(0, 4)));
  const frac = six.slice(4);
  return `Census tract ${frac === "00" ? whole : `${whole}.${frac}`}`;
}

export function n0(v: number | null | undefined): string {
  return v == null || !Number.isFinite(v) ? "—" : Math.round(v).toLocaleString();
}

const LABEL_TONE: Record<string, string> = {
  RECORDED: "chip-recorded",
  UNRECORDED: "chip-unrecorded",
  "VERIFIED LIVE": "chip-live",
  MODELED: "chip-modeled",
  "COUNTY-LEVEL": "chip-county",
  APPROXIMATE: "chip-approx",
};

/** The one evidence chip a claim may carry. Clicking it opens the shared explainer. */
export function Chip({ label, title }: { label?: EvidenceLabel | string | null; title?: string }) {
  const { d } = useStore();
  if (!label) return null;
  return (
    <button
      type="button"
      className={`chip ${LABEL_TONE[label] || ""}`}
      title={title ? `${title}. What does this mean?` : "What does this label mean?"}
      onClick={(e) => {
        e.stopPropagation();
        d({ type: "explain", on: true });
      }}
    >
      {label}
    </button>
  );
}

export const LABEL_HELP: [string, string][] = [
  ["RECORDED", "Written in a federal record (FEMA, Census, CDC, CMS). It describes what was filed, not necessarily what is true today."],
  ["UNRECORDED", "The field is blank: nobody wrote it down. This is never the same as “no”."],
  ["VERIFIED LIVE", "Checked against Google Maps during this session. The time of the check is shown."],
  ["MODELED", "A statistical estimate (CDC PLACES), not a count."],
  ["COUNTY-LEVEL", "Published per county only (HHS emPOWER). It says how many, never which block."],
  [
    "APPROXIMATE",
    "Our own estimate built on a record. People without a car = ACS households without a vehicle × persons per household (people living in group quarters excluded). Also: a shelter matched to its nearest census tract, and straight-line distance.",
  ],
];

export function Explainer() {
  const { s, d } = useStore();
  useEffect(() => {
    if (!s.explain) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && d({ type: "explain", on: false });
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [s.explain, d]);
  if (!s.explain) return null;
  return (
    <div className="modal-backdrop" onClick={() => d({ type: "explain", on: false })}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="What the evidence labels mean" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>What the evidence labels mean</h2>
          <button className="icon-btn" aria-label="Close" onClick={() => d({ type: "explain", on: false })} autoFocus>
            ✕
          </button>
        </div>
        <dl className="label-help">
          {LABEL_HELP.map(([k, v]) => (
            <div key={k}>
              <dt>
                <span className={`chip ${LABEL_TONE[k]}`}>{k}</span>
              </dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
        <p className="muted small">Distances say how they were measured: “Google Maps route” or “straight-line” (a lower bound on road distance).</p>
      </div>
    </div>
  );
}

export function Kpi({ value, label, chip, tone }: { value: ReactNode; label: string; chip?: EvidenceLabel | string; tone?: "danger" | "ok" | "warn" }) {
  return (
    <div className={`kpi ${tone ? `kpi-${tone}` : ""}`}>
      <div className="kpi-value">{value}</div>
      <div className="kpi-label">{label}</div>
      {chip && <Chip label={chip} />}
    </div>
  );
}

/** Collapsible evidence. Anything Maps-derived inside carries its own sources inside too. */
export function Disclosure({ summary = "Evidence", children }: { summary?: string; children: ReactNode }) {
  return (
    <details className="disclosure" onClick={(e) => e.stopPropagation()}>
      <summary>{summary}</summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}

/** Top `n` visible, "Show all" for the rest; expands itself when a hidden item is selected from the map. */
export function TopList<T>({
  items,
  n = 5,
  render,
  keyOf,
  selectedKey,
  empty = "Nothing to show.",
}: {
  items: T[];
  n?: number;
  render: (item: T, i: number) => ReactNode;
  keyOf: (item: T) => string;
  selectedKey?: string | null;
  empty?: string;
}) {
  const [all, setAll] = useState(false);
  useEffect(() => {
    if (!selectedKey) return;
    const idx = items.findIndex((x) => keyOf(x) === selectedKey);
    if (idx >= n) setAll(true);
  }, [selectedKey, items, n, keyOf]);
  if (!items.length) return <p className="muted small">{empty}</p>;
  const shown = all ? items : items.slice(0, n);
  return (
    <>
      <ol className="list">{shown.map((x, i) => <li key={keyOf(x)}>{render(x, i)}</li>)}</ol>
      {items.length > n && (
        <button className="link-btn" onClick={() => setAll(!all)}>
          {all ? "Show top " + n : `Show all ${items.length}`}
        </button>
      )}
    </>
  );
}

export function Severity({ level }: { level?: string }) {
  const l = (level || "MEDIUM").toUpperCase();
  const glyph = l === "HIGH" ? "▲" : l === "MEDIUM" ? "◆" : "●";
  return (
    <span className={`sev sev-${l.toLowerCase()}`}>
      <span aria-hidden>{glyph}</span> {l === "HIGH" ? "High" : l === "MEDIUM" ? "Medium" : "Low"}
    </span>
  );
}

export function humanType(t: string): string {
  const map: Record<string, string> = {
    NAME_SAYS_INACTIVE: "FEMA's own name says inactive",
    SUCCESSOR_AT_ADDRESS: "Another organisation is at this address now",
    NOT_FOUND: "Google Maps finds nothing at this address",
    CLOSED: "Permanently closed",
    PRE_LANDFALL_IN_SURGE_ZONE: "Pre-landfall shelter inside the surge zone",
    PRE_LANDFALL_FLOOD_EXPOSED_SITE: "Site in a flood-exposed neighbourhood",
    NO_WALKABLE_DESTINATION: "No shelter within walking distance",
    GENERATOR_GAP: "Power-dependent residents, no generator shelter",
    CARE_FACILITY_FAR_FROM_GENERATOR: "Nursing home far from a generator shelter",
    CAPACITY_IMPLAUSIBLE: "Implausible capacity on file",
    CAPACITY_UNRECORDED_EXCLUDED: "Capacity not recorded",
    CAPACITY_IMPLAUSIBLE_EXCLUDED: "Implausible capacity excluded",
    UNASSIGNED_DEMAND: "People with no shelter seat",
    ASSESS_ASSIGNED_SHELTER: "Assess wheelchair access where the plan sends people",
    ASSIGNED_SHELTER_IN_SURGE_ZONE: "Plan sends people into the surge zone",
    OUTREACH_FOR_MUST_LEAVE: "Outreach for residents who must leave",
    ACCESSIBILITY_CONFLICT: "FEMA and Google Maps disagree on access",
    UNASSESSED_ACCESSIBILITY: "Nobody has assessed wheelchair access",
    TEMPORARILY_CLOSED: "Temporarily closed",
    UNCONFIRMED: "Google Maps could not confirm it",
  };
  return map[t] || t.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
}
