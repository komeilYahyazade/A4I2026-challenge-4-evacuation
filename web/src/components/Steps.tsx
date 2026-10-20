// The four-step readiness flow (layout from main's StepPanel): 1 who can't leave, 2 where they'd go,
// 3 Hurricane Ian rehearsal, 4 what to fix. Every number comes from an API payload; every live claim
// carries its Google Maps sources directly under it.
import { useMemo, useState, type ReactNode } from "react";
import { api, ApiError } from "../api";
import type {
  Affected, Allocation, AuditResponse, Brief, CountyInsights, CountyMap, Driver, Rehearsal, Selection,
  ShelterVerification, Step, StormTrack, Tract, Track,
} from "../types";
import { VERDICT, VSTATUS, fmt, hhmm, km, reachClass, tractLabel } from "../format";
import { TRACKS } from "../tracks";
import { FlagChip, GoogleMaps, Segments, Tag, type TagKind } from "./Attribution";
import { AuditBars } from "./Charts";
import { FixList } from "./FixList";
import { exportBrief } from "../export";

export const STEPS: { n: Step; label: string; question: string }[] = [
  { n: 1, label: "Who can't leave", question: "Where do the people who cannot drive themselves out live?" },
  { n: 2, label: "Where they'd go", question: "Is there a shelter they can reach, and does it hold up when we check it live?" },
  { n: 3, label: "Hurricane Ian rehearsal", question: "Replay Ian's forecast: which shelters drop out, and who loses a seat?" },
  { n: 4, label: "What to fix", question: "What should the county fix before the September peak?" },
];

export interface StepData {
  step: Step; track: Track; reachKm: number; county: string; countyName: string;
  map: CountyMap | null; ins: CountyInsights | null; alloc: Allocation | null; brief: Brief | null; briefId: string | null;
  audit: AuditResponse | null; storm: StormTrack | null; rehearsal: Rehearsal | null; advisory: string;
  verifications: Record<string, ShelterVerification>; selection: Selection;
}
export interface StepActions {
  goStep: (s: Step) => void; select: (s: Selection) => void; highlight: (a: Affected) => void;
  setAdvisory: (a: string) => void; onVerified: (v: ShelterVerification) => void; generate: () => void;
}

function Kpi({ value, label, chip, tone }: { value: ReactNode; label: ReactNode; chip?: TagKind; tone?: "danger" | "ok" | "warn" }) {
  return (
    <div className={`kpi${tone ? " " + tone : ""}`}>
      <div className="v">{value}</div>
      <div className="l">{label}</div>
      {chip && <div style={{ marginTop: 4 }}><Tag kind={chip} /></div>}
    </div>
  );
}
const Takeaway = ({ children }: { children: ReactNode }) => <p className="takeaway">{children}</p>;
const Loading = ({ what }: { what: string }) => <div className="loading" role="status"><span className="spinner" aria-hidden /> {what}</div>;

function BriefNeeded({ what, onGenerate }: { what: string; onGenerate: () => void }) {
  return (
    <div className="callout">
      {what} <button className="btn small primary" onClick={onGenerate}>Generate brief</button>
      <div className="small muted" style={{ marginTop: 4 }}>About two minutes: shelters are checked on <GoogleMaps /> one building at a time.</div>
    </div>
  );
}

export function StepPanel({ d, a }: { d: StepData; a: StepActions }) {
  const meta = STEPS[d.step - 1];
  return (
    <div className="panel-body">
      <header className="step-head">
        <div className="step-kicker">Step {d.step} of 4</div>
        <h2>{meta.label}</h2>
        <p className="muted">{meta.question}</p>
      </header>
      {d.brief && d.brief.track !== d.track && d.step !== 3 && (
        <div className="callout small">This brief was generated for <b>{TRACKS[d.brief.track].label}</b>. Generate again to see <b>{TRACKS[d.track].label}</b>.</div>
      )}
      {d.step === 1 && <Step1 d={d} a={a} />}
      {d.step === 2 && <Step2 d={d} a={a} />}
      {d.step === 3 && <Step3 d={d} a={a} />}
      {d.step === 4 && <Step4 d={d} a={a} />}
      {d.step < 4 && <button className="btn primary next" onClick={() => a.goStep((d.step + 1) as Step)}>Next: {STEPS[d.step].label} →</button>}
    </div>
  );
}

