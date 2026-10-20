import type { WhereRow } from "../contract";
import { ago, fmt, pct } from "../derive";
import { useStore } from "../store";
import { ACCESS, EXISTENCE } from "../theme";
import type { ShelterView, TractView } from "../views";
import { Basis, Label, Tri } from "./Badges";
import { MapsAttribution, protectGM } from "./MapsAttribution";

export function CapacityText({ capacity, status, implausible }: { capacity: number | null | undefined; status?: string; implausible?: boolean }) {
  if (capacity == null || status === "UNRECORDED")
    return (
      <span className="cap-unrec" title="Capacity is blank in the FEMA record. Not zero.">
        capacity not recorded
      </span>
    );
  if (implausible)
    return (
      <span className="cap-implausible" title="Flagged as implausible and excluded from totals. The federal number is left as filed.">
        {fmt(capacity)} as filed (implausible, excluded)
      </span>
    );
  return <span>{fmt(capacity)} spaces</span>;
}

/** The live (Google Maps) half of a shelter row. Attribution renders immediately after the claim. */
export function LiveBlock({ row }: { row: WhereRow }) {
  const live = row.live;
  if (!live) return null;
  const usable = live.status === "VERIFIED" && live.sources.length > 0;
  return (
    <div className={`live-block verdict-${row.verdict.toLowerCase()}`}>
      <div className="live-head">
        <Label label={live.label} />
        <span className="muted">checked {ago(live.verified_at)}</span>
        <span className={`verdict v-${row.verdict.toLowerCase()}`}>{row.verdict.replace(/_/g, " ")}</span>
      </div>
      {usable ? (
        <ul className="live-facts">
          <li>
            Exists: <b>{String(live.exists)}</b>
            {live.closed && live.closed !== "no" && live.closed !== "unknown" ? ` · closed ${live.closed}` : ""}
          </li>
          {live.current_name && <li>Known on Google Maps as: <b translate="no">{live.current_name}</b></li>}
          <li>
            Wheelchair entrance: <b>{live.wheelchair_entrance || "unknown"}</b>
          </li>
          {live.drive_minutes != null && (
            <li>
              Drive: <Basis basis={live.drive_basis} minutes={live.drive_minutes} />
              {live.road_km != null ? ` · ${fmt(live.road_km, 1)} km by road` : ""}
            </li>
          )}
        </ul>
      ) : (
        <p className="muted">{live.notes || "Google Maps returned no source for this building, so its answer is not evidence."}</p>
      )}
      {row.verdict === "DROP" && (
        <div className="drop-callout">
          Dropped from destinations{(row.flags || []).includes("NAME_SAYS_INACTIVE") ? ". FEMA's own name says INACTIVE" : ""}. Confirm in person before anyone is sent here.
          {(row.flags || []).includes("SUCCESSOR_AT_ADDRESS") && live.current_name && (
            <div className="successor">
              Now at this address: <b translate="no">{live.current_name}</b>
            </div>
          )}
        </div>
      )}
      {row.verdict === "DROP" && (row.flags || []).includes("SUCCESSOR_AT_ADDRESS") && (
        <MapsAttribution sources={live.sources} fallbackTitle={live.current_name || row.name || row.shelter_name} />
      )}
      <p className="claim">
        {protectGM(row.why || "")}
        {row.accessibility && (
          <>
            {" "}
            <Label label={row.accessibility.label} /> {protectGM(row.accessibility.summary)}
          </>
        )}
      </p>
      <MapsAttribution sources={live.sources} fallbackTitle={row.name || row.shelter_name} />
      {(row.flags || []).length > 0 && (
        <div className="flags">
          {row.flags!.map((f) => (
            <span key={f} className="flag">
              {f.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function VerifyButton({ id, origin }: { id: string; origin?: string }) {
  const { s, act } = useStore();
  const busy = s.verifying.includes(id);
  if (s.rehearsalMode)
    return <span className="muted small">No live checks in rehearsal mode.</span>;
  return (
    <button className="btn btn-small" disabled={busy} onClick={() => act.verify([id], origin)}>
      {busy ? "Checking Google Maps…" : "Verify live"}
    </button>
  );
}

export function ShelterCard({ x, compact }: { x: ShelterView; compact?: boolean }) {
  const a = ACCESS[x.access];
  return (
    <div className={`shelter-card ${compact ? "compact" : ""}`}>
      {x.down && (
        <div className="down-banner">
          OFFLINE in the Ian rehearsal: {x.down.replace(/_/g, " ").toLowerCase()}
        </div>
      )}
      {x.address && <div className="muted small">{x.address}</div>}
      <div className="row-chips">
        <span className="swatch" style={{ background: a.color }}>
          {a.glyph}
        </span>
        <span className="small">{a.text}</span>
      </div>
      <div className="small muted">{EXISTENCE[x.existence].text}</div>
      <div className="record">
        <Label label="RECORDED" title="FEMA National Shelter System, a historical registry" />{" "}
        <CapacityText capacity={x.capacity} status={x.capacityStatus} implausible={x.implausible} /> · {x.usage || "usage UNRECORDED"}
        {x.isMedical ? " · medical-needs" : ""}
        <div className="row-chips">
          <Tri name="Wheelchair" value={x.wheelchair} />
          <Tri name="Generator" value={x.generator} />
          <Tri name="Surge zone" value={x.surge} bad="YES" />
        </div>
        {x.surge === "UNRECORDED" && <div className="small muted">Surge flag blank: unflagged does not mean outside the zone.</div>}
        {x.nameInactive && (
          <div className="inactive-callout">
            FEMA's own record names this site <b>INACTIVE</b>, yet it is still designated {x.usage || "EVAC"}.
          </div>
        )}
        {x.siteHazard && <SiteHazardLine h={x.siteHazard} />}
      </div>
      {x.assigned != null && (
        <div className="small">
          Plan sends <b>{fmt(x.assigned)}</b> people here
          {x.utilization != null ? ` (${pct(x.utilization)} of recorded capacity)` : ""}
          {x.excluded ? ` · excluded: ${x.excluded.replace(/_/g, " ").toLowerCase()}` : ""}
        </div>
      )}
      {x.row?.live ? <LiveBlock row={x.row} /> : <div className="not-checked">
        <span className="muted small">FEMA record only. Not checked against Google Maps yet.</span>{" "}
        <VerifyButton id={x.id} origin={x.row?.for_tract} />
      </div>}
    </div>
  );
}

export function SiteHazardLine({ h }: { h: NonNullable<ShelterView["siteHazard"]> }) {
  const share = h.coastal_flood_exposed_share;
  return (
    <div className="small site-hazard" title={h.note || h.method}>
      <Label label={h.label || "APPROXIMATE"} title={h.method} /> Site flood exposure:{" "}
      {share == null ? "not available" : <b>{pct(share)} of the nearest tract's people exposed to coastal flooding</b>}
      {h.km_to_centroid != null ? ` · nearest tract ${h.nearest_geo_id} at ${fmt(h.km_to_centroid, 1)} km` : ""}
    </div>
  );
}

export function TractCard({ t }: { t: TractView }) {
  const e = t.entry;
  return (
    <div className="tract-card">
      {e && (
        <div className="small">
          Rank <b>#{e.rank}</b> · priority {fmt(e.priority_score, 2)}
        </div>
      )}
      <div className="small">
        <b>{fmt(t.householdsNoVehicle)}</b> households without a vehicle
        {t.share != null ? ` (${pct(t.share)})` : ""} <Label label="RECORDED" title="Census ACS vehicle ownership. Owning no car is not the same as having no ride." />
      </div>
      {t.persons != null && <div className="small muted">≈ {fmt(t.persons)} people (households × persons per household)</div>}
      {t.demand != null && (
        <div className="small">
          Plan demand {fmt(t.demand)} people{t.unassigned ? <> · <b className="red">{fmt(t.unassigned)} unassigned</b></> : " · all assigned"}
        </div>
      )}
      {e?.reasons && (
        <ul className="reasons">
          {e.reasons.map((r, i) => (
            <li key={i}>
              {r.text} <Label label={r.label} title={r.source} />
            </li>
          ))}
        </ul>
      )}
      {e?.mobility?.recommendation && <p className="small">{e.mobility.recommendation}</p>}
    </div>
  );
}
