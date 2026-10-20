import { useEffect, useMemo, useState } from "react";
import { AdvancedMarker, APIProvider, Map, useMap } from "@vis.gl/react-google-maps";
import type { Affected, Allocation, Brief, CountyMap, GeoJsonGeometry, Rehearsal, Selection, ShelterVerification, Step, StormTrack, Tract } from "../types";
import { evidence, fmt, percentileRanks, rampColor, tractLabel } from "../format";
import { GoogleMaps } from "./Attribution";

export interface MapConfig { MAPS_API_KEY?: string; MAP_ID?: string }

type Hover =
  | { kind: "tract"; t: Tract }
  | { kind: "shelter"; id: string; name: string; label: string; extra: string }
  | { kind: "care"; name: string; beds: number | null; km: number | null | undefined }
  | null;

function FitCounty({ data }: { data: CountyMap }) {
  const map = useMap();
  useEffect(() => {
    if (!map || !data.tracts.length) return;
    const b = new google.maps.LatLngBounds();
    data.tracts.forEach((t) => b.extend({ lat: t.lat, lng: t.lon }));
    map.fitBounds(b, 24);
  }, [map, data.county_fips]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

function PanTo({ target }: { target: { lat: number; lng: number } | null }) {
  const map = useMap();
  useEffect(() => {
    if (map && target) map.panTo(target);
  }, [map, target?.lat, target?.lng]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

/** Tracts as pixel-sized dots (centroids): size = households without a car, colour = need percentile.
 *  Metre-radius circles shrink to ~3 px at county zoom, so these stay readable at every zoom. */
function TractDots({ tracts, highlight, selected, onHover, onClick }: {
  tracts: Tract[]; highlight: Set<string>; selected: string | null;
  onHover: (t: Tract | null) => void; onClick: (geoId: string) => void;
}) {
  const ranks = useMemo(() => percentileRanks(tracts.map((t) => t.need_score)), [tracts]);
  return (
    <>
      {tracts.map((t, i) => {
        const size = Math.round(7 + 0.8 * Math.sqrt(Math.max(t.households_no_vehicle ?? 0, 0)));
        const hl = highlight.has(t.geo_id) || t.geo_id === selected;
        return (
          <AdvancedMarker key={t.geo_id} position={{ lat: t.lat, lng: t.lon }} zIndex={hl ? 25 : 1} onClick={() => onClick(t.geo_id)}>
            <div onMouseEnter={() => onHover(t)} onMouseLeave={() => onHover(null)}
              style={{ width: size, height: size, borderRadius: "50%", background: rampColor(ranks[i]), opacity: hl ? 0.95 : 0.72,
                border: hl ? "3px solid #111" : "1px solid rgba(255,255,255,.9)", transform: "translateY(50%)", cursor: "pointer" }} />
          </AdvancedMarker>
        );
      })}
    </>
  );
}

function ServeLines({ lines }: { lines: { from: google.maps.LatLngLiteral; to: google.maps.LatLngLiteral; dropped: boolean }[] }) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    const polys = lines.map((l) => new google.maps.Polyline({
      map, path: [l.from, l.to], strokeColor: l.dropped ? "#d03b3b" : "#1c5cab", strokeOpacity: 0.85, strokeWeight: 2.5,
      icons: l.dropped ? [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 1, scale: 3 }, offset: "0", repeat: "10px" }] : undefined,
      zIndex: 2,
    }));
    return () => polys.forEach((p) => p.setMap(null));
  }, [map, lines]);
  return null;
}

/** GeoJSON Polygon / MultiPolygon rings -> google.maps paths. */
function rings(g: GeoJsonGeometry | null | undefined): google.maps.LatLngLiteral[][] {
  if (!g) return [];
  const toPath = (ring: number[][]) => ring.map(([lng, lat]) => ({ lat, lng }));
  if (g.type === "Polygon") return (g.coordinates as number[][][]).map(toPath);
  if (g.type === "MultiPolygon") return (g.coordinates as number[][][][]).flatMap((poly) => poly.map(toPath));
  return [];
}