// ---- step 1 ------------------------------------------------------------------------------------

function driverText(dr: Driver, ins: CountyInsights | null): string {
  const s = ins?.baseline.state, v = dr.value;
  switch (dr.metric) {
    case "households_no_vehicle": return `${fmt(v)} households without a car`;
    case "pct_no_vehicle": return `${fmt(v, 1)}% of households without a car${s?.pct_no_vehicle != null ? ` (state ${fmt(s.pct_no_vehicle, 1)}%)` : ""}`;
    case "pop_65_plus": return `${fmt(v)} people aged 65+`;
    case "pct_disability": return `${fmt(v, 1)}% with a disability${s?.pct_disability != null ? ` (state ${fmt(s.pct_disability, 1)}%)` : ""}`;
    case "mobile_homes": return `${fmt(v)} mobile homes`;
    case "group_quarters_pop": return `${fmt(v)} people in group quarters`;
    case "nearest_wheelchair_yes_km": return `nearest shelter recorded as wheelchair accessible: ${km(v)} straight line`;
    case "certified_beds": return `${fmt(v)} certified beds`;
    case "nearest_generator_yes_km": return `nearest shelter with a recorded generator: ${km(v)} straight line`;
    default: return `${dr.metric.replace(/_/g, " ")}: ${fmt(v, 1)} ${dr.unit}`;
  }
}

/** Who-can't-leave rows: the brief's, or (before a brief) the track's ranking of the map data. */
export function whoRows(brief: Brief | null, track: Track, map: CountyMap | null) {
  if (brief && brief.track === track) return { source: "brief" as const, rows: brief.who_cannot_leave };
  const tracts = [...(map?.tracts ?? [])].sort((x, y) => track === "getting_people_out"
    ? (y.households_no_vehicle ?? 0) - (x.households_no_vehicle ?? 0) : (y.need_score ?? 0) - (x.need_score ?? 0));
  const care = [...(map?.care_facilities ?? [])].filter((c) => (c.nearest_generator_yes_km ?? 99) > 10)
    .sort((x, y) => (y.certified_beds ?? 0) - (x.certified_beds ?? 0));
  const nT = track === "medically_dependent" ? 2 : track === "getting_people_out" ? 6 : 5;
  const nC = track === "medically_dependent" ? 6 : track === "getting_people_out" ? 0 : 3;
  const tr = tracts.slice(0, nT).map((t) => ({ rank: 0, kind: "tract" as const, id: t.geo_id, label: tractLabel(t.geo_id), lat: t.lat, lon: t.lon,
    people: t.total_pop, drivers: [] as Driver[], reason: "" }));
  const cr = care.slice(0, nC).map((c) => ({ rank: 0, kind: "care_facility" as const, id: c.ccn, label: c.name, lat: c.lat, lon: c.lon,
    people: c.certified_beds, drivers: [] as Driver[], reason: "" }));
  const rows = (track === "medically_dependent" ? [...cr, ...tr] : [...tr, ...cr]).map((r, i) => ({ ...r, rank: i + 1 }));
  return { source: "ranking" as const, rows };
}

function useWho(d: StepData) {
  return useMemo(() => whoRows(d.brief, d.track, d.map), [d.brief, d.track, d.map]);
}

