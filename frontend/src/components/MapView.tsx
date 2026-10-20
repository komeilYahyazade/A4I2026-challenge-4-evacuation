import {
  AdvancedMarker,
  APIProvider,
  Circle,
  InfoWindow,
  Map as GoogleMap,
  Polygon,
  Polyline,
  useMap,
} from "@vis.gl/react-google-maps";
import { Fragment, useEffect, useMemo, useState } from "react";
import { CONFIG } from "../config";
import type { Geometry, LatLng } from "../contract";
import { useStore } from "../store";
import { ACCESS, EXISTENCE, GAP_RED, MODE_COLOR, TRACT_BASE, TRACT_MUST, TRACT_TOP } from "../theme";
import { gapMarks, lines, shelterViews, tractViews, type ShelterView, type TractView } from "../views";
import { Legend } from "./Legend";
import { ShelterCard, TractCard } from "./Cards";

const LEE: LatLng = { lat: 26.56, lng: -81.87 };

export function MapView() {
  if (!CONFIG.mapsApiKey) {
    return (
      <div className="map-missing">
        <p>
          No Maps JavaScript API key configured. Set <code>VITE_MAPS_API_KEY</code> in <code>frontend/.env.local</code> or{" "}
          <code>MAPS_API_KEY</code> in the container env.
        </p>
      </div>
    );
  }
  return (
    <APIProvider apiKey={CONFIG.mapsApiKey} libraries={["marker"]}>
      <MapInner />
    </APIProvider>
  );
}

