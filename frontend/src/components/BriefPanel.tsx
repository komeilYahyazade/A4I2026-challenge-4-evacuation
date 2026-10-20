import { useEffect, useMemo, useState } from "react";
import type { CannotTell, FixItem, OperatorAssessment, TriCount, WhereRow } from "../contract";
import { ago, coords, downReasonLabel, fmt, inGeometry, shelterName } from "../derive";
import { api, type PlanRef } from "../api/rest";
import { CONFIG, MOCK_ONLY } from "../config";
import { FIXTURE_NOTE } from "../mock/lee";
import { useStore } from "../store";
import { shelterViews } from "../views";
import { Basis, Label, SourceBadge, Tri } from "./Badges";
import { CapacityText, LiveBlock, ShelterCard, SiteHazardLine, VerifyButton } from "./Cards";

export function BriefPanel() {
  const { s } = useStore();
  if (s.phase === "watch") return <WatchView />;
  if (s.phase === "inventory") return <InventoryView />;
  if (s.phase === "learn") return <LearnView />;
  if (s.phase === "plan") return <PlanView />;
  return <BriefView />;
}

function Header({ title, subtitle }: { title: string; subtitle?: string }) {
  const { s } = useStore();
  const src = s.sources.brief || s.sources.map;
  return (
    <header className="panel-head">
      <div>
        <h2>{title}</h2>
        {subtitle && <p className="muted small">{subtitle}</p>}
      </div>
      <SourceBadge source={src} note={FIXTURE_NOTE} />
    </header>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="muted empty">{text}</p>;
}

// ---- August brief: four segments ----------------------------------------------------------

function BriefView() {
  const { s } = useStore();
  const b = s.brief;
  if (!b) return <><Header title="Readiness brief" /><Empty text={s.busy.brief ? "Building the brief…" : "Press “1 · August brief” to build it."} /></>;
  return (
    <div className="brief">
      <Header
        title={`${b.county.name} County readiness brief`}
        subtitle={`Generated ${ago(b.generated_at)} · ${new Set(b.where_they_would_go.filter((r) => r.live).map((r) => r.shelter_id)).size} shelters checked live this session`}
      />
      <TopNumbers />

      <section className="seg">
        <h3>1 · Who cannot leave</h3>
        <WhoCannotLeave />
      </section>

      <section className="seg">
        <h3>2 · Where they would go, and whether that holds up</h3>
        <p className="muted small">Record and live check side by side. Google Maps sources appear directly under the claim they support.</p>
        <WhereList rows={b.where_they_would_go} />
      </section>

      <FixSection />
      <CannotTellSection items={b.could_not_tell_you} />
    </div>
  );
}

function WhoCannotLeave() {
  const { s, act } = useStore();
  const [list, setList] = useState<"drive" | "must">("drive");
  const b = s.brief!;
  const must = b.must_leave_may_need_help || [];
  const rows = list === "must" && must.length ? must : b.who_cannot_leave;
  const change = typeof b.assumptions?.ranking_change === "string" ? (b.assumptions.ranking_change as string) : null;
  return (
    <>
      {must.length > 0 && (
        <div className="seg-toggle" role="tablist">
          <button className={list === "drive" ? "active t-drive" : ""} onClick={() => setList("drive")}>
            Can't drive ({b.who_cannot_leave.length})
          </button>
          <button className={list === "must" ? "active t-must" : ""} onClick={() => setList("must")}>
            Must leave, may need help ({must.length})
          </button>
        </div>
      )}
      <p className="muted small">
        {list === "must"
          ? "Ranked by mobile homes, people 65+ and estimated disability counts. Physical and demographic inputs only."
          : "Ranked by the number of households without a car. Physical and demographic inputs only: no accessibility field, no race."}
      </p>
        {rows.map((t) => (
          <article key={t.geo_id} className="card tract" onClick={() => act.focusOn(coords(t), 13)}>
            <div className="card-head">
              <span className="rank">#{t.rank}</span> Tract {t.geo_id}
              <span className="muted small"> · priority {fmt(t.priority_score, 2)}</span>
            </div>
            <ul className="reasons">
              {(t.reasons || []).map((r, i) => (
                <li key={i}>
                  {r.text} <Label label={r.label} title={r.source} />
                </li>
              ))}
            </ul>
            {t.mobility && (
              <p className="small">
                <b>{(t.mobility.status || "").replace(/_/g, " ")}</b> · nearest pre-landfall shelter{" "}
                <Basis basis={t.mobility.distance_basis} km={t.mobility.straight_line_km} minutes={t.mobility.drive_minutes} />. {t.mobility.recommendation}
              </p>
            )}
          </article>
        ))}
      {change && (
        <details className="why-ranking" open>
          <summary>Why this ranking</summary>
          <p className="small">{change}</p>
        </details>
      )}
    </>
  );
}

function TopNumbers() {
  const { s } = useStore();
  const b = s.brief;
  const cs = b?.county_shelters;
  const pw = b?.power_dependence;
  const totals = s.allocation?.totals;
  return (
    <div className="top-numbers">
      {cs?.pre_landfall_wheelchair && <TriBar title="Wheelchair access, pre-landfall shelters" c={cs.pre_landfall_wheelchair} />}
      {cs?.pre_landfall_surge && <TriBar title="Surge-zone flag, pre-landfall shelters" c={cs.pre_landfall_surge} note="Blank does not mean outside the zone." />}
      <div className="stat-row">
        {pw?.power_dependent_dme != null && (
          <div className="stat">
            <b>{fmt(pw.power_dependent_dme)}</b>
            <span>power-dependent residents <Label label={pw.label || "COUNTY-LEVEL"} /></span>
          </div>
        )}
        {totals?.demand_persons != null && (
          <div className="stat">
            <b>{fmt(totals.demand_persons)}</b>
            <span>people without a car to place</span>
          </div>
        )}
        {cs?.pre_landfall_capacity_recorded != null && (
          <div className="stat">
            <b>{fmt(cs.pre_landfall_capacity_plausible ?? cs.pre_landfall_capacity_recorded)}</b>
            <span>
              recorded spaces ({fmt(cs.pre_landfall_capacity_unrecorded)} shelters with capacity not recorded)
            </span>
          </div>
        )}
        {(cs?.pre_landfall_name_says_inactive || 0) > 0 && (
          <div className="stat stat-alert">
            <b>{cs!.pre_landfall_name_says_inactive}</b>
            <span>designated shelters whose FEMA name says INACTIVE</span>
          </div>
        )}
      </div>
    </div>
  );
}

function TriBar({ title, c, note }: { title: string; c: TriCount; note?: string }) {
  const total = c.yes + c.no + c.unrecorded || 1;
  return (
    <div className="tribar">
      <div className="small">
        {title}: <b>{c.yes + c.no} I can speak to; {c.unrecorded} nobody has assessed.</b>
      </div>
      <div className="bar" role="img" aria-label={`${c.yes} yes, ${c.no} no, ${c.unrecorded} unrecorded`}>
        <span className="b-yes" style={{ width: `${(100 * c.yes) / total}%` }} title={`${c.yes} recorded YES`} />
        <span className="b-no" style={{ width: `${(100 * c.no) / total}%` }} title={`${c.no} recorded NO`} />
        <span className="b-unrec" style={{ width: `${(100 * c.unrecorded) / total}%` }} title={`${c.unrecorded} UNRECORDED`} />
      </div>
      <div className="bar-key small muted">
        YES {c.yes} · NO {c.no} · UNRECORDED {c.unrecorded}
        {note ? ` · ${note}` : ""}
      </div>
    </div>
  );
}

function WhereList({ rows }: { rows: WhereRow[] }) {
  const { act } = useStore();
  // One card per shelter; a shelter can be a destination for several tracts.
  const unique = useMemo(() => {
    const m = new Map<string, WhereRow & { tracts: string[] }>();
    for (const r of rows) {
      const prev = m.get(r.shelter_id);
      if (prev) {
        if (r.for_tract && !prev.tracts.includes(r.for_tract)) prev.tracts.push(r.for_tract);
      } else m.set(r.shelter_id, { ...r, tracts: r.for_tract ? [r.for_tract] : [] });
    }
    return [...m.values()];
  }, [rows]);
  if (!unique.length) return <Empty text="No pre-landfall destinations within reach." />;
  return (
    <>
      {unique.map((r) => {
        const rec = r.record || {};
        return (
          <article key={r.shelter_id} className={`card shelter ${r.verdict === "DROP" ? "dropped" : ""}`}>
            <div className="card-head" onClick={() => act.focusOn(coords(r), 14)}>
              {shelterName(r)}
              {rec.name_says_inactive && <span className="flag flag-alert">NAME SAYS INACTIVE</span>}
            </div>
            <div className="muted small">
              {r.address}
              {r.tracts.length ? (
                <>
                  {" "}· for tract{r.tracts.length > 1 ? "s" : ""} {r.tracts.join(", ")} · <Basis km={r.straight_line_km} />
                </>
              ) : (
                " · checked from the fix list"
              )}
            </div>
            <div className="record">
              <Label label={rec.label || "RECORDED"} title={rec.source || "FEMA National Shelter System"} />{" "}
              <CapacityText capacity={rec.capacity ?? rec.evacuation_capacity} status={rec.capacity_status} implausible={rec.capacity_implausible} /> · {rec.usage || "usage UNRECORDED"}
              {rec.is_medical ? " · medical-needs" : ""}
              <div className="row-chips">
                <Tri name="Wheelchair" value={rec.wheelchair} />
                <Tri name="Generator" value={rec.generator} />
                <Tri name="Surge zone" value={rec.surge} bad="YES" />
              </div>
              {rec.site_hazard && <SiteHazardLine h={rec.site_hazard} />}
            </div>
            {r.live ? (
              <LiveBlock row={r} />
            ) : (
              <div className="not-checked">
                <span className="muted small">{r.why || "Not checked against Google Maps in this brief."}</span> <VerifyButton id={r.shelter_id} origin={r.for_tract} />
              </div>
            )}
          </article>
        );
      })}
    </>
  );
}

function FixSection({ title = "3 · What to fix before the season", planFirst = false }: { title?: string; planFirst?: boolean }) {
  const { s, d, act } = useStore();
  const alloc = s.rehearsalMode && s.rehearsal ? s.rehearsal.after : s.allocation;
  const briefFixes = s.brief?.fix_before_season || [];
  const planGaps = alloc?.gaps || [];
  const budget = s.brief?.budget;
  const fixes = s.brief?.fixes || [];
  const headline = planGaps.find((g) => g.ref?.kind === "county" && g.type === "UNASSIGNED_DEMAND");
  const options = (alloc?.site_options || []).slice(0, 3);
  const item = (f: FixItem, idx: number) => (
    <li
      key={idx}
      className={`fix sev-${(f.severity || "MEDIUM").toLowerCase()} ${s.highlightFix === idx ? "hl" : ""} ${f.type === "NAME_SAYS_INACTIVE" ? "fix-alert" : ""}`}
      onClick={() => {
        d({ type: "highlightFix", idx });
        act.focusOn(coords(f.ref), f.ref?.kind === "county" ? 10 : 14);
      }}
    >
      <div className="fix-head">
        {f.severity && <span className={`sev sev-${f.severity.toLowerCase()}`}>{f.severity}</span>}
        <span className="fix-type">{f.type.replace(/_/g, " ")}</span>
        <Label label={f.label} />
      </div>
      <div className="fix-subject">{f.subject}</div>
      <div className="small">{f.evidence}</div>
      <div className="small fix-action">→ {f.action}</div>
      {f.ref?.kind === "shelter" && <FixLive id={f.ref.id} />}
    </li>
  );
  return (
    <section className="seg" id="seg-fix">
      <h3>{title}</h3>
      {!planFirst && briefFixes.length > 0 && (
        <>
          <h4>From the brief</h4>
          <ol className="fixes">{briefFixes.map((f, i) => item(f, i))}</ol>
        </>
      )}
      {headline && (
        <div className="headline-gap">
          <b>{headline.subject}</b>
          <div className="small">{headline.evidence}</div>
          <div className="small">→ {headline.action}</div>
        </div>
      )}
      {options.length > 0 && (
        <>
          <h4>What one site buys</h4>
          <ol className="fixes">
            {options.map((o, i) => (
              <li
                key={i}
                className={`fix site-option ${s.highlightFix === 2000 + i ? "hl" : ""}`}
                onClick={() => {
                  d({ type: "highlightFix", idx: 2000 + i });
                  act.focusOn(coords(o.ref), 13);
                }}
              >
                <div className="fix-head">
                  <span className="star">★</span>
                  <span className="fix-type">{i === 0 ? "DESIGNATE ONE SITE HERE" : `NEXT SITE (#${i + 1})`}</span>
                  <Label label={o.label} />
                </div>
                <div className="fix-subject">
                  Seats <b>{fmt(o.persons_covered)}</b> more people without a car
                  {o.tracts_covered ? ` from ${o.tracts_covered.length} tracts` : ""}
                  {o.seats_needed != null ? ` · needs ${fmt(o.seats_needed)} spaces` : ""}
                </div>
                <div className="small">{o.evidence}</div>
                <div className="small fix-action">→ {o.action}</div>
              </li>
            ))}
          </ol>
        </>
      )}
      {planFirst && briefFixes.length > 0 && (
        <>
          <h4>From the brief</h4>
          <ol className="fixes">{briefFixes.map((f, i) => item(f, i))}</ol>
        </>
      )}
      {planGaps.length > 0 && (
        <>
          <h4>From the assignment plan{s.rehearsalMode ? " (after the Ian rehearsal)" : ""}</h4>
          <ol className="fixes">{planGaps.map((f, i) => (f === headline ? null : item(f, 1000 + i)))}</ol>
        </>
      )}
      {fixes.length > 0 && (
        <>
          <h4>Budgeted fixes{budget ? ` · $${fmt(budget.spent_usd || 0)} of $${fmt(budget.total_usd)}` : ""}</h4>
          <ol className="fixes">
            {fixes.map((f, i) => (
              <li key={i} className="fix">
                <div className="fix-head">
                  <span className="fix-type">{f.type.replace(/_/g, " ")}</span>
                  {f.cost_usd != null && <span className="cost">${fmt(f.cost_usd)}</span>}
                </div>
                <div className="fix-subject">{f.subject}</div>
                <div className="small">
                  {f.persons_covered != null ? `Covers ${fmt(f.persons_covered)} more people without a car. ` : ""}
                  {f.evidence}
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
      {!briefFixes.length && !planGaps.length && <Empty text="Nothing to fix yet. Load the brief or the plan." />}
    </section>
  );
}

/** A fix about one shelter can be checked live right there; the result and its sources sit under it. */
function FixLive({ id }: { id: string }) {
  const { s } = useStore();
  const row = s.brief?.where_they_would_go.find((r) => String(r.shelter_id) === String(id) && r.live);
  if (!row) return <div onClick={(e) => e.stopPropagation()}><VerifyButton id={id} /></div>;
  return <LiveBlock row={row} />;
}

function CannotTellSection({ items }: { items: CannotTell[] }) {
  const { s } = useStore();
  const extra = (s.allocation?.could_not_tell_you || []).filter((x) => !items.some((y) => y.item === x.item));
  const all = [...items, ...extra];
  return (
    <section className="seg">
      <h3>4 · What we could not tell you</h3>
      <p className="muted small">Named, counted, and not hidden. This is a finding, not a disclaimer.</p>
      <ul className="cannot">
        {all.map((c, i) => (
          <li key={i}>
            <div>
              <b>{c.item}</b> <Label label={c.label} />
            </div>
            {(c.spoken_to != null || c.unassessed != null) && (
              <div className="small">
                {c.spoken_to != null ? `${c.spoken_to} I can speak to; ` : ""}
                {c.unassessed != null ? `${c.unassessed} nobody has assessed.` : ""}
              </div>
            )}
            {c.note && <div className="small muted">{c.note}</div>}
          </li>
        ))}
      </ul>
    </section>
  );
}

// ---- Other phases ---------------------------------------------------------------------------

function InventoryView() {
  const { s } = useStore();
  const shelters = useMemo(() => shelterViews(s), [s.map, s.allocation, s.brief, s.rehearsal, s.rehearsalMode]);
  const pre = shelters.filter((x) => x.isPre);
  const cs = s.brief?.county_shelters;
  return (
    <div className="brief">
      <Header title="Shelter inventory" subtitle="Every registered shelter, three-state. Check them against Google Maps one building at a time." />
      {cs?.pre_landfall_wheelchair && <TriBar title="Wheelchair access" c={cs.pre_landfall_wheelchair} />}
      <p className="small">
        {pre.length} pre-landfall shelters on the map · {pre.filter((x) => x.row?.live).length} checked live this session.
      </p>
      {shelters
        .slice()
        .sort((a, b) => Number(!!b.row?.live) - Number(!!a.row?.live) || a.name.localeCompare(b.name))
        .slice(0, 60)
        .map((x) => (
          <article key={x.id} className="card shelter">
            <div className="card-head">{x.name}</div>
            <ShelterCard x={x} />
          </article>
        ))}
    </div>
  );
}

function PlanView() {
  const { s } = useStore();
  const a = s.allocation;
  return (
    <div className="brief">
      <Header title="Plan: who goes where" subtitle="OR-Tools min-cost flow on straight-line distance, within recorded capacity. Recorded accessibility is deliberately not rewarded." />
      {a?.totals && (
        <div className="stat-row">
          <div className="stat"><b>{fmt(a.totals.demand_persons)}</b><span>people to place</span></div>
          <div className="stat"><b>{fmt(a.totals.assigned_persons)}</b><span>assigned</span></div>
          <div className={`stat ${(a.totals.unassigned_persons || 0) > 0 ? "stat-alert" : ""}`}><b>{fmt(a.totals.unassigned_persons)}</b><span>no room within 15 km</span></div>
          <div className="stat"><b>{fmt(a.totals.usable_capacity)}</b><span>usable recorded spaces</span></div>
        </div>
      )}
      {a?.params && (
        <p className="small muted">
          {String(a.params.demand_rule || "")} {String(a.params.capacity_rule || "")} {String(a.params.cost_rule || "")}
        </p>
      )}
      <PlanApproval kind="allocation" />
      <FixSection title="What to fix before the season" planFirst />
    </div>
  );
}

export function PlanApproval({ kind }: { kind: "allocation" | "rehearsal" }) {
  const { s } = useStore();
  const [name, setName] = useState("Lee County Emergency Manager");
  const [plan, setPlan] = useState<PlanRef | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const mock = MOCK_ONLY || (kind === "allocation" ? s.sources.allocation : s.sources.rehearsal) === "mock";
  const run = async (fn: () => Promise<PlanRef>) => {
    setBusy(true);
    setErr(null);
    try {
      setPlan(await fn());
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const create = () =>
    run(async () =>
      mock ? { plan_id: "MOCK-PLAN", version: 1, status: "DRAFT", created_by: name } : api.planCreate(kind, name, s.countyFips),
    );
  const approve = () =>
    run(async () =>
      mock
        ? { ...plan!, status: "APPROVED", approved_by: name, approved_at: new Date().toISOString() }
        : { ...plan!, ...(await api.planApprove(plan!.plan_id, name)), approved_by: name },
    );
  return (
    <div className="approval">
      <div className="approval-head">
        <b>A person approves before anything is published</b>
        {mock && <span className="mock-badge">MOCK</span>}
      </div>
      {CONFIG.publicDemo && !mock ? (
        <div className="small muted">
          <button className="btn btn-small" disabled>
            Submit {kind} plan for approval
          </button>{" "}
          Approval needs a signed-in county user (local demo).
        </div>
      ) : (
        <label className="small">
          Name{" "}
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      )}
      {!plan && !(CONFIG.publicDemo && !mock) && (
        <button className="btn btn-small" disabled={busy || !name.trim()} onClick={create}>
          {busy ? "Saving…" : `Submit ${kind} plan for approval`}
        </button>
      )}
      {plan && (
        <div className="small">
          Plan <code>{plan.plan_id}</code> v{plan.version ?? 1} ·{" "}
          <b className={plan.status === "APPROVED" ? "ok" : ""}>{plan.status}</b>
          {plan.status === "APPROVED" && plan.approved_at ? ` by ${plan.approved_by} at ${new Date(plan.approved_at).toLocaleTimeString()}` : ""}
          {plan.status !== "APPROVED" && (
            <button className="btn btn-small" disabled={busy || !name.trim()} onClick={approve} style={{ marginLeft: 8 }}>
              {busy ? "Approving…" : `Approve as ${name}`}
            </button>
          )}
        </div>
      )}
      {err && <div className="small red">{err}</div>}
      <div className="small muted">Stored in readiness_ops.plans. Only Google Maps place IDs are kept, never Maps content.</div>
    </div>
  );
}

function WatchView() {
  const { s, d, act } = useStore();
  const storm = s.storm;
  const r = s.rehearsal;
  const adv = storm?.advisories[Math.min(s.advisoryIdx, (storm?.advisories.length || 1) - 1)];
  const landfall = r?.scenario.landfall_at || storm?.landfall_at;
  const issued = s.rehearsalMode ? r?.scenario.issued_at || adv?.issued_at : adv?.issued_at;
  const hoursOut = landfall && issued ? Math.round((Date.parse(landfall) - Date.parse(issued)) / 3600000) : null;
  return (
    <div className="brief">
      <header className="panel-head">
        <div>
          <h2>Watch: Hurricane Ian replay</h2>
          <p className="muted small">Consumes the official National Hurricane Center forecast. It does not predict storms.</p>
        </div>
        <SourceBadge source={s.rehearsalMode ? s.sources.rehearsal : s.sources.storm} note={FIXTURE_NOTE} />
      </header>
      {s.rehearsalMode && <div className="rehearsal-banner">Tabletop rehearsal: no Google Maps grounding</div>}
      <div className="countdown">
        {hoursOut != null ? (
          <>
            <b>T−{hoursOut} h</b> to landfall
          </>
        ) : (
          "Load the storm track"
        )}
        {issued && <span className="muted small"> · advisory {s.rehearsalMode ? r?.scenario.advisory : adv?.advisory} issued {new Date(issued).toUTCString().replace(":00 GMT", " UTC")}</span>}
      </div>
      {storm && !s.rehearsalMode && storm.advisories.length > 1 && (
        <div className="advisories">
          <input
            type="range"
            min={0}
            max={storm.advisories.length - 1}
            value={Math.min(s.advisoryIdx, storm.advisories.length - 1)}
            onChange={(e) => d({ type: "advisory", idx: Number(e.target.value) })}
            aria-label="NHC advisory"
          />
          <div className="adv-ticks small">
            {storm.advisories.map((a, i) => (
              <button key={String(a.advisory)} className={`tick ${i === s.advisoryIdx ? "active" : ""}`} onClick={() => d({ type: "advisory", idx: i })}>
                {a.advisory}
              </button>
            ))}
          </div>
        </div>
      )}
      {(s.rehearsalMode ? r?.scenario.cone : adv?.cone) && <InCone cone={(s.rehearsalMode ? r!.scenario.cone : adv!.cone)!} />}
      <p className="small muted">Forecast cones: NOAA National Hurricane Center (public domain). {storm?.source && !/NOAA/.test(storm.source) ? storm.source : ""}</p>
      {!s.rehearsalMode && (
        <button className="btn" onClick={() => act.rehearsalBeat()} disabled={s.busy.rehearsal}>
          {s.busy.rehearsal ? "Re-solving…" : "Run Ian tabletop rehearsal"}
        </button>
      )}
      {s.rehearsalMode && r && <RehearsalResult />}
      {s.rehearsalMode && !r && <Empty text={s.busy.rehearsal ? "Taking shelters offline and re-solving…" : "No rehearsal loaded."} />}
    </div>
  );
}

function InCone({ cone }: { cone: NonNullable<NonNullable<ReturnType<typeof useStore>["s"]["storm"]>["advisories"][number]["cone"]> }) {
  const { s } = useStore();
  const shelters = useMemo(() => shelterViews(s).filter((x) => x.isPre), [s.map, s.brief, s.allocation]);
  if (!shelters.length) return null;
  const inside = shelters.filter((x) => inGeometry(x.at, cone)).length;
  const fortMyers = inGeometry({ lat: 26.6406, lng: -81.8723 }, cone);
  return (
    <p className="small">
      Lee pre-landfall shelters inside this cone: <b>{inside} of {shelters.length}</b>. Fort Myers is {fortMyers ? <b>inside</b> : "outside"} the cone.
    </p>
  );
}

function RehearsalResult() {
  const { s, act } = useStore();
  const r = s.rehearsal!;
  const ub = r.diff.unassigned_before ?? r.before.totals?.unassigned_persons ?? 0;
  const ua = r.diff.unassigned_after ?? r.after.totals?.unassigned_persons ?? 0;
  return (
    <>
      <div className="stat-row">
        <div className="stat"><b>{r.shelters_down.length}</b><span>shelters offline</span></div>
        <div className="stat"><b>{fmt(r.diff.capacity_lost)}</b><span>spaces lost</span></div>
        <div className="stat"><b>{r.diff.reassigned.length}</b><span>tract runs reassigned</span></div>
        <div className={`stat ${ua > ub ? "stat-alert" : ""}`}><b>{fmt(ub)} → {fmt(ua)}</b><span>people with no room</span></div>
      </div>
      <h4>Taken offline</h4>
      <ul className="down-list">
        {r.shelters_down.map((x) => (
          <li key={x.shelter_id} onClick={() => act.focusOn(coords(x), 13)}>
            <b>{x.name || x.shelter_id}</b> · {x.reason.replace(/_/g, " ").toLowerCase()}{" "}
            <Label label={downReasonLabel(x.reason).label} title={downReasonLabel(x.reason).source} />
            {x.in_plan === false ? " · was not in the plan" : ""}
            {(x.evidence || x.rule) && <div className="small muted">{x.evidence || x.rule}</div>}
          </li>
        ))}
      </ul>
      {(r.at_risk_not_in_plan || []).length > 0 && (
        <div className="callout">
          <b>{r.at_risk_not_in_plan!.length} at-risk shelters have no capacity on file</b>, so losing them moves nobody in the plan. That is a
          paperwork gap, not safety:
          <ul>
            {r.at_risk_not_in_plan!.map((x) => (
              <li key={x.shelter_id}>{x.name || x.shelter_id}</li>
            ))}
          </ul>
        </div>
      )}
      {r.diff.newly_unassigned.length > 0 && (
        <>
          <h4>Newly without a destination</h4>
          <ul className="down-list">
            {r.diff.newly_unassigned.map((u) => (
              <li key={u.geo_id} onClick={() => act.focusOn(coords(u), 13)}>
                Tract {u.geo_id}: <b className="red">{fmt(u.persons)} people</b>
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="small muted">{r.note}</p>
      <PlanApproval kind="rehearsal" />
      <FixSection title="What the rehearsal says to fix" planFirst />
    </>
  );
}

export function LearnView() {
  const { s } = useStore();
  const shelters = useMemo(() => shelterViews(s).filter((x) => x.isPre), [s.map, s.brief, s.allocation]);
  const unrecorded = useMemo(() => shelters.filter((x) => x.wheelchair === "UNRECORDED"), [shelters]);
  const [shelterId, setShelterId] = useState<string>("");
  const [unaccompaniedEntry, setUnaccompaniedEntry] = useState<boolean>(true);
  const [medicalPower, setMedicalPower] = useState<boolean>(true);
  const [reportedBy, setReportedBy] = useState<string>("Field Shelter Coordinator");
  const [notes, setNotes] = useState<string>("Level entrance ramp, accessible restroom, and backup medical outlets verified on site.");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [records, setRecords] = useState<OperatorAssessment[]>([]);
  const mock = MOCK_ONLY || Object.values(s.sources).includes("mock");

  useEffect(() => {
    if (!shelterId && (unrecorded[0]?.id || shelters[0]?.id)) {
      setShelterId(unrecorded[0]?.id || shelters[0]?.id || "");
    }
  }, [shelterId, unrecorded, shelters]);

  useEffect(() => {
    if (mock) return;
    void api
      .assessments(s.countyFips)
      .then((r) => setRecords(r.assessments || []))
      .catch(() => {});
  }, [s.countyFips, mock]);

  const submit = async () => {
    const chosen = shelters.find((x) => x.id === shelterId);
    if (!chosen) return;
    setBusy(true);
    setErr(null);
    try {
      if (mock) {
        const rec: OperatorAssessment = {
          shelter_id: chosen.id,
          shelter_name: chosen.name,
          county: s.countyFips,
          operator_name: reportedBy,
          operator_statement: notes,
          audit_status: "REPORTED (UNVERIFIED)",
          wheelchair_status: unaccompaniedEntry ? "YES" : "NO",
          generator_status: medicalPower ? "YES" : "NO",
          timestamp_utc: new Date().toISOString(),
        };
        setRecords((prev) => [rec, ...prev]);
      } else {
        const res = await api.assessShelter({
          shelter_id: chosen.id,
          shelter_name: chosen.name,
          county: s.countyFips,
          operator_name: reportedBy,
          operator_statement: notes,
          unaccompanied_entry: unaccompaniedEntry,
          medical_power_outlets: medicalPower,
        });
        setRecords((prev) => [res.assessment, ...prev]);
      }
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="brief">
      <Header
        title="Learn: field assessment & after-action"
        subtitle="Close the UNRECORDED gap with first-party shelter operator reports (labeled REPORTED until audited)."
      />
      <p className="small">
        FEMA leaves wheelchair access blank on <b>{unrecorded.length} of {shelters.length}</b> pre-landfall shelters in this county.
        Record operator walk-throughs and post-storm actuals here so next season&apos;s inventory learns from ground truth rather than
        leaving fields blank.
      </p>
      <div className="approval" style={{ display: "grid", gap: 8 }}>
        <div className="approval-head">
          <b>Log shelter operator ADA / generator check</b>
          {mock && <span className="mock-badge">MOCK</span>}
        </div>
        <label className="small">
          Shelter{" "}
          <select value={shelterId} onChange={(e) => setShelterId(e.target.value)} style={{ maxWidth: "100%" }}>
            {shelters.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name} ({x.wheelchair})
              </option>
            ))}
          </select>
        </label>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <label className="small">
            <input type="checkbox" checked={unaccompaniedEntry} onChange={(e) => setUnaccompaniedEntry(e.target.checked)} /> Unaccompanied wheelchair entry + ADA restroom
          </label>
          <label className="small">
            <input type="checkbox" checked={medicalPower} onChange={(e) => setMedicalPower(e.target.checked)} /> Backup generator + medical power outlets
          </label>
        </div>
        <label className="small">
          Reported by{" "}
          <input value={reportedBy} onChange={(e) => setReportedBy(e.target.value)} />
        </label>
        <label className="small">
          Operator statement{" "}
          <input value={notes} onChange={(e) => setNotes(e.target.value)} style={{ width: "100%" }} />
        </label>
        <button className="btn btn-small" disabled={busy || !shelterId || !reportedBy.trim()} onClick={() => void submit()}>
          {busy ? "Recording…" : "Record operator assessment"}
        </button>
        {err && <div className="small red">{err}</div>}
      </div>
      {records.length > 0 && (
        <>
          <h4>Recorded operator assessments ({records.length})</h4>
          <ul className="down-list">
            {records.map((r, i) => (
              <li key={`${r.shelter_id}:${r.timestamp_utc || i}`}>
                <b>{r.shelter_name}</b> · wheelchair: <b>{r.wheelchair_status}</b> · generator: <b>{r.generator_status}</b>{" "}
                <Label label="RECORDED" title={r.audit_status} />
                <div className="small muted">
                  {r.operator_name || "Operator"} · {r.timestamp_utc ? new Date(r.timestamp_utc).toLocaleTimeString() : ""}{" "}
                  {r.operator_statement ? `— ${r.operator_statement}` : ""}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