function Step1({ d, a }: { d: StepData; a: StepActions }) {
  const who = useWho(d);
  const [list, setList] = useState<"tract" | "care_facility">(d.track === "medically_dependent" ? "care_facility" : "tract");
  const tracts = useMemo(() => new Map((d.map?.tracts ?? []).map((t) => [t.geo_id, t])), [d.map]);
  const care = useMemo(() => new Map((d.map?.care_facilities ?? []).map((c) => [c.ccn, c])), [d.map]);
  const seats = useMemo(() => new Map((d.alloc?.tracts ?? []).map((t) => [t.geo_id, t])), [d.alloc]);
  const t = d.alloc?.totals;
  if (!d.map) return <Loading what="Loading the county from BigQuery…" />;
  const rows = who.rows.filter((r) => r.kind === list);
  const nCare = who.rows.filter((r) => r.kind === "care_facility").length;
  return (
    <>
      <div className="kpis">
        <Kpi value={fmt(t?.demand_persons)} label={`people in households without a car (from ${fmt(d.ins?.access.county.households_no_vehicle)} households, a Census count)`} chip="approximate" />
        <Kpi value={fmt(t?.usable_capacity)} label="recorded pre-landfall shelter spaces in reach (15 nearest shelters per tract, any county)" chip="recorded" />
        <Kpi value={fmt(t?.unassigned_persons)} label="with no shelter seat within 15 km" chip="approximate" tone="danger" />
      </div>
      {t && <Takeaway>{fmt(t.unassigned_persons)} of {fmt(t.demand_persons)} people without a car have no shelter seat within 15 km, even with every recorded space filled nearest-first. Track <b>{TRACKS[d.track].label}</b>: {TRACKS[d.track].question}</Takeaway>}
      {!t && <Loading what="Matching people to shelter seats…" />}
      <div className="seg-toggle" role="tablist">
        <button role="tab" aria-selected={list === "tract"} className={list === "tract" ? "active" : ""} onClick={() => setList("tract")}>● Can't drive ({who.rows.length - nCare})</button>
        <button role="tab" aria-selected={list === "care_facility"} className={list === "care_facility" ? "active must" : ""} onClick={() => setList("care_facility")}>◆ Nursing homes ({nCare})</button>
      </div>
      {who.source === "ranking" && <div className="small muted" style={{ marginBottom: 6 }}>From the county data, ranked for this track. Generate a brief for reasons and live checks.</div>}
      {rows.map((w) => {
        const tr: Tract | undefined = tracts.get(w.id);
        const cf = care.get(w.id);
        const st = seats.get(w.id);
        const cls = tr ? reachClass(tr, d.reachKm) : "unknown";
        const sel = d.selection?.id === w.id;
        return (
          <article key={w.id} className={`card clickable${sel ? " selected" : ""}`} onClick={() => a.select({ kind: w.kind === "tract" ? "tract" : "care", id: w.id })}>
            <div className="card-title"><span className={`rank${w.kind === "care_facility" ? " rank-must" : ""}`}>{w.rank}</span> {w.kind === "tract" ? w.label : (cf?.name ?? w.label)}</div>
            {tr && <div className="card-line"><b>{fmt(tr.households_no_vehicle)}</b> households without a car ({fmt(tr.pct_no_vehicle, 0)}%) <Tag kind="recorded" title="Census ACS vehicle ownership" /></div>}
            {cf && <div className="card-line"><b>{fmt(cf.certified_beds)}</b> certified beds · nearest generator-recorded shelter {km(cf.nearest_generator_yes_km)} <Tag kind="recorded" title="CMS, FEMA" /></div>}
            {st && st.unassigned_persons > 0 && <div className="card-line danger"><b>{fmt(st.unassigned_persons)}</b> people with no seat within 15 km <Tag kind="approximate" /></div>}
            {w.reason && <p className="card-sub">{w.reason}</p>}
            {tr && cls !== "unknown" && (
              <p className="card-sub">{cls === "walkable"
                ? <><b className="walk">WALKABLE</b> · nearest shelter {km(tr.nearest_shelter_km)} straight line. Plan a ride for residents who cannot walk that far.</>
                : <><b className="pickup">PICKUP NEEDED</b> · nearest shelter {km(tr.nearest_shelter_km)} straight line: no shelter within {d.reachKm} km on foot. Stage a pickup point.</>}</p>
            )}
            <details onClick={(e) => e.stopPropagation()}>
              <summary className="small">Evidence</summary>
              <ul className="evidence-list">
                {w.drivers.map((dr) => <li key={dr.metric}>{driverText(dr, d.ins)} <Tag kind={dr.source === "cdc_places" ? "modeled" : "recorded"} /></li>)}
                {tr && !w.drivers.length && <>
                  <li>{fmt(tr.pop_65_plus)} people aged 65+ ({fmt(tr.pct_age_65_plus, 0)}%) <Tag kind="recorded" /></li>
                  <li>{fmt(tr.pct_disability, 0)}% with a disability (state {fmt(d.ins?.baseline.state.pct_disability, 1)}%) <Tag kind="recorded" /></li>
                  <li>{fmt(tr.mobile_homes)} mobile homes <Tag kind="recorded" /></li>
                </>}
                {tr?.pct_mobility != null && <li>Mobility disability {fmt(tr.pct_mobility, 0)}% <Tag kind="modeled" /></li>}
                {tr && <li className="muted">Census GEOID {tr.geo_id} · need score {fmt(tr.need_score, 3)}</li>}
              </ul>
            </details>
          </article>
        );
      })}
      <details className="card">
        <summary>Why this ranking</summary>
        <p className="small">{TRACKS[d.track].who}</p>
        <p className="small muted">Physical and demographic inputs only: no accessibility field, no race. Race appears only in the equity audit (step 4). Car ownership (ACS) is not the same as having a ride.</p>
      </details>
      {d.ins?.power.power_dependent_dme != null && (
        <p className="note"><b>{fmt(d.ins.power.power_dependent_dme)}</b> Medicare beneficiaries depend on electricity for medical equipment. HHS publishes this per county, so we can say how many but never which block. <Tag kind="county" /></p>
      )}
    </>
  );
}

