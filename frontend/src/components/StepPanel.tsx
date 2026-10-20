import { useEffect, useMemo, useRef, useState } from "react";
import type { FixItem, SiteOption, TractEntry, WhereRow } from "../contract";
import { ago, basisCode, coords, existenceOf, inGeometry, shelterName } from "../derive";
import { useStore, type Step } from "../store";
import { shelterViews } from "../views";
import { PlanApproval } from "./BriefPanel";
import { MapsAttribution, protectGM } from "./MapsAttribution";
import { Chip, Disclosure, Kpi, Severity, TopList, humanType, n0, tractName } from "./ui";

const STEP_TITLE: Record<Step, [string, string]> = {
  1: ["Who can't leave", "Where do the people who cannot drive themselves out live?"],
  2: ["Where they'd go", "Is there a shelter they can reach, and does it hold up when we check it live?"],
  3: ["Hurricane Ian rehearsal", "Replay Ian's forecast: which shelters drop out, and who loses a seat?"],
  4: ["What to fix", "What should the county fix before the September peak?"],
};

export function StepPanel() {
  const { s, act } = useStore();
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => bodyRef.current?.scrollTo({ top: 0 }), [s.step]);
  // Selection from the map scrolls its card into view.
  useEffect(() => {
    if (s.selected?.from !== "map") return;
    const t = setTimeout(() => document.getElementById(`card-${s.selected!.kind}-${s.selected!.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 60);
    return () => clearTimeout(t);
  }, [s.selected]);
  const [title, question] = STEP_TITLE[s.step];
  return (
    <div className="panel-body" ref={bodyRef}>
      <header className="step-head">
        <div className="step-kicker">Step {s.step} of 4</div>
        <h2>{title}</h2>
        <p className="muted">{question}</p>
      </header>
      {s.step === 1 && <Step1 />}
      {s.step === 2 && <Step2 />}
      {s.step === 3 && <Step3 />}
      {s.step === 4 && <Step4 />}
      {s.step < 4 && (
        <button className="btn btn-primary next" onClick={() => void act.goStep((s.step + 1) as Step)}>
          Next: {STEP_TITLE[(s.step + 1) as Step][0]} →
        </button>
      )}
    </div>
  );
}

function Loading({ what }: { what: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden /> {what}
    </div>
  );
}

function useSelect() {
  const { s, d, act } = useStore();
  return (kind: "shelter" | "tract" | "fix", id: string, at: { lat: number; lng: number } | null, zoom = 13) => {
    d({ type: "select", sel: { kind, id, from: "list" } });
    act.focusOn(at, zoom);
    void s;
  };
}

// ---- Step 1 --------------------------------------------------------------------------------

function Step1() {
  const { s, d } = useStore();
  const select = useSelect();
  const [list, setList] = useState<"drive" | "must">("drive");
  const b = s.brief;
  const t = s.allocation?.totals;
  const cs = b?.county_shelters;
  if (!b && !t) return <Loading what="Loading the county from BigQuery (about a minute on first load)…" />;
  const must = b?.must_leave_may_need_help || [];
  const rows: TractEntry[] = (list === "must" && must.length ? must : b?.who_cannot_leave) || [];
  const seats = cs?.pre_landfall_capacity_plausible ?? t?.usable_capacity;
  // RECORDED: ACS households without a vehicle. The person count above is derived from it, so it is APPROXIMATE.
  const noCarHouseholds = (s.map?.tracts || []).reduce((sum, x) => sum + (x.households_no_vehicle || 0), 0) || null;
  const change = typeof b?.assumptions?.ranking_change === "string" ? (b!.assumptions!.ranking_change as string) : null;
  return (
    <>
      <div className="kpis">
        <Kpi
          value={n0(t?.demand_persons)}
          label={`people in households without a car (estimated from ${n0(noCarHouseholds)} households, a Census count)`}
          chip="APPROXIMATE"
        />
        <Kpi value={n0(seats)} label="recorded shelter spaces before landfall" chip="RECORDED" />
        <Kpi value={n0(t?.unassigned_persons)} label="with no shelter seat within 15 km" chip="APPROXIMATE" tone="danger" />
      </div>
      {t?.unassigned_persons != null && t?.demand_persons != null && (
        <p className="takeaway">
          {n0(t.unassigned_persons)} of {n0(t.demand_persons)} people without a car have no shelter seat within 15 km, even with every recorded
          space filled. These neighbourhoods hold the most of them.
        </p>
      )}
      {must.length > 0 && (
        <div className="seg-toggle" role="tablist" aria-label="Which list">
          <button role="tab" aria-selected={list === "drive"} className={list === "drive" ? "active" : ""} onClick={() => setList("drive")}>
            ● Can't drive
          </button>
          <button role="tab" aria-selected={list === "must"} className={list === "must" ? "active must" : ""} onClick={() => setList("must")}>
            ◆ Must leave, may need help
          </button>
        </div>
      )}
      <TopList
        items={rows}
        keyOf={(r) => r.geo_id}
        selectedKey={s.selected?.kind === "tract" ? s.selected.id : null}
        render={(r) => (
          <article
            id={`card-tract-${r.geo_id}`}
            className={`card ${s.selected?.kind === "tract" && s.selected.id === r.geo_id ? "selected" : ""}`}
            onClick={() => select("tract", r.geo_id, coords(r), 13)}
            onMouseEnter={() => d({ type: "hoverTract", id: r.geo_id })}
            onMouseLeave={() => d({ type: "hoverTract", id: null })}
            tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && select("tract", r.geo_id, coords(r), 13)}
          >
            <div className="card-title">
              <span className={`rank ${list === "must" ? "rank-must" : ""}`}>{r.rank}</span> {tractName(r.geo_id)}
            </div>
            <div className="card-line">
              <b>{n0(r.people?.households_no_vehicle ?? r.mobility?.households_no_vehicle)}</b> households without a car
              {r.mobility?.share_no_vehicle != null ? ` (${Math.round(r.mobility.share_no_vehicle * 100)}%)` : ""} <Chip label="RECORDED" title="Census ACS vehicle ownership" />
            </div>
            {r.mobility?.recommendation && <p className="card-sub">{r.mobility.recommendation}</p>}
            <Disclosure>
              <ul className="evidence-list">
                {(r.reasons || []).map((x, i) => (
                  <li key={i}>
                    {x.text} <Chip label={x.label} title={x.source} />
                  </li>
                ))}
                {r.mobility?.straight_line_km != null && (
                  <li>
                    Nearest pre-landfall shelter: {distanceText(r.mobility.distance_basis, r.mobility.straight_line_km, r.mobility.drive_minutes)}
                  </li>
                )}
                <li className="muted">
                  Census GEOID {r.geo_id} · priority score {r.priority_score != null ? Math.round(r.priority_score * 100) / 100 : "—"}
                </li>
              </ul>
            </Disclosure>
          </article>
        )}
      />
      {change && (
        <Disclosure summary="Why this ranking">
          <p className="small">{change}</p>
        </Disclosure>
      )}
      {b?.power_dependence?.power_dependent_dme != null && (
        <p className="note">
          <b>{n0(b.power_dependence.power_dependent_dme)}</b> residents depend on electricity for medical equipment. HHS publishes this per
          county, so we can say how many but never which block. <Chip label="COUNTY-LEVEL" />
        </p>
      )}
    </>
  );
}

// ---- Step 2 --------------------------------------------------------------------------------

function Step2() {
  const { s, act } = useStore();
  const select = useSelect();
  const b = s.brief;
  const shelters = useMemo(() => shelterViews(s), [s.map, s.allocation, s.brief, s.rehearsal, s.rehearsalMode]);
  const rows = useMemo(() => {
    const m = new Map<string, WhereRow>();
    for (const r of b?.where_they_would_go || []) if (!m.has(r.shelter_id) || r.live) m.set(r.shelter_id, r);
    return [...m.values()].sort((a, z) => Number(!!z.live) - Number(!!a.live));
  }, [b]);
  if (!b) return <Loading what="Building the brief from BigQuery (about a minute on first load)…" />;
  const wc = b.county_shelters?.pre_landfall_wheelchair;
  const pre = b.county_shelters?.pre_landfall ?? shelters.filter((x) => x.isPre).length;
  const checked = rows.filter((r) => r.live).length;
  const verifying = s.verifying.length > 0;
  return (
    <>
      <div className="kpis">
        <Kpi value={n0(pre)} label="shelters designated for use before landfall" chip="RECORDED" />
        <Kpi value={wc ? `${n0(wc.unrecorded)}` : "—"} label={wc ? `with wheelchair access not recorded (${wc.yes + wc.no} known)` : "wheelchair access"} chip="UNRECORDED" tone="warn" />
        <Kpi value={verifying ? "…" : n0(checked)} label="checked on Google Maps this session" chip="VERIFIED LIVE" tone="ok" />
      </div>
      <p className="takeaway">
        {wc ? `${n0(wc.unrecorded)} of ${n0(wc.yes + wc.no + wc.unrecorded)}` : "Most"} shelters have no wheelchair record, and the federal file is a
        historical registry. So we check named buildings on Google Maps one at a time, and say plainly what nobody has recorded.
      </p>
      <div className="row-actions">
        <button className="btn" onClick={() => void act.verifyTwo()} disabled={verifying}>
          {verifying ? "Checking on Google Maps, one building at a time…" : "Check 2 shelters on Google Maps"}
        </button>
      </div>
      <TopList
        items={rows}
        keyOf={(r) => r.shelter_id}
        selectedKey={s.selected?.kind === "shelter" ? s.selected.id : null}
        render={(r) => <ShelterCard row={r} onSelect={() => select("shelter", r.shelter_id, coords(r) || shelters.find((x) => x.id === r.shelter_id)?.at || null, 14)} />}
      />
    </>
  );
}

function distanceText(basis?: string | null, km?: number | null, minutes?: number | null): string {
  const code = basisCode(basis);
  if (code === "ROUTED" && minutes != null) return `Drive: ${Math.round(minutes)} min (Google Maps route)`;
  if (code === "MODEL_REPORTED" && minutes != null) return `Drive: about ${Math.round(minutes)} min (estimate, no route returned)`;
  return km != null ? `${km.toFixed(1)} km straight-line (road distance is longer)` : "distance unknown";
}

function liveStatus(r: WhereRow): { text: string; cls: string } {
  const ex = existenceOf(r);
  const flags = r.flags || [];
  if (ex === "DROPPED")
    return flags.includes("SUCCESSOR_AT_ADDRESS") && r.live?.current_name
      ? { text: `Dropped: now ${r.live.current_name} at this address`, cls: "st-drop" }
      : { text: "Dropped: not found or closed", cls: "st-drop" };
  if (ex === "RENAMED") return { text: `Confirmed, now called ${r.live?.current_name}`, cls: "st-renamed" };
  if (ex === "CONFLICT") return { text: "Checked, still unconfirmed", cls: "st-conflict" };
  if (ex === "VERIFIED_LIVE") return { text: "Confirmed on Google Maps", cls: "st-ok" };
  return { text: r.live ? "Google Maps gave no usable answer" : "Not checked yet", cls: "st-none" };
}

export function ShelterCard({ row: r, onSelect }: { row: WhereRow; onSelect: () => void }) {
  const { s, act } = useStore();
  const rec = r.record || {};
  const st = liveStatus(r);
  const sel = s.selected?.kind === "shelter" && s.selected.id === r.shelter_id;
  const busy = s.verifying.includes(r.shelter_id);
  const cap = rec.capacity ?? rec.evacuation_capacity;
  const wcText = rec.wheelchair === "YES" ? "wheelchair: yes (recorded)" : rec.wheelchair === "NO" ? "wheelchair: no (recorded)" : "wheelchair: not recorded";
  return (
    <article id={`card-shelter-${r.shelter_id}`} className={`card ${sel ? "selected" : ""} ${r.verdict === "DROP" ? "dropped" : ""}`} onClick={onSelect} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onSelect()}>
      <div className="card-title">
        <span className="name">{shelterName(r)}</span>
        {rec.name_says_inactive && <span className="tag tag-danger">FEMA name says INACTIVE</span>}
      </div>
      <div className="card-line muted">
        {cap == null || rec.capacity_status === "UNRECORDED" ? "capacity not recorded" : `${n0(cap)} spaces`} · {wcText}
      </div>
      <div className={`status ${st.cls}`}>
        <span className="status-mark" aria-hidden />
        {st.text}
        {r.live?.verified_at && <span className="muted"> · checked on Google Maps {ago(r.live.verified_at)}</span>}
      </div>
      {r.live && (
        <>
          <p className="claim">
            {protectGM(r.why || "")}
            {r.live.drive_minutes != null && <> {distanceText(r.live.drive_basis, null, r.live.drive_minutes)}.</>}
          </p>
          <MapsAttribution sources={r.live.sources} fallbackTitle={r.live.current_name || shelterName(r)} />
        </>
      )}
      {!r.live && !s.rehearsalMode && (
        <button
          className="btn btn-small"
          disabled={busy}
          onClick={(e) => {
            e.stopPropagation();
            void act.verify([r.shelter_id], r.for_tract);
          }}
        >
          {busy ? "Checking on Google Maps…" : "Check on Google Maps"}
        </button>
      )}
      <Disclosure>
        <ul className="evidence-list">
          <li>
            FEMA record <Chip label="RECORDED" title="FEMA National Shelter System, a historical registry" />: wheelchair {rec.wheelchair || "UNRECORDED"} ·
            generator {rec.generator || "UNRECORDED"} · surge zone {rec.surge || "UNRECORDED"}
            {rec.surge === "UNRECORDED" ? " (blank does not mean outside the zone)" : ""} · usage {rec.usage || "not recorded"}
          </li>
          {rec.capacity_implausible && <li>Capacity as filed looks implausible, so it is excluded from totals.</li>}
          {r.straight_line_km != null && <li>From {tractName(r.for_tract)}: {distanceText(null, r.straight_line_km, null)}</li>}
          {rec.site_hazard?.coastal_flood_exposed_share != null && (
            <li>
              Neighbourhood flood exposure: {Math.round(rec.site_hazard.coastal_flood_exposed_share * 100)}% of the nearest tract's residents{" "}
              <Chip label="APPROXIMATE" title={rec.site_hazard.method} />
            </li>
          )}
          {r.accessibility && (
            <li>
              {protectGM(r.accessibility.summary)} <Chip label={r.accessibility.label} />
              {r.accessibility.label === "VERIFIED LIVE" && r.live && <MapsAttribution sources={r.live.sources} fallbackTitle={shelterName(r)} />}
            </li>
          )}
          {(r.flags || []).length > 0 && <li>Flags: {(r.flags || []).map((f) => humanType(f)).join(" · ")}</li>}
          <li className="muted">
            FEMA shelter ID {r.shelter_id}
            {r.address ? ` · ${r.address}` : ""}
          </li>
        </ul>
      </Disclosure>
    </article>
  );
}

// ---- Step 3 --------------------------------------------------------------------------------

function Step3() {
  const { s, d } = useStore();
  const select = useSelect();
  const r = s.rehearsal;
  const storm = s.storm;
  const shelters = useMemo(() => shelterViews(s).filter((x) => x.isPre), [s.map, s.brief, s.allocation]);
  return (
    <>
      <div className="rehearsal-banner" role="note">
        Tabletop rehearsal: no Google Maps grounding
      </div>
      {!r ? (
        <Loading what="Taking shelters offline and re-solving the plan…" />
      ) : (
        <Step3Body />
      )}
      {storm && storm.advisories.length > 1 && r && (
        <Disclosure summary="Earlier advisories">
          <p className="small muted">Slide through NHC advisories to see when Fort Myers entered the cone.</p>
          <input
            type="range"
            min={0}
            max={storm.advisories.length - 1}
            value={Math.min(s.advisoryIdx, storm.advisories.length - 1)}
            onChange={(e) => d({ type: "advisory", idx: Number(e.target.value) })}
            aria-label="NHC advisory"
          />
          {(() => {
            const adv = storm.advisories[Math.min(s.advisoryIdx, storm.advisories.length - 1)];
            const inside = adv.cone ? shelters.filter((x) => inGeometry(x.at, adv.cone)).length : 0;
            const fm = adv.cone ? inGeometry({ lat: 26.6406, lng: -81.8723 }, adv.cone) : false;
            const h = storm.landfall_at ? Math.round((Date.parse(storm.landfall_at) - Date.parse(adv.issued_at)) / 3600000) : null;
            return (
              <p className="small">
                Advisory {adv.advisory}
                {h != null ? ` (${h} h before landfall)` : ""}: {inside} of {shelters.length} shelters inside the cone; Fort Myers {fm ? "inside" : "outside"}.
              </p>
            );
          })()}
        </Disclosure>
      )}
      <p className="small muted">Forecast cones: NOAA National Hurricane Center (public domain). Storm Watch consumes the official forecast; it does not predict storms.</p>
      {void select}
    </>
  );
}

function Step3Body() {
  const { s } = useStore();
  const select = useSelect();
  const r = s.rehearsal!;
  const ub = r.diff.unassigned_before ?? r.before.totals?.unassigned_persons ?? 0;
  const ua = r.diff.unassigned_after ?? r.after.totals?.unassigned_persons ?? 0;
  const landfall = r.scenario.landfall_at;
  const issued = r.scenario.issued_at;
  const hours = landfall && issued ? Math.round((Date.parse(landfall) - Date.parse(issued)) / 3600000) : null;
  return (
    <>
      <div className="kpis">
        <Kpi value={hours != null ? `T−${hours} h` : "—"} label={`to landfall, NHC advisory ${r.scenario.advisory ?? ""}`} chip="RECORDED" />
        <Kpi value={n0(r.shelters_down.length)} label={`shelters offline · ${n0(r.diff.capacity_lost)} spaces lost`} chip="APPROXIMATE" tone="warn" />
        <Kpi value={<>{n0(ub)} → {n0(ua)}</>} label="people with no shelter seat" chip="APPROXIMATE" tone="danger" />
      </div>
      <p className="takeaway">
        If Ian's cone and surge take {r.shelters_down.length} shelters offline, {n0(ua - ub)} more people lose a seat and{" "}
        {n0((r.diff as { reassigned_total?: number }).reassigned_total ?? r.diff.reassigned.length)} pickup runs must be re-routed.
      </p>
      <h3 className="list-title">Taken offline</h3>
      <TopList
        items={r.shelters_down}
        keyOf={(x) => x.shelter_id}
        selectedKey={s.selected?.kind === "shelter" ? s.selected.id : null}
        render={(x) => (
          <article id={`card-shelter-${x.shelter_id}`} className={`card ${s.selected?.id === x.shelter_id ? "selected" : ""}`} onClick={() => select("shelter", x.shelter_id, coords(x), 13)} tabIndex={0}>
            <div className="card-title">
              <span className="shape-tri" aria-hidden /> {x.name || x.shelter_id}
            </div>
            <div className="card-line">
              {x.reason === "IN_SURGE_ZONE" ? "Recorded inside the storm-surge zone" : "Inside the Ian cone, in a flood-exposed neighbourhood"}{" "}
              <Chip label={x.reason === "IN_SURGE_ZONE" ? "RECORDED" : "APPROXIMATE"} />
              {x.in_plan === false ? <span className="muted"> · was not in the plan</span> : null}
            </div>
            <Disclosure>
              <p className="small">{x.evidence || x.rule}</p>
              <p className="small muted">FEMA shelter ID {x.shelter_id}</p>
            </Disclosure>
          </article>
        )}
      />
      {(r.at_risk_not_in_plan || []).length > 0 && (
        <div className="callout">
          <b>{r.at_risk_not_in_plan!.length} at-risk shelters have no capacity on file</b>, so losing them moves nobody in the plan. That is a
          paperwork gap, not safety.
        </div>
      )}
      {r.diff.newly_unassigned.length > 0 && (
        <>
          <h3 className="list-title">Newly without a shelter seat</h3>
          <TopList
            items={r.diff.newly_unassigned}
            keyOf={(u) => u.geo_id}
            selectedKey={s.selected?.kind === "tract" ? s.selected.id : null}
            render={(u) => (
              <article id={`card-tract-${u.geo_id}`} className={`card ${s.selected?.id === u.geo_id ? "selected" : ""}`} onClick={() => select("tract", u.geo_id, coords(u), 13)} tabIndex={0}>
                <div className="card-title">{tractName(u.geo_id)}</div>
                <div className="card-line">
                  <b className="danger">{n0(u.persons)}</b> people lose their seat <Chip label="APPROXIMATE" title="Estimated people, from households × persons per household" />
                </div>
              </article>
            )}
          />
        </>
      )}
      <PlanApproval kind="rehearsal" />
    </>
  );
}

// ---- Step 4 --------------------------------------------------------------------------------

function Step4() {
  const { s, act } = useStore();
  const select = useSelect();
  const a = s.allocation;
  const b = s.brief;
  const t = a?.totals;
  const options: SiteOption[] = (a?.site_options || []).slice(0, 3);
  const headline = (a?.gaps || []).find((g) => g.ref?.kind === "county" && g.type === "UNASSIGNED_DEMAND");
  const fixes = useMemo(() => {
    const all: (FixItem & { _key: string })[] = [
      ...(b?.fix_before_season || []).map((f, i) => ({ ...f, _key: `b${i}` })),
      ...(a?.gaps || []).filter((g) => g !== headline).map((f, i) => ({ ...f, _key: `p${i}` })),
    ];
    const rank: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 };
    return all.sort((x, y) => (rank[x.severity || "MEDIUM"] ?? 1) - (rank[y.severity || "MEDIUM"] ?? 1));
  }, [a, b, headline]);
  if (!a && !b) return <Loading what="Loading the plan…" />;
  const high = fixes.filter((f) => f.severity === "HIGH").length;
  return (
    <>
      <div className="kpis">
        <Kpi value={n0(t?.unassigned_persons)} label="people with no shelter seat within 15 km" chip="APPROXIMATE" tone="danger" />
        <Kpi value={n0(options[0]?.persons_covered)} label="seated by one new site in the right place" chip="APPROXIMATE" tone="ok" />
        <Kpi value={n0(high)} label="high-priority fixes before the season" chip="RECORDED" tone="warn" />
      </div>
      {options[0] && (
        <p className="takeaway">
          One designated site near {tractName(options[0].ref?.id)} would seat {n0(options[0].persons_covered)} of the {n0(t?.unassigned_persons)} people
          who have no seat today. Start there, then work down the fix list.
        </p>
      )}
      {headline && !options[0] && <p className="takeaway">{headline.evidence}</p>}
      {options.length > 0 && (
        <>
          <h3 className="list-title">What one site buys</h3>
          <ol className="list">
            {options.map((o, i) => (
              <li key={i}>
                <article
                  id={`card-fix-site${i}`}
                  className={`card site ${s.selected?.id === `site${i}` ? "selected" : ""}`}
                  onClick={() => select("fix", `site${i}`, coords(o.ref), 12)}
                  tabIndex={0}
                >
                  <div className="card-title">
                    <span className="star" aria-hidden>★</span> {i === 0 ? "Designate one site here" : `Next site #${i + 1}`}
                  </div>
                  <div className="card-line">
                    Seats <b>{n0(o.persons_covered)}</b> more people without a car from {o.tracts_covered?.length ?? "—"} neighbourhoods
                  </div>
                  <Disclosure>
                    <p className="small">{o.evidence}</p>
                    <p className="small">→ {o.action}</p>
                    <p className="small muted">Centred on {tractName(o.ref?.id)} · needs {n0(o.seats_needed)} spaces</p>
                  </Disclosure>
                </article>
              </li>
            ))}
          </ol>
        </>
      )}
      <h3 className="list-title">Fix list</h3>
      <TopList
        items={fixes}
        keyOf={(f) => f._key}
        selectedKey={s.selected?.kind === "fix" ? s.selected.id : null}
        render={(f) => <FixCard f={f} id={f._key} onSelect={() => select("fix", f._key, coords(f.ref), f.ref?.kind === "county" ? 10 : 14)} />}
      />
      <PlanApproval kind="allocation" />
      {b?.could_not_tell_you && (
        <Disclosure summary={`What we could not tell you (${b.could_not_tell_you.length})`}>
          <ul className="evidence-list">
            {b.could_not_tell_you.map((c, i) => (
              <li key={i}>
                <b>{c.item}</b> <Chip label={c.label} />
                {c.spoken_to != null || c.unassessed != null ? (
                  <div className="small">
                    {c.spoken_to != null ? `${c.spoken_to} I can speak to; ` : ""}
                    {c.unassessed != null ? `${c.unassessed} nobody has assessed.` : ""}
                  </div>
                ) : null}
                {c.note && <div className="small muted">{c.note}</div>}
              </li>
            ))}
          </ul>
        </Disclosure>
      )}
      {void act}
    </>
  );
}

