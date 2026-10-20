import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type { Brief, CountyInsights, CountyMap, FieldAssessment, Selection, Shelter, ShelterDetail, ShelterVerification } from "../types";
import { VSTATUS, evidence, fmt, haversineKm, hhmm, km, tractLabel } from "../format";
import { AssessPanel, FieldAssessmentCard } from "./AssessPanel";
import { FlagChip, GoogleMaps, Segments, Tag } from "./Attribution";

function Nearby({ from, shelters, limit = 8, filter, onSelect, verifications }: {
  from: { lat: number; lon: number }; shelters: Shelter[]; limit?: number; filter?: (s: Shelter) => boolean;
  onSelect: (s: Selection) => void; verifications: Record<string, ShelterVerification>;
}) {
  const rows = shelters.filter(filter ?? (() => true)).map((s) => ({ s, d: haversineKm(from, s) })).sort((a, b) => a.d - b.d).slice(0, limit);
  return (
    <table className="data">
      <thead><tr><th>Shelter</th><th className="r">km</th><th>Wheelchair</th><th>Use</th></tr></thead>
      <tbody>
        {rows.map(({ s, d }) => {
          const ev = evidence(s, verifications[s.shelter_id]);
          return (
            <tr key={s.shelter_id} style={{ cursor: "pointer" }} onClick={() => onSelect({ kind: "shelter", id: s.shelter_id })}>
              <td>{s.name}</td><td className="r">{fmt(d, 1)}</td>
              <td><span className={`pin ${ev.cls}`} style={{ display: "inline-grid", width: 15, height: 15, fontSize: 9, verticalAlign: -3 }}>{ev.glyph}</span> {s.wheelchair_accessible === "unrecorded" ? "Not recorded" : s.wheelchair_accessible}</td>
              <td>{s.usage ?? "–"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function ShelterPanel({ selection, map, brief, ins, verifications, onVerified, onSelect, reachKm, onAssessed }: {
  selection: Selection; map: CountyMap | null; brief: Brief | null; ins: CountyInsights | null;
  verifications: Record<string, ShelterVerification>; onVerified: (v: ShelterVerification) => void;
  onSelect: (s: Selection) => void; reachKm: number; onAssessed?: (a: FieldAssessment) => void;
}) {
  const [detail, setDetail] = useState<ShelterDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [origin, setOrigin] = useState<string>("");
  const sid = selection?.kind === "shelter" ? selection.id : null;

  useEffect(() => {
    setDetail(null); setErr(null);
    if (!sid) return;
    api.shelter(sid).then(setDetail).catch((e: ApiError) => setErr(e.message));
  }, [sid]);

  const dest = brief?.destinations.find((d) => d.shelter.shelter_id === sid) ?? null;
  const originOptions = useMemo(() => {
    if (!map || !detail) return [];
    const served = dest?.serves ?? [];
    const near = [...map.tracts].map((t) => ({ t, d: haversineKm(detail, t) })).sort((a, b) => a.d - b.d).slice(0, 6).map((x) => x.t.geo_id);
    return [...new Set([...served, ...near])];
  }, [map, detail, dest]);
  useEffect(() => { setOrigin(originOptions[0] ?? ""); }, [originOptions]);

  if (!selection) return <div className="empty">Click a shelter, tract or nursing home on the map, or a card in the brief.</div>;

  if (selection.kind === "tract") {
    const t = map?.tracts.find((x) => x.geo_id === selection.id);
    if (!t || !map) return <div className="empty">Tract {selection.id} is not in this county's map.</div>;
    const s = ins?.baseline.state;
    return (
      <div>
        <h2 style={{ fontSize: 17 }}>{tractLabel(t.geo_id)}</h2>
        <div className="muted small">{t.geo_id} · {fmt(t.total_pop)} people · need score {fmt(t.need_score, 3)} · centroid, not a boundary</div>
        <table className="data" style={{ marginTop: 10 }}>
          <thead><tr><th>Measure</th><th className="r">Tract</th><th className="r">State</th><th>Kind</th></tr></thead>
          <tbody>
            <tr><td>Households without a car</td><td className="r">{fmt(t.pct_no_vehicle, 1)}%</td><td className="r">{fmt(s?.pct_no_vehicle, 1)}%</td><td><Tag kind="recorded" /></td></tr>
            <tr><td>Aged 65+</td><td className="r">{fmt(t.pct_age_65_plus, 1)}%</td><td className="r">{fmt(s?.pct_age_65_plus, 1)}%</td><td><Tag kind="recorded" /></td></tr>
            <tr><td>With a disability</td><td className="r">{fmt(t.pct_disability, 1)}%</td><td className="r">{fmt(s?.pct_disability, 1)}%</td><td><Tag kind="recorded" /></td></tr>
            <tr><td>Mobility disability</td><td className="r">{fmt(t.pct_mobility, 1)}%</td><td className="r">{fmt(s?.pct_mobility, 1)}%</td><td><Tag kind="modeled" /></td></tr>
            <tr><td>Mobile homes</td><td className="r">{fmt(t.mobile_homes)}</td><td className="r">–</td><td><Tag kind="recorded" /></td></tr>
          </tbody>
        </table>
        <div className="insight">{fmt(t.households_no_vehicle)} households own no car (ownership, not lacking a ride). Nearest shelter {km(t.nearest_shelter_km)}; nearest recorded as wheelchair accessible {km(t.nearest_wheelchair_yes_km)} (straight line).</div>
        <div className="section-title">Shelters within reach of this tract</div>
        <Nearby from={t} shelters={map.shelters} onSelect={onSelect} verifications={verifications} />
        <div className="muted small" style={{ marginTop: 4 }}>Straight-line km from the tract centroid; {reachKm} km counts as reachable without a car.</div>
      </div>
    );
  }

  if (selection.kind === "care") {
    const c = map?.care_facilities.find((x) => x.ccn === selection.id);
    if (!c || !map) return <div className="empty">Facility {selection.id} is not in this county's map.</div>;
    return (
      <div>
        <h2 style={{ fontSize: 17 }}>{c.name}</h2>
        <div className="muted small">Nursing home · CCN {c.ccn} · {fmt(c.certified_beds)} certified beds <Tag kind="recorded" /></div>
        <div className="insight">Nearest shelter with a generator recorded as yes: {km(c.nearest_generator_yes_km)} (straight line).
          {(c.nearest_generator_yes_km ?? 99) > 10 && " That is beyond 10 km: arrange a generator-equipped receiving site or a transfer agreement."}</div>
        <div className="section-title">Nearest shelters with a recorded generator</div>
        <Nearby from={c} shelters={map.shelters} filter={(s) => s.generator_onsite === "yes"} onSelect={onSelect} verifications={verifications} />
        <div className="muted small" style={{ marginTop: 4 }}>Only shelters in this county; a recorded "yes" is paperwork, so confirm on site.</div>
      </div>
    );
  }

  const v = (sid && verifications[sid]) || dest?.verification || detail?.last_verification || null;
  const st = v ? VSTATUS[v.status] : null;
  const run = async () => {
    if (!sid) return;
    setBusy(true); setErr(null);
    try { onVerified(await api.verify(sid, origin || null)); } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  };
  return (
    <div>
      {err && <div className="err">{err}</div>}
      {!detail ? <div className="muted">Loading shelter…</div> : (<>
        <h2 style={{ fontSize: 17 }}>{detail.name}</h2>
        <div className="muted small">{detail.address} · shelter {detail.shelter_id}</div>
        <div className="card" style={{ marginTop: 10 }}>
          <b>FEMA record</b> <span className="fresh">historical</span>
          <div style={{ margin: "6px 0" }}>
            <FlagChip label="Wheelchair" value={detail.wheelchair_accessible} /><FlagChip label="Generator" value={detail.generator_onsite} />
            <FlagChip label="Surge zone" value={detail.in_surge_slosh_area} /><FlagChip label="100-yr floodplain" value={detail.in_100_yr_floodplain} />
          </div>
          <dl className="kv">
            <dt>Use</dt><dd>{detail.usage ?? "not recorded"} {detail.usage === "EVAC" ? "(before landfall)" : detail.usage === "POST" ? "(after impact)" : ""}</dd>
            <dt>Capacity</dt><dd>{detail.evacuation_capacity ?? "not recorded"} evacuation · {detail.post_impact_capacity ?? "not recorded"} post-impact</dd>
            <dt>Medical needs</dt><dd>{detail.is_medical ? "yes" : "no"} · population {detail.population_code ?? "–"}</dd>
            <dt>Pets</dt><dd>{detail.pet_accommodations_code ?? "–"}</dd>
            <dt>FEMA status</dt><dd>{detail.shelter_status_code ?? "–"} <span className="muted small">(a historical registry, not operational status)</span></dd>
          </dl>
        </div>
        {detail.field_assessment && <FieldAssessmentCard a={detail.field_assessment} />}
        {!detail.field_assessment && <div className="muted small" style={{ marginTop: 6 }}>Nobody has assessed whether a wheelchair user can get in here.</div>}
        <AssessPanel key={detail.shelter_id} shelterId={detail.shelter_id}
          onSaved={(a) => { setDetail((d) => (d ? { ...d, field_assessment: a } : d)); onAssessed?.(a); }} />
        <div className="card">
          <b><GoogleMaps /></b> {v ? <span className="fresh live">checked {hhmm(v.checked_at)}</span> : <span className="fresh">not checked yet</span>}
          {v && st && (<>
            <div style={{ margin: "6px 0" }}><span className={`verdict ${st.cls}`}>{st.icon} {st.label}</span>{" "}
              {v.maps_name && <b>{v.maps_name}</b>} {v.place_type && <span className="muted">· {v.place_type}</span>}</div>
            <dl className="kv">
              <dt>Wheelchair entrance</dt><dd>{v.wheelchair.maps.replace("_", " ")} · record {v.wheelchair.record === "unrecorded" ? "not recorded" : v.wheelchair.record} · <b>{v.wheelchair.agreement.replace("_", " ")}</b></dd>
              <dt>Open now</dt><dd>{v.open_now == null ? "hours unknown" : v.open_now ? "open" : "closed"} <span className="muted small">(business hours, not shelter status)</span></dd>
              {v.drive && <><dt>Drive</dt><dd>{fmt(v.drive.drive_minutes, 0)} min{v.drive.drive_km ? `, ${fmt(v.drive.drive_km, 1)} km` : ""} from {v.drive.from_geo_id}</dd></>}
            </dl>
            <Segments segments={v.summary} sources={v.sources} checkedAt={v.checked_at} />
          </>)}
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginTop: 6 }}>
            <select value={origin} onChange={(e) => setOrigin(e.target.value)} style={{ maxWidth: 220 }}>
              <option value="">No drive time</option>
              {originOptions.map((g) => <option key={g} value={g}>Drive from {tractLabel(g)}</option>)}
            </select>
            <button className="btn primary" onClick={run} disabled={busy}>{busy ? "Asking Google Maps…" : v ? "Check again" : "Check on Google Maps"}</button>
          </div>
          <div className="muted small" style={{ marginTop: 4 }}>One named building at one address: the only question shape Maps answers well. Only the place_id is stored.</div>
        </div>
        {map && (<>
          <div className="section-title">Tracts this shelter is closest to</div>
          <table className="data"><thead><tr><th>Tract</th><th className="r">km</th><th className="r">No-car households</th></tr></thead>
            <tbody>{[...map.tracts].map((t) => ({ t, d: haversineKm(detail, t) })).sort((a, b) => a.d - b.d).slice(0, 5).map(({ t, d }) => (
              <tr key={t.geo_id} style={{ cursor: "pointer" }} onClick={() => onSelect({ kind: "tract", id: t.geo_id })}>
                <td>{tractLabel(t.geo_id)}</td><td className="r">{fmt(d, 1)}</td><td className="r">{fmt(t.households_no_vehicle)}</td></tr>))}</tbody></table>
        </>)}
      </>)}
    </div>
  );
}
