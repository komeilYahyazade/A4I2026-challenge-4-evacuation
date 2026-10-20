import { useMemo, useState } from "react";
import type { Affected, Brief, CountyMap, FixCategory, Selection } from "../types";
import { tractLabel } from "../format";
import { Segments } from "./Attribution";

const CAT: Record<FixCategory, { label: string; icon: string }> = {
  no_accessible_destination: { label: "No accessible destination", icon: "♿" },
  unassessed_access: { label: "Access never assessed", icon: "?" },
  generator_gap: { label: "Generator gap", icon: "⚡" },
  shelter_in_surge_zone: { label: "Shelter in surge zone", icon: "≋" },
  record_mismatch: { label: "Record vs Google Maps", icon: "≠" },
  medical_needs_gap: { label: "Medical-needs capacity", icon: "✚" },
  field_assessment: { label: "Operator says no access", icon: "☎" },
};

export function FixList({ brief, map, onHighlight, onSelect }: {
  brief: Brief | null; map: CountyMap | null; onHighlight: (a: Affected) => void; onSelect: (s: Selection) => void;
}) {
  const [cat, setCat] = useState<FixCategory | "all">("all");
  const names = useMemo(() => ({
    shelter: new Map((map?.shelters ?? []).map((s) => [s.shelter_id, s.name])),
    care: new Map((map?.care_facilities ?? []).map((c) => [c.ccn, c.name])),
  }), [map]);
  if (!brief) return <div className="empty">The fix list comes from a brief. Press <b>Generate brief</b> on the left (about two minutes).</div>;
  const counts = brief.fixes.reduce<Record<string, number>>((m, f) => ({ ...m, [f.category]: (m[f.category] ?? 0) + 1 }), {});
  const fixes = brief.fixes.filter((f) => cat === "all" || f.category === cat);
  return (
    <div>
      <h2 style={{ fontSize: 17 }}>What to fix before September</h2>
      <div className="muted small">Each fix is tied to the tracts, shelters and facilities it affects. Priorities from the brief; ids come from tool results only.</div>
      <div className="filters">
        <button aria-pressed={cat === "all"} onClick={() => setCat("all")}>All ({brief.fixes.length})</button>
        {(Object.keys(CAT) as FixCategory[]).filter((k) => counts[k]).map((k) => (
          <button key={k} aria-pressed={cat === k} onClick={() => setCat(k)}>{CAT[k].icon} {CAT[k].label} ({counts[k]})</button>
        ))}
      </div>
      {fixes.map((f) => {
        const a = f.affected;
        const n = a.tracts.length + a.shelters.length + a.care_facilities.length;
        return (
          <div key={f.priority} className="card">
            <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <span className="badge" style={{ background: "var(--surface-2)", color: "var(--text)" }}>#{f.priority}</span>
              <span className="small muted">{CAT[f.category].icon} {CAT[f.category].label}</span>
            </div>
            <div style={{ fontWeight: 700, margin: "4px 0" }}>{f.title}</div>
            <div>{f.recommendation}</div>
            {n > 0 && (
              <div style={{ marginTop: 6 }}>
                {a.tracts.map((t) => <button key={t} className="chip" style={{ cursor: "pointer", background: "none" }} onClick={() => onSelect({ kind: "tract", id: t })}>▦ {tractLabel(t)}</button>)}
                {a.shelters.map((s) => <button key={s} className="chip" style={{ cursor: "pointer", background: "none" }} onClick={() => onSelect({ kind: "shelter", id: s })}>⌂ {names.shelter.get(s) ?? s}</button>)}
                {a.care_facilities.map((c) => <button key={c} className="chip" style={{ cursor: "pointer", background: "none" }} onClick={() => onSelect({ kind: "care", id: c })}>H {names.care.get(c) ?? c}</button>)}
              </div>
            )}
            {f.source_ids.length > 0 && <Segments segments={[{ text: "Evidence from the live check:", source_ids: f.source_ids, freshness: "live" }]} sources={brief.sources} />}
            {n > 0 && <button className="btn small" onClick={() => onHighlight(a)}>Show {n} on map</button>}
          </div>
        );
      })}
    </div>
  );
}
