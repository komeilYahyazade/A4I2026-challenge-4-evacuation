import { useEffect, useMemo, useState } from "react";
import { AdvancedMarker, APIProvider, Map, useMap } from "@vis.gl/react-google-maps";
import type { Affected, Brief, CountyMap, Selection, ShelterVerification, Tract } from "../types";
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

export function MapView({ cfg, data, brief, verifications, highlight, selection, onSelect }: {
  cfg: MapConfig; data: CountyMap | null; brief: Brief | null; verifications: Record<string, ShelterVerification>;
  highlight: Affected; selection: Selection; onSelect: (s: Selection) => void;
}) {
  const [layers, setLayers] = useState({ tracts: true, shelters: true, care: true, lines: true });
  const [hover, setHover] = useState<Hover>(null);
  const dest = useMemo(() => new globalThis.Map((brief?.destinations ?? []).map((d) => [d.shelter.shelter_id, d])), [brief]);
  const hlTracts = useMemo(() => new Set(highlight.tracts), [highlight]);
  const lines = useMemo(() => {
    if (!data || !brief) return [];
    const byTract = new globalThis.Map(data.tracts.map((t) => [t.geo_id, t]));
    return brief.destinations.flatMap((d) => d.serves.flatMap((g) => {
      const t = byTract.get(g);
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
          {layers.lines && <ServeLines lines={lines} />}
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
        </div>
      </div>
    </div>
  );
}