function MapInner() {
  const { s, d } = useStore();
  const shelters = useMemo(() => shelterViews(s), [s.map, s.allocation, s.brief, s.rehearsal, s.rehearsalMode]);
  const tracts = useMemo(() => tractViews(s), [s.map, s.allocation, s.brief, s.rehearsal, s.rehearsalMode]);
  const planLines = useMemo(() => lines(s, tracts, shelters), [s.allocation, s.rehearsal, s.rehearsalMode, tracts, shelters]);
  const gaps = useMemo(() => gapMarks(s, tracts, shelters), [s.brief, s.allocation, s.rehearsal, s.rehearsalMode, tracts, shelters]);
  const center = (s.map?.county && { lat: s.map.county.lat, lng: s.map.county.lng }) ||
    (s.brief?.county.lat != null && s.brief?.county.lng != null ? { lat: s.brief.county.lat, lng: s.brief.county.lng } : LEE);

  const sel = s.selected;
  const selShelter = sel?.kind === "shelter" ? shelters.find((x) => x.id === sel.id) : undefined;
  const selTract = sel?.kind === "tract" ? tracts.find((x) => x.geoId === sel.id) : undefined;
  const showStorm = s.layers.storm && (s.phase === "watch" || s.rehearsalMode);
  const advisory = s.storm?.advisories[Math.min(s.advisoryIdx, (s.storm?.advisories.length || 1) - 1)];
  const cone = s.rehearsalMode && s.rehearsal?.scenario.cone ? s.rehearsal.scenario.cone : advisory?.cone || null;

  return (
    <div className="map-wrap">
      <GoogleMap
        mapId={CONFIG.mapId}
        defaultCenter={center}
        defaultZoom={10}
        gestureHandling="greedy"
        clickableIcons={false}
        mapTypeControl={false}
        streetViewControl={false}
        fullscreenControl={false}
        onClick={() => d({ type: "select", sel: null })}
      >
        <Focus />
        {showStorm && cone && <Cone geometry={cone} />}
        {showStorm && advisory && (
          <Polyline
            path={advisory.track.map((p) => ({ lat: p.lat, lng: p.lng }))}
            strokeColor="#37474f"
            strokeWeight={2}
            strokeOpacity={0.9}
            clickable={false}
          />
        )}
        {showStorm &&
          advisory?.track.map((p, i) => (
            <AdvancedMarker key={`trk${i}`} position={{ lat: p.lat, lng: p.lng }} zIndex={4} title={`${p.valid_at || ""} ${p.wind_kt ?? ""} kt`}>
              <div className="track-dot">
                <span>{p.category || ""}</span>
                {p.wind_kt != null && <small>{p.wind_kt} kt</small>}
              </div>
            </AdvancedMarker>
          ))}
        {showStorm && s.storm?.best_track && (
          <Polyline
            path={s.storm.best_track.map((p) => ({ lat: p.lat, lng: p.lng }))}
            strokeColor="#b71c1c"
            strokeWeight={2}
            strokeOpacity={0.7}
            clickable={false}
            icons={[{ icon: { path: "M 0,-1 0,1", strokeOpacity: 1, scale: 2 }, offset: "0", repeat: "8px" }]}
          />
        )}
        {showStorm && s.storm?.landfall && (
          <AdvancedMarker position={s.storm.landfall} zIndex={5} title="Ian landfall (NHC)">
            <div className="landfall-mark">✱ Ian landfall</div>
          </AdvancedMarker>
        )}

        {s.layers.tracts && tracts.map((t) => <TractCircle key={t.geoId} t={t} onClick={() => d({ type: "select", sel: { kind: "tract", id: t.geoId } })} />)}

        {s.layers.lines &&
          planLines.map((l) => (
            <Polyline
              key={l.key}
              path={[l.from, l.to]}
              clickable={false}
              strokeColor={l.kind === "reassigned" ? "#ff6f00" : l.kind === "old" ? "#616161" : MODE_COLOR[l.mode] || "#1565c0"}
              strokeOpacity={l.kind === "old" ? 0 : l.kind === "reassigned" ? 0.95 : 0.55}
              strokeWeight={l.kind === "reassigned" ? 4 : Math.max(1.5, Math.min(5, Math.sqrt(l.persons) / 6))}
              icons={
                l.kind === "old"
                  ? [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 0.7, scale: 3 }, offset: "0", repeat: "12px" }]
                  : !l.routed && l.kind === "plan"
                    ? undefined
                    : undefined
              }
              zIndex={l.kind === "reassigned" ? 4 : 2}
            />
          ))}

        {s.layers.gaps &&
          gaps.map((g) => (
            <Circle
              key={g.key}
              center={g.at}
              radius={g.kind === "tract" ? 1400 : 700}
              strokeColor={GAP_RED}
              strokeWeight={s.highlightFix === g.idx ? 4 : 2}
              strokeOpacity={0.9}
              fillColor={GAP_RED}
              fillOpacity={g.severity === "HIGH" ? 0.22 : g.severity === "MEDIUM" ? 0.14 : 0.08}
              zIndex={3}
              onClick={() => {
                d({ type: "highlightFix", idx: g.idx });
                d({ type: "tab", tab: "brief" });
              }}
            />
          ))}

        {s.layers.gaps && (s.phase === "plan" || s.phase === "brief") && <SiteOptions tracts={tracts} />}

        {s.layers.shelters &&
          shelters.map((x) => (
            <AdvancedMarker
              key={x.id}
              position={x.at}
              zIndex={x.row ? 30 : x.down ? 40 : 20}
              title={x.name}
              onClick={() => d({ type: "select", sel: { kind: "shelter", id: x.id } })}
            >
              <ShelterPin x={x} verifying={s.verifying.includes(x.id)} />
            </AdvancedMarker>
          ))}

        {selShelter && (
          <InfoWindow position={selShelter.at} onCloseClick={() => d({ type: "select", sel: null })} maxWidth={360} headerContent={<b>{selShelter.name}</b>}>
            <ShelterCard x={selShelter} compact />
          </InfoWindow>
        )}
        {selTract && (
          <InfoWindow position={selTract.at} onCloseClick={() => d({ type: "select", sel: null })} maxWidth={340} headerContent={<b>Tract {selTract.geoId}</b>}>
            <TractCard t={selTract} />
          </InfoWindow>
        )}
      </GoogleMap>
      <Legend />
    </div>
  );
}

/** v1.2 site_options: a star where one designated site would seat the most people, halo over the tracts it serves. */
function SiteOptions({ tracts }: { tracts: TractView[] }) {
  const { s, d } = useStore();
  const opts = (s.allocation?.site_options || []).slice(0, 3);
  const tAt = new Map(tracts.map((t) => [t.geoId, t.at]));
  return (
    <>
      {opts.map((o, i) => {
        const at = o.ref && o.ref.lat != null && o.ref.lng != null ? { lat: o.ref.lat, lng: o.ref.lng } : tAt.get(o.ref?.id || "");
        if (!at) return null;
        const hl = s.highlightFix === 2000 + i || (i === 0 && s.highlightFix == null);
        return (
          <Fragment key={`opt${i}`}>
            {(o.tracts_covered || []).map((g) => {
              const c = tAt.get(g);
              return c ? (
                <Circle key={`${i}:${g}`} center={c} radius={900} strokeColor="#ffb300" strokeWeight={hl ? 2 : 1} strokeOpacity={0.9} fillColor="#ffca28" fillOpacity={hl ? 0.25 : 0.1} clickable={false} zIndex={2} />
              ) : null;
            })}
            <AdvancedMarker position={at} zIndex={60} title={o.subject} onClick={() => d({ type: "highlightFix", idx: 2000 + i })}>
              <div className={`site-star ${hl ? "hl" : ""}`}>
                ★<span>{fmtShort(o.persons_covered)}</span>
              </div>
            </AdvancedMarker>
          </Fragment>
        );
      })}
    </>
  );
}