/** Hurricane Ian overlay for the rehearsal step: NHC cone, forecast track, observed best track. */
function StormLayer({ cone, forecast, best }: { cone: GeoJsonGeometry | null; forecast: { lat: number; lon: number }[]; best: { lat: number; lon: number }[] }) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    const objs: (google.maps.Polygon | google.maps.Polyline)[] = [];
    const paths = rings(cone);
    if (paths.length) objs.push(new google.maps.Polygon({ map, paths, strokeColor: "#ec835a", strokeWeight: 2, fillColor: "#ec835a", fillOpacity: 0.12, clickable: false, zIndex: 0 }));
    if (forecast.length > 1) objs.push(new google.maps.Polyline({ map, path: forecast.map((p) => ({ lat: p.lat, lng: p.lon })), strokeOpacity: 0, zIndex: 1,
      icons: [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 0.9, strokeColor: "#d03b3b", scale: 3 }, offset: "0", repeat: "12px" }] }));
    if (best.length > 1) objs.push(new google.maps.Polyline({ map, path: best.map((p) => ({ lat: p.lat, lng: p.lon })), strokeColor: "#7a1f1f", strokeWeight: 3, strokeOpacity: 0.85, zIndex: 1 }));
    return () => objs.forEach((o) => o.setMap(null));
  }, [map, cone, forecast, best]);
  return null;
}

function Lines({ lines, color, dashed }: { lines: { from: google.maps.LatLngLiteral; to: google.maps.LatLngLiteral }[]; color: string; dashed?: boolean }) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    const polys = lines.map((l) => new google.maps.Polyline({ map, path: [l.from, l.to], strokeColor: color, strokeOpacity: dashed ? 0 : 0.8, strokeWeight: 2, zIndex: 2,
      icons: dashed ? [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 0.9, strokeColor: color, scale: 2.5 }, offset: "0", repeat: "9px" }] : undefined }));
    return () => polys.forEach((p) => p.setMap(null));
  }, [map, lines, color, dashed]);
  return null;
}

export interface StepOverlay {
  step: Step;
  ranks: { id: string; kind: "tract" | "care_facility"; rank: number; lat: number; lon: number }[];
  rehearsal: Rehearsal | null; storm: StormTrack | null; advisory: string; alloc: Allocation | null;
}