function FixCard({ f, id, onSelect }: { f: FixItem; id: string; onSelect: () => void }) {
  const { s, act } = useStore();
  const sel = s.selected?.kind === "fix" && s.selected.id === id;
  const live = f.ref?.kind === "shelter" ? s.brief?.where_they_would_go.find((r) => String(r.shelter_id) === String(f.ref!.id) && r.live) : undefined;
  const busy = f.ref?.kind === "shelter" && s.verifying.includes(f.ref.id);
  return (
    <article id={`card-fix-${id}`} className={`card fix fix-${(f.severity || "MEDIUM").toLowerCase()} ${sel ? "selected" : ""}`} onClick={onSelect} tabIndex={0}>
      <div className="card-title">
        <Severity level={f.severity} /> {humanType(f.type)}
      </div>
      <div className="card-line">{f.ref?.kind === "tract" ? tractName(f.ref.id) : f.subject.replace(/\s*\(\d+\)$/, "")}</div>
      <p className="card-sub">→ {f.action}</p>
      {live && (
        <div className="fix-live">
          <div className={`status ${liveStatus(live).cls}`}>
            <span className="status-mark" aria-hidden />
            {liveStatus(live).text} <span className="muted">· checked on Google Maps {ago(live.live?.verified_at)}</span>
          </div>
          <p className="claim">{protectGM(live.why || "")}</p>
          <MapsAttribution sources={live.live?.sources} fallbackTitle={live.live?.current_name || shelterName(live)} />
        </div>
      )}
      {!live && f.ref?.kind === "shelter" && (
        <button
          className="btn btn-small"
          disabled={busy}
          onClick={(e) => {
            e.stopPropagation();
            void act.verify([f.ref!.id]);
          }}
        >
          {busy ? "Checking on Google Maps…" : "Check on Google Maps"}
        </button>
      )}
      <Disclosure>
        <p className="small">
          {protectGM(f.evidence)} <Chip label={f.label} />
        </p>
        <p className="small muted">
          {f.ref ? `${f.ref.kind} ${f.ref.id}` : ""}
          {f.persons_affected != null ? ` · ${n0(f.persons_affected)} people affected` : ""}
        </p>
      </Disclosure>
    </article>
  );
}