// ---- step 2 ------------------------------------------------------------------------------------

function Step2({ d, a }: { d: StepData; a: StepActions }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const who = useWho(d);
  const evac = d.ins?.flags.find((f) => f.scope === "evac" && f.flag === "wheelchair_accessible");
  const checked = Object.keys(d.verifications).length + (d.brief?.destinations.filter((x) => x.verification).length ?? 0);
  const shelterInfo = useMemo(() => new Map((d.alloc?.shelters ?? []).map((s) => [s.shelter_id, s])), [d.alloc]);
  const plan = useMemo(() => {  // before a brief: the shelters the seat plan sends each top tract to
    const byTract = new Map<string, { shelter_id: string; persons: number; km: number }[]>();
    for (const x of d.alloc?.assignments ?? []) {
      const arr = byTract.get(x.geo_id) ?? [];
      arr.push({ shelter_id: x.shelter_id, persons: x.persons, km: x.straight_line_km });
      byTract.set(x.geo_id, arr);
    }
    return who.rows.filter((r) => r.kind === "tract").map((r) => ({ geo: r.id, label: r.label,
      shelters: (byTract.get(r.id) ?? []).sort((x, y) => y.persons - x.persons).slice(0, 2) }));
  }, [d.alloc, who]);
  const verify = async (sid: string, geo: string | null) => {
    setBusy(sid); setErr(null);
    try { a.onVerified(await api.verify(sid, geo)); } catch (e) { setErr((e as ApiError).message); } finally { setBusy(null); }
  };
  const brief = d.brief && d.brief.track === d.track ? d.brief : null;
  return (
    <>
      <div className="kpis">
        <Kpi value={fmt(d.ins?.shelters.evac)} label="shelters designated for use before landfall" chip="recorded" />
        <Kpi value={evac ? fmt(evac.unrecorded) : "–"} label={evac ? `with wheelchair access not recorded (${evac.yes + evac.no} known)` : "wheelchair access"} chip="unrecorded" tone="warn" />
        <Kpi value={fmt(checked)} label="checked on Google Maps this session" chip="live" tone="ok" />
      </div>
      <Takeaway>{evac ? `${fmt(evac.unrecorded)} of ${fmt(evac.yes + evac.no + evac.unrecorded)}` : "Most"} pre-landfall shelters have no wheelchair record, and the federal file is a historical registry. So we check named buildings on <GoogleMaps /> one at a time, and say plainly what nobody has recorded.</Takeaway>
      {err && <div className="err">{err}</div>}
      {brief ? <BriefDestinations brief={brief} d={d} a={a} /> : (<>
        <BriefNeeded what="No brief yet: these are the shelters the seat plan sends each top tract to." onGenerate={a.generate} />
        {plan.map((p) => (
          <div key={p.geo}>
            <h3 className="list-title">{p.label} <span className="muted small">{p.geo}</span></h3>
            {!p.shelters.length && <div className="small muted">No pre-landfall seat within 15 km in the plan.</div>}
            {p.shelters.map((x) => {
              const s = shelterInfo.get(x.shelter_id);
              const v = d.verifications[x.shelter_id];
              return (
                <article key={x.shelter_id} className={`card clickable${d.selection?.id === x.shelter_id ? " selected" : ""}`} onClick={() => a.select({ kind: "shelter", id: x.shelter_id })}>
                  <div className="card-title">{s?.name ?? x.shelter_id}</div>
                  <div className="card-line muted">{fmt(x.persons)} people sent here by the plan · {km(x.km)} straight line · capacity {s?.capacity ?? "not recorded"}</div>
                  {s && <div><FlagChip label="Wheelchair" value={s.wheelchair_accessible} /><FlagChip label="Generator" value={s.generator_onsite} /><FlagChip label="Surge zone" value={s.in_surge_slosh_area} /></div>}
                  {v ? <VerificationBlock v={v} /> : (
                    <button className="btn small" disabled={busy === x.shelter_id} onClick={(e) => { e.stopPropagation(); void verify(x.shelter_id, p.geo); }}>
                      {busy === x.shelter_id ? "Checking on Google Maps…" : "Check on Google Maps"}
                    </button>)}
                </article>
              );
            })}
          </div>
        ))}
      </>)}
    </>
  );
}