export function MapView({ cfg, data, brief, verifications, highlight, selection, onSelect, overlay }: {
  cfg: MapConfig; data: CountyMap | null; brief: Brief | null; verifications: Record<string, ShelterVerification>;
  highlight: Affected; selection: Selection; onSelect: (s: Selection) => void; overlay: StepOverlay;
}) {
  const [layers, setLayers] = useState({ tracts: true, shelters: true, care: true, lines: true });
  const [hover, setHover] = useState<Hover>(null);
  const dest = useMemo(() => new globalThis.Map((brief?.destinations ?? []).map((d) => [d.shelter.shelter_id, d])), [brief]);
  const hlTracts = useMemo(() => new Set(highlight.tracts), [highlight]);
  const lines = useMemo(() => {
    if (!data || !brief) return [];
    // A destination serves tracts, or nursing homes ("ccn:<id>") in the medically dependent track.
    const origin = new globalThis.Map<string, { lat: number; lon: number }>([
      ...data.tracts.map((t) => [t.geo_id, t] as [string, { lat: number; lon: number }]),
      ...data.care_facilities.map((c) => [`ccn:${c.ccn}`, c] as [string, { lat: number; lon: number }]),
    ]);
    return brief.destinations.flatMap((d) => d.serves.flatMap((g) => {
      const t = origin.get(g);
      return t ? [{ from: { lat: t.lat, lng: t.lon }, to: { lat: d.shelter.lat, lng: d.shelter.lon }, dropped: d.verdict === "do_not_rely" }] : [];
    }));
  }, [data, brief]);
  const target = useMemo(() => {
    if (!data || !selection) return null;
    if (selection.kind === "shelter") { const s = data.shelters.find((x) => x.shelter_id === selection.id); return s ? { lat: s.lat, lng: s.lon } : null; }
    if (selection.kind === "tract") { const t = data.tracts.find((x) => x.geo_id === selection.id); return t ? { lat: t.lat, lng: t.lon } : null; }
    const c = data.care_facilities.find((x) => x.ccn === selection.id); return c ? { lat: c.lat, lng: c.lon } : null;
  }, [data, selection]);

  if (!cfg.MAPS_API_KEY) {
    return <div className="map-missing">The map needs a Maps JavaScript API key (MAPS_API_KEY on the web service).<br />Everything else on this page works without it.</div>;
  }
  return (
    <div className="map-wrap">
      <APIProvider apiKey={cfg.MAPS_API_KEY}>
        <Map mapId={cfg.MAP_ID || "DEMO_MAP_ID"} defaultCenter={{ lat: 26.56, lng: -81.89 }} defaultZoom={10}
          gestureHandling="greedy" clickableIcons={false} mapTypeControl={false} streetViewControl={false} fullscreenControl={false}
          style={{ width: "100%", height: "100%" }}>
          {data && <FitCounty data={data} />}
          <PanTo target={target} />
          {data && layers.tracts && (
            <TractDots tracts={data.tracts} highlight={hlTracts} selected={selection?.kind === "tract" ? selection.id : null}
              onHover={(t) => setHover(t ? { kind: "tract", t } : null)} onClick={(id) => onSelect({ kind: "tract", id })} />
          )}
          {layers.lines && overlay.step === 2 && <ServeLines lines={lines} />}
          {data && layers.shelters && data.shelters.map((s) => {
            const d = dest.get(s.shelter_id) ?? null;
            const v = verifications[s.shelter_id] ?? d?.verification ?? null;
            const ev = evidence(s, v, d);
            const ring = highlight.shelters.includes(s.shelter_id) || (selection?.kind === "shelter" && selection.id === s.shelter_id);
            return (
              <AdvancedMarker key={s.shelter_id} position={{ lat: s.lat, lng: s.lon }} zIndex={d ? 30 : ring ? 40 : 10}
                onClick={() => onSelect({ kind: "shelter", id: s.shelter_id })} title={s.name}>
                <div className={`pin ${ev.cls}${ev.live ? " live" : ""}${d ? " big" : ""}${ring ? " ring" : ""}`}
                  onMouseEnter={() => setHover({ kind: "shelter", id: s.shelter_id, name: s.name, label: ev.label,
                    extra: `${s.usage ?? "usage n/a"} · capacity ${s.evacuation_capacity ?? "not recorded"}${d ? " · in the brief: " + d.verdict.replace(/_/g, " ") : ""}` })}
                  onMouseLeave={() => setHover(null)}>{ev.glyph}</div>
              </AdvancedMarker>
            );
          })}
          {data && layers.care && data.care_facilities.map((c) => {
            const ring = highlight.care_facilities.includes(c.ccn) || (selection?.kind === "care" && selection.id === c.ccn);
            return (
              <AdvancedMarker key={c.ccn} position={{ lat: c.lat, lng: c.lon }} zIndex={20} onClick={() => onSelect({ kind: "care", id: c.ccn })} title={c.name}>
                <div className={`care-pin${ring ? " ring" : ""}`} onMouseEnter={() => setHover({ kind: "care", name: c.name, beds: c.certified_beds, km: c.nearest_generator_yes_km })}
                  onMouseLeave={() => setHover(null)}>H</div>
              </AdvancedMarker>
            );
          })}
          {overlay.step === 3 && (() => {
            const adv = overlay.storm?.advisories.find((x) => x.advisory === overlay.advisory);
            return <StormLayer cone={overlay.rehearsal?.cone ?? adv?.cone ?? null} forecast={adv?.track ?? []} best={overlay.storm?.best_track ?? []} />;
          })()}
          {overlay.step === 3 && overlay.rehearsal && (
            <Lines color="#eb6834" dashed lines={overlay.rehearsal.reassigned.filter((m) => m.tract_lat != null && m.to_lat != null)
              .map((m) => ({ from: { lat: m.tract_lat!, lng: m.tract_lon! }, to: { lat: m.to_lat!, lng: m.to_lon! } }))} />
          )}
          {overlay.step === 3 && overlay.rehearsal?.shelters_down.concat(overlay.rehearsal.at_risk_not_in_plan).filter((x) => x.lat != null).map((x) => (
            <AdvancedMarker key={`down-${x.shelter_id}`} position={{ lat: x.lat!, lng: x.lon! }} zIndex={60} onClick={() => onSelect({ kind: "shelter", id: x.shelter_id })} title={x.name}>
              <div className={`tri${x.in_plan ? "" : " faint"}`} onMouseEnter={() => setHover({ kind: "shelter", id: x.shelter_id, name: x.name, label: x.in_plan ? "Taken offline in the Ian replay" : "At risk, but no capacity on file (not in the plan)", extra: x.evidence })} onMouseLeave={() => setHover(null)}>▲</div>
            </AdvancedMarker>
          ))}
          {overlay.step === 4 && overlay.alloc?.site_options.filter((o) => o.lat != null).map((o, i) => (
            <AdvancedMarker key={`site-${o.geo_id}`} position={{ lat: o.lat!, lng: o.lon! }} zIndex={70} onClick={() => onSelect({ kind: "tract", id: o.geo_id })}>
              <div className="site-pin" onMouseEnter={() => setHover({ kind: "shelter", id: o.geo_id, name: i === 0 ? "Designate one site here" : `Next site #${i + 1}`, label: `Seats ${o.persons_covered.toLocaleString()} people without a car`, extra: `${o.tracts_covered.length} neighbourhoods within 15 km` })} onMouseLeave={() => setHover(null)}>★ {o.persons_covered.toLocaleString()}</div>
            </AdvancedMarker>
          ))}
          {overlay.step === 1 && overlay.ranks.map((r) => (
            <AdvancedMarker key={`rank-${r.id}`} position={{ lat: r.lat, lng: r.lon }} zIndex={80} onClick={() => onSelect({ kind: r.kind === "tract" ? "tract" : "care", id: r.id })}>
              <div className={`rank-pin${r.kind === "care_facility" ? " must" : ""}`}>{r.rank}</div>
            </AdvancedMarker>
          ))}
        </Map>
      </APIProvider>

      <div className="map-overlay map-layers">
        {(["tracts", "shelters", "care", "lines"] as const).map((k) => (
          <label key={k}><input type="checkbox" checked={layers[k]} onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} />
            {{ tracts: "Tracts", shelters: "Shelters", care: "Nursing homes", lines: "Tract → shelter" }[k]}</label>
        ))}
      </div>
      {hover && (
        <div className="map-overlay map-hover">
          {hover.kind === "tract" && (<>
            <b>{tractLabel(hover.t.geo_id)}</b> <span className="muted small">{hover.t.geo_id}</span>
            <div>{fmt(hover.t.households_no_vehicle)} households without a car <span className="tag recorded">ACS</span></div>
            <div>{fmt(hover.t.pct_age_65_plus, 0)}% aged 65+ · {fmt(hover.t.pct_disability, 0)}% with a disability</div>
            {hover.t.pct_mobility != null && <div>{fmt(hover.t.pct_mobility, 0)}% mobility disability <span className="tag modeled">MODELED</span></div>}
            <div className="muted small">nearest shelter {fmt(hover.t.nearest_shelter_km, 1)} km · recorded-accessible {fmt(hover.t.nearest_wheelchair_yes_km, 1)} km (straight line)</div>
          </>)}
          {hover.kind === "shelter" && (<><b>{hover.name}</b> <span className="muted small">{hover.id}</span><div>{hover.label}</div><div className="muted small">{hover.extra}</div></>)}
          {hover.kind === "care" && (<><b>{hover.name}</b><div>Nursing home · {fmt(hover.beds)} certified beds (CMS)</div>
            <div className="muted small">nearest shelter with a recorded generator {fmt(hover.km, 1)} km (straight line)</div></>)}
        </div>
      )}
      <div className="map-overlay map-legend">
        <div className="row">
          <b>Shelters</b>
          <span><span className="pin yes" style={{ display: "inline-grid", width: 16, height: 16, fontSize: 10 }}>✓</span> recorded yes</span>
          <span><span className="pin yes live" style={{ display: "inline-grid", width: 16, height: 16, fontSize: 10 }}>✓</span> live yes (<GoogleMaps />)</span>
          <span><span className="pin no" style={{ display: "inline-grid", width: 16, height: 16, fontSize: 10 }}>–</span> no</span>
          <span><span className="pin conflict" style={{ display: "inline-grid", width: 16, height: 16, fontSize: 10 }}>!</span> conflict</span>
          <span><span className="pin closed" style={{ display: "inline-grid", width: 16, height: 16, fontSize: 10 }}>✕</span> closed / surge / do not rely</span>
          <span><span className="pin unrecorded" style={{ display: "inline-grid", width: 16, height: 16, fontSize: 10 }}>?</span> not recorded</span>
        </div>
        <div className="row muted small" style={{ marginTop: 4 }}>
          <span><span className="care-pin" style={{ display: "inline-grid", width: 13, height: 13, fontSize: 8 }}>H</span> nursing home</span>
          <span>Circles: tracts, size = households without a car, darker = higher need</span>
          <span>Big pins = in the brief</span>
          {overlay.step === 3 && <span><b style={{ color: "#d03b3b" }}>▲</b> taken offline · <span style={{ color: "#ec835a" }}>▭</span> NHC cone · dashed: forecast track and re-routed pickups</span>}
          {overlay.step === 4 && <span><b style={{ color: "#eda100" }}>★</b> where one new site seats the most people</span>}
        </div>
      </div>
    </div>
  );
}