function fmtShort(n?: number) {
  if (n == null) return "";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function Focus() {
  const map = useMap();
  const { s } = useStore();
  useEffect(() => {
    if (!map || !s.focus) return;
    map.panTo(s.focus.at);
    if (s.focus.zoom) map.setZoom(s.focus.zoom);
  }, [map, s.focus?.n]);
  // First time a county centroid arrives, centre on it.
  const [centered, setCentered] = useState(false);
  useEffect(() => {
    if (!map || centered) return;
    const c = s.map?.county || (s.brief?.county.lat != null ? s.brief.county : null);
    if (c && c.lat != null && c.lng != null) {
      map.panTo({ lat: c.lat, lng: c.lng });
      setCentered(true);
    }
  }, [map, s.map, s.brief, centered]);
  return null;
}

function Cone({ geometry }: { geometry: Geometry }) {
  const polys: number[][][][] = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  return (
    <>
      {polys.map((rings, i) => (
        <Polygon
          key={i}
          paths={rings.map((ring) => ring.map(([lng, lat]) => ({ lat, lng })))}
          strokeColor="#263238"
          strokeOpacity={0.8}
          strokeWeight={1.5}
          fillColor="#eceff1"
          fillOpacity={0.35}
          clickable={false}
          zIndex={1}
        />
      ))}
    </>
  );
}

function TractCircle({ t, onClick }: { t: TractView; onClick: () => void }) {
  const ranked = !!t.entry;
  const mustLeave = !ranked && !!t.mustLeave;
  const nv = t.householdsNoVehicle || t.entry?.people?.households_no_vehicle || 0;
  const radius = Math.max(250, 55 * Math.sqrt(nv));
  const unassigned = (t.unassigned || 0) > 0;
  return (
    <Circle
      center={t.at}
      radius={radius}
      onClick={onClick}
      strokeColor={unassigned ? GAP_RED : ranked ? TRACT_TOP : mustLeave ? TRACT_MUST : TRACT_BASE}
      strokeWeight={unassigned ? 2.5 : ranked || mustLeave ? 2 : 0.6}
      strokeOpacity={0.9}
      fillColor={ranked ? TRACT_TOP : mustLeave ? TRACT_MUST : TRACT_BASE}
      fillOpacity={ranked || mustLeave ? 0.32 : Math.min(0.28, 0.06 + (t.share || 0) * 1.2)}
      zIndex={ranked || mustLeave ? 6 : 1}
    />
  );
}

function floodExposed(x: ShelterView): boolean {
  const share = x.siteHazard?.coastal_flood_exposed_share;
  return typeof share === "number" && share > 0;
}

function ShelterPin({ x, verifying }: { x: ShelterView; verifying: boolean }) {
  const a = ACCESS[x.access];
  const title = `${x.name}\n${a.text}\n${EXISTENCE[x.existence].text}${x.down ? `\nOFFLINE in rehearsal: ${x.down}` : ""}${x.isPre ? "" : "\nPOST-impact only: not an evacuation destination"}${x.nameInactive ? "\nFEMA's own name says INACTIVE, yet it is still designated" : ""}`;
  return (
    <div
      className={`pin ex-${x.existence.toLowerCase()} ${x.isPre ? "" : "pin-post"} ${x.down ? "pin-down" : ""} ${x.excluded ? "pin-excluded" : ""} ${verifying ? "pin-verifying" : ""}`}
      style={{ background: a.color }}
      title={title}
    >
      <span className="pin-glyph">{a.glyph}</span>
      {x.down && <span className="pin-x">✕</span>}
      {x.isMedical && <span className="pin-med">+</span>}
      {floodExposed(x) && <span className="pin-flood" title="Site flood exposure (APPROXIMATE)">≈</span>}
      {x.nameInactive && <span className="pin-inactive">INACTIVE</span>}
    </div>
  );
}