function VerificationBlock({ v }: { v: ShelterVerification }) {
  const st = VSTATUS[v.status];
  return (
    <div className="live-block">
      <span className={`verdict ${st.cls}`}>{st.icon} {st.label}</span>{" "}
      <span className="small">{v.maps_name && v.maps_name !== v.record_name ? <>now <b>{v.maps_name}</b></> : null} {v.place_type && <span className="muted">· {v.place_type}</span>}</span>
      {v.drive?.drive_minutes != null && <div className="small"><b>{fmt(v.drive.drive_minutes, 0)} min drive</b> (<GoogleMaps /> routing, typical traffic, not evacuation traffic)</div>}
      <Segments segments={v.summary} sources={v.sources} checkedAt={v.checked_at} />
    </div>
  );
}

/** "Tract 803, Tract 5.02" or "4 nursing homes" (medically dependent destinations serve facilities). */
function servesText(serves: string[]): string {
  const tracts = serves.filter((g) => !g.startsWith("ccn:")).map(tractLabel);
  const homes = serves.length - tracts.length;
  return [...tracts, ...(homes ? [`${homes} nursing home${homes > 1 ? "s" : ""}`] : [])].join(", ");
}

function BriefDestinations({ brief, d, a }: { brief: Brief; d: StepData; a: StepActions }) {
  const checked = brief.destinations.filter((x) => x.verification);
  const by = (s: string) => checked.filter((x) => x.verification?.status === s).length;
  const blanks = checked.filter((x) => x.verification?.wheelchair.agreement === "record_blank").length;
  const dropped = brief.destinations.filter((x) => x.verdict === "do_not_rely");
  return (
    <>
      <div className={`card${dropped.length ? " alert" : ""}`}>
        <b>What <GoogleMaps /> changed</b> <Tag kind="live" />
        <div className="kpis four">
          <Kpi value={by("confirmed")} label="confirmed" />
          <Kpi value={by("renamed")} label="renamed" />
          <Kpi value={by("closed") + by("not_found")} label="closed or missing" tone={by("closed") + by("not_found") ? "danger" : undefined} />
          <Kpi value={`${blanks}/${checked.filter((x) => x.shelter.wheelchair_accessible === "unrecorded").length}`} label="blank wheelchair records Maps answered" />
        </div>
        {dropped.map((x) => <div key={x.shelter.shelter_id} className="small">✕ <b>{x.shelter.name}</b> dropped{x.verification ? `: ${VSTATUS[x.verification.status].label.toLowerCase()}` : ""}{x.serves.length ? ` · was an option for ${servesText(x.serves)}` : ""}</div>)}
        {blanks > 0 && <div className="small muted">A Maps "accessible entrance" fills a blank record but does not close it: those stay verify before season.</div>}
      </div>
      {brief.destinations.map((x) => {
        const s = x.shelter, v = x.verification, vd = VERDICT[x.verdict];
        return (
          <article key={s.shelter_id} className={`card clickable${d.selection?.id === s.shelter_id ? " selected" : ""}${x.verdict === "do_not_rely" ? " dropped" : ""}`} onClick={() => a.select({ kind: "shelter", id: s.shelter_id })}>
            <div className="card-title"><span className={`verdict ${vd.cls}`}>{vd.icon} {vd.label}</span> {s.name}</div>
            <div className="card-line muted">{s.address} · {s.usage ?? "usage n/a"} · capacity {s.evacuation_capacity ?? "not recorded"}{s.is_medical ? " · medical-needs" : ""}</div>
            <div className="small">{km(x.straight_line_km)} straight line{x.serves.length > 0 && <> · serves {servesText(x.serves)}</>}</div>
            <div><FlagChip label="Wheelchair" value={s.wheelchair_accessible} /><FlagChip label="Generator" value={s.generator_onsite} /><FlagChip label="Surge zone" value={s.in_surge_slosh_area} /><FlagChip label="Floodplain" value={s.in_100_yr_floodplain} /></div>
            {v && <VerificationBlock v={v} />}
            <div className="small" style={{ fontWeight: 600, marginTop: 4 }}>Why this verdict</div>
            <Segments segments={x.verdict_reason} sources={brief.sources} checkedAt={v?.checked_at} />
          </article>
        );
      })}
    </>
  );
}

// ---- step 3 ------------------------------------------------------------------------------------

function Step3({ d, a }: { d: StepData; a: StepActions }) {
  const r = d.rehearsal;
  const advs = d.storm?.advisories ?? [];
  const idx = Math.max(0, advs.findIndex((x) => x.advisory === d.advisory));
  const cur = advs[idx];
  return (
    <>
      <div className="rehearsal-banner" role="note">Tabletop rehearsal: no Google Maps grounding. Planning drill, never a live emergency.</div>
      {advs.length > 0 && (
        <div className="card">
          <label className="small"><b>NHC advisory {cur?.advisory}</b>{cur?.hours_before_landfall != null ? ` · ${Math.round(cur.hours_before_landfall)} h before landfall` : ""}{cur?.issued_at ? ` · issued ${new Date(cur.issued_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC` : ""}
            <input type="range" min={0} max={advs.length - 1} value={idx} onChange={(e) => a.setAdvisory(advs[Number(e.target.value)].advisory)} aria-label="NHC advisory" style={{ width: "100%" }} />
          </label>
          <div className="small muted">Slide through Ian's advisories: earlier cones miss Fort Myers, which is the drill's point. Decisions that take days cannot wait for the cone.</div>
        </div>
      )}
      {!r ? <Loading what="Taking shelters offline and re-solving the seat plan…" /> : (<>
        <div className="kpis">
          <Kpi value={r.scenario.hours_before_landfall != null ? `T−${Math.round(r.scenario.hours_before_landfall)} h` : "–"} label={`to landfall, NHC advisory ${r.scenario.advisory}`} chip="recorded" />
          <Kpi value={fmt(r.shelters_down.length)} label={`shelters offline · ${fmt(r.capacity_lost)} spaces lost`} chip="approximate" tone="warn" />
          <Kpi value={<>{fmt(r.before.unassigned_persons)} → {fmt(r.after.unassigned_persons)}</>} label="people with no shelter seat" chip="approximate" tone="danger" />
        </div>
        <Takeaway>If Ian's cone and coastal flooding take {r.shelters_down.length} in-plan shelters offline, <b>{fmt(r.after.unassigned_persons - r.before.unassigned_persons)}</b> more people lose a seat and {fmt(r.reassigned_total)} pickup assignments must be re-routed. {r.at_risk_total} shelters serving the county meet the at-risk rule at this advisory.</Takeaway>
        <h3 className="list-title">Taken offline</h3>
        {r.shelters_down.length === 0 && <div className="small muted">No in-plan shelter meets the rule at this advisory.</div>}
        {r.shelters_down.map((x) => (
          <article key={x.shelter_id} className={`card clickable${d.selection?.id === x.shelter_id ? " selected" : ""}`} onClick={() => a.select({ kind: "shelter", id: x.shelter_id })}>
            <div className="card-title"><span className="shape-tri" aria-hidden>▲</span> {x.name}</div>
            <div className="card-line">{x.reason === "IN_SURGE_ZONE" ? "Recorded inside the storm-surge zone" : "Inside the Ian cone, in a flood-exposed neighbourhood"} <Tag kind={x.reason === "IN_SURGE_ZONE" ? "recorded" : "approximate"} /> · {fmt(x.capacity)} spaces</div>
            <p className="card-sub">{x.evidence}</p>
          </article>
        ))}
        {r.at_risk_not_in_plan.length > 0 && (
          <div className="callout"><b>{r.at_risk_not_in_plan.length} at-risk shelters have no capacity on file</b> ({r.at_risk_not_in_plan.map((x) => x.name).join(", ")}), so losing them moves nobody in the plan. That is a paperwork gap, not safety.</div>
        )}
        {r.newly_unassigned.length > 0 && (<>
          <h3 className="list-title">Newly without a shelter seat</h3>
          {r.newly_unassigned.slice(0, 8).map((u) => (
            <article key={u.geo_id} className={`card clickable${d.selection?.id === u.geo_id ? " selected" : ""}`} onClick={() => a.select({ kind: "tract", id: u.geo_id })}>
              <div className="card-title">{tractLabel(u.geo_id)}</div>
              <div className="card-line"><b className="danger">{fmt(u.persons)}</b> people lose their seat <Tag kind="approximate" title="households × persons per household" /></div>
            </article>
          ))}
        </>)}
        <p className="small muted">Forecast cones: NOAA National Hurricane Center (public domain). The re-solve uses recorded data only, as a real activation would. Map: cone, forecast track, shelters taken offline (▲) and re-routed pickups.</p>
      </>)}
    </>
  );
}

// ---- step 4 ------------------------------------------------------------------------------------

function Step4({ d, a }: { d: StepData; a: StepActions }) {
  const t = d.alloc?.totals;
  const opts = d.alloc?.site_options ?? [];
  const brief = d.brief;
  return (
    <>
      <div className="kpis">
        <Kpi value={fmt(t?.unassigned_persons)} label="people with no shelter seat within 15 km" chip="approximate" tone="danger" />
        <Kpi value={fmt(opts[0]?.persons_covered)} label="seated by one new site in the right place" chip="approximate" tone="ok" />
        <Kpi value={brief ? fmt(brief.fixes.length) : "–"} label="fixes before the season, from the brief" chip="recorded" tone="warn" />
      </div>
      {opts[0] && t && <Takeaway>One designated site near {tractLabel(opts[0].geo_id)} would seat <b>{fmt(opts[0].persons_covered)}</b> of the {fmt(t.unassigned_persons)} people who have no seat today. Start there, then work down the fix list.</Takeaway>}
      {opts.length > 0 && (<>
        <h3 className="list-title">What one site buys</h3>
        {opts.map((o, i) => (
          <article key={o.geo_id} className={`card clickable site${d.selection?.id === o.geo_id ? " selected" : ""}`}
            onClick={() => { a.select({ kind: "tract", id: o.geo_id }); a.highlight({ tracts: o.tracts_covered, shelters: [], care_facilities: [] }); }}>
            <div className="card-title"><span className="star" aria-hidden>★</span> {i === 0 ? "Designate one site here" : `Next site #${i + 1}`}</div>
            <div className="card-line">Seats <b>{fmt(o.persons_covered)}</b> more people without a car from {o.tracts_covered.length} neighbourhoods <Tag kind="approximate" /></div>
            <p className="card-sub">Centred on {tractLabel(o.geo_id)}: a public school or civic building outside the surge area, then verified on <GoogleMaps /> and in person. Seats needed = people covered; it says how big the site must be, not that one exists.</p>
          </article>
        ))}
      </>)}
      <h3 className="list-title">Fix list</h3>
      {brief ? <FixList brief={brief} map={d.map} onHighlight={a.highlight} onSelect={a.select} /> : <BriefNeeded what="The fix list comes from a brief." onGenerate={a.generate} />}
      {brief && (<>
        <details className="card" open>
          <summary>What we could not tell you ({brief.unknowns.length})</summary>
          {brief.unknowns.map((u) => (
            <div key={u.topic} className="small" style={{ margin: "6px 0" }}><b>{u.topic}</b>: {u.count == null ? "not known" : <b>{fmt(u.count)}{u.of != null ? ` of ${fmt(u.of)}` : ""} {u.unit}</b>}
              <div>{u.why}</div><div className="muted">How to close it: {u.how_to_close}</div></div>
          ))}
        </details>
        <details className="card">
          <summary>Equity audit</summary>
          {d.audit ? <><div className="small muted">{d.audit.compares}. Did the plan land where people cannot leave, or where somebody filled in the form?</div><AuditBars rows={d.audit.rows} /></> : <div className="small muted">Loading audit…</div>}
        </details>
        <div className="small muted">Brief generated {hhmm(brief.generated_at)} · {TRACKS[brief.track].label} · {brief.model}</div>
        <a className="btn" style={{ marginTop: 8, display: "inline-block" }} download={`readiness_brief_${brief.county.county_fips}.json`}
          href={`data:application/json;charset=utf-8,${encodeURIComponent(exportBrief(brief))}`}>Download brief (FEMA data + place_id only)</a>
      </>)}
    </>
  );
}
