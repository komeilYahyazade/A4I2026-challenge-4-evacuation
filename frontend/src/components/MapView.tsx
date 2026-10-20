import { AdvancedMarker, APIProvider, Circle, Map as GoogleMap, Polygon, Polyline, useMap } from "@vis.gl/react-google-maps";
import { Fragment, useEffect, useMemo, useState } from "react";
import { CONFIG } from "../config";
import type { Geometry, LatLng } from "../contract";
import { coords } from "../derive";
import { useStore } from "../store";
import { T } from "../theme";
import { gapMarks, lines, shelterViews, tractViews, type ShelterView, type TractView } from "../views";
import { Legend, type LegendKey } from "./Legend";
import { tractName } from "./ui";

const LEE: LatLng = { lat: 26.56, lng: -81.87 };

export function MapView() {
  if (!CONFIG.mapsApiKey) {
    return (
      <div className="map-missing">
        <p>
          No Maps JavaScript API key configured. Set <code>VITE_MAPS_API_KEY</code> in <code>frontend/.env.local</code> or <code>MAPS_API_KEY</code> in the
          container env.
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

/** Marker state, by priority. Each has its own shape AND colour; meaning is never colour alone. */
type Mark = "dropped" | "offline" | "conflict" | "renamed" | "confirmed" | "recYes" | "recNo" | "unrec";

function markOf(x: ShelterView, step: number): Mark {
  if (step === 3 && x.down) return "offline";
  if (x.existence === "DROPPED") return "dropped";
  if (x.existence === "CONFLICT" || x.access === "CONFLICT") return "conflict";
  if (x.existence === "RENAMED") return "renamed";
  if (x.existence === "VERIFIED_LIVE") return "confirmed";
  if (x.wheelchair === "YES") return "recYes";
  if (x.wheelchair === "NO") return "recNo";
  return "unrec";
}

const MARK_GLYPH: Partial<Record<Mark, string>> = { dropped: "✕", offline: "✕", conflict: "!", renamed: "↻", confirmed: "✓" };
const MARK_LEGEND: Record<Mark, LegendKey> = {
  dropped: "dropped",
  offline: "offline",
  conflict: "conflict",
  renamed: "renamed",
  confirmed: "confirmed",
  recYes: "recYes",
  recNo: "recNo",
  unrec: "unrec",
};

function MapInner() {
  const { s, d } = useStore();
  const shelters = useMemo(() => shelterViews(s), [s.map, s.allocation, s.brief, s.rehearsal, s.rehearsalMode]);
  const tracts = useMemo(() => tractViews(s), [s.map, s.allocation, s.brief, s.rehearsal, s.rehearsalMode]);
  const planLines = useMemo(() => lines(s, tracts, shelters), [s.allocation, s.rehearsal, s.rehearsalMode, tracts, shelters]);
  const gaps = useMemo(() => gapMarks(s, tracts, shelters), [s.brief, s.allocation, s.rehearsal, s.rehearsalMode, tracts, shelters]);
  const step = s.step;
  const center = (s.map?.county && { lat: s.map.county.lat, lng: s.map.county.lng }) || LEE;
  const sel = s.selected;
  const legend = new Set<LegendKey>();
  const pick = (kind: "shelter" | "tract" | "fix", id: string) => d({ type: "select", sel: { kind, id, from: "map" } });

  // ---- Step 1: who can't leave ----
  const ranked = new Map<string, number>();
  for (const t of s.brief?.who_cannot_leave || []) ranked.set(t.geo_id, t.rank);
  const mustRanked = new Map<string, number>();
  for (const t of s.brief?.must_leave_may_need_help || []) mustRanked.set(t.geo_id, t.rank);
  if (step === 1) {
    legend.add("tractNeed");
    if (ranked.size) legend.add("tractRank");
    if (mustRanked.size) legend.add("tractMust");
    if ((s.map?.care_facilities || []).length) legend.add("nursing");
  }

  // ---- Step 2: shelters and assignment lines ----
  const showShelters = step === 2 || step === 3;
  const marks = showShelters ? shelters.map((x) => ({ x, m: markOf(x, step) })) : [];
  for (const { x, m } of marks) {
    if (step === 3 && m !== "offline") {
      legend.add("context");
      continue;
    }
    legend.add(MARK_LEGEND[m]);
    if (x.nameInactive) legend.add("inactive");
  }
  const focusTract = s.hoverTract || (sel?.kind === "tract" ? sel.id : null);
  const showLines = step === 2 || step === 3;
  if (step === 2 && planLines.length) legend.add("line");
  if (step === 3 && planLines.some((l) => l.kind === "reassigned")) legend.add("reassigned");
  if (step === 3 && planLines.some((l) => l.kind === "old")) legend.add("oldRun");

  // ---- Step 3: storm ----
  const advisory = s.storm?.advisories[Math.min(s.advisoryIdx, (s.storm?.advisories.length || 1) - 1)];
  const cone = step === 3 ? s.rehearsal?.scenario.cone || advisory?.cone || null : null;
  if (cone) legend.add("cone");
  if (step === 3 && advisory) legend.add("track");

  // ---- Step 4: fixes ----
  // Only high-priority fixes are pinned (plus whichever fix is selected); the rest live in the list.
  const fixMarks =
    step === 4
      ? gaps.filter((g) => {
          if (g.kind === "county") return false;
          const id = g.source === "brief" ? `b${g.idx}` : `p${g.idx - 1000}`;
          return g.severity === "HIGH" || (sel?.kind === "fix" && sel.id === id);
        })
      : [];
  if (fixMarks.length) legend.add("fix");
  const sites = step === 4 ? (s.allocation?.site_options || []).slice(0, 3) : [];
  if (sites.length) legend.add("site");
  const unassigned = step === 4 ? s.allocation?.unassigned || [] : [];
  if (unassigned.length) legend.add("unassigned");
  const tAt = new Map(tracts.map((t) => [t.geoId, t.at]));

  return (
    <div className="map-wrap">
      <GoogleMap
        mapId={CONFIG.mapId}
        colorScheme="DARK"
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

        {/* Step 1: need circles, ranked tracts numbered, nursing homes as squares */}
        {step === 1 &&
          tracts.map((t) => (
            <TractCircle key={t.geoId} t={t} ranked={ranked.has(t.geoId)} must={mustRanked.has(t.geoId)} selected={sel?.kind === "tract" && sel.id === t.geoId} hovered={s.hoverTract === t.geoId} onClick={() => pick("tract", t.geoId)} />
          ))}
        {step === 1 &&
          [...ranked.entries()].map(([g, rank]) => {
            const at = tAt.get(g) || coords(s.brief?.who_cannot_leave.find((x) => x.geo_id === g));
            return at ? (
              <AdvancedMarker key={`r${g}`} position={at} zIndex={50} title={`${tractName(g)} · rank ${rank}`} onClick={() => pick("tract", g)}>
                <div className={`m-rank ${sel?.id === g ? "is-selected" : ""}`}>{rank}</div>
              </AdvancedMarker>
            ) : null;
          })}
        {step === 1 &&
          (s.map?.care_facilities || [])
            .filter((cf) => cf.latitude != null && cf.longitude != null)
            .map((cf) => (
              <AdvancedMarker
                key={`cf:${cf.facility_id}`}
                position={{ lat: cf.latitude!, lng: cf.longitude! }}
                zIndex={20}
                title={`${cf.facility_name || "Nursing home"} · ${cf.certified_beds ?? "?"} certified beds (CMS)`}
              >
                <div className="m-nursing" aria-label="Nursing home">+</div>
              </AdvancedMarker>
            ))}

        {/* Step 3: cone and track under everything */}
        {cone && <Cone geometry={cone} />}
        {step === 3 && advisory && (
          <Polyline path={advisory.track.map((p) => ({ lat: p.lat, lng: p.lng }))} strokeColor={T.text} strokeWeight={2} strokeOpacity={0.8} clickable={false} />
        )}
        {step === 3 && s.storm?.landfall && (
          <AdvancedMarker position={s.storm.landfall} zIndex={5} title="Ian landfall, 28 Sep 2022 (NHC)">
            <div className="m-landfall">Ian landfall</div>
          </AdvancedMarker>
        )}

        {/* Steps 2–3: assignment lines, thin unless their tract is hovered or selected */}
        {showLines &&
          planLines.map((l) => {
            const tractId = l.key.split(":")[1];
            const hot = focusTract != null && tractId === focusTract;
            if (step === 3 && l.kind === "plan" && !hot) return null;
            const color = l.kind === "reassigned" ? T.warn : l.kind === "old" ? T.muted : T.accent;
            return (
              <Polyline
                key={l.key}
                path={[l.from, l.to]}
                clickable={false}
                strokeColor={hot ? T.text : color}
                strokeOpacity={l.kind === "old" ? 0 : hot ? 0.95 : l.kind === "reassigned" ? 0.85 : 0.22}
                strokeWeight={hot ? 3 : l.kind === "reassigned" ? 2.5 : 1}
                icons={l.kind === "old" ? [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 0.6, strokeColor: T.muted, scale: 2 }, offset: "0", repeat: "10px" }] : undefined}
                zIndex={hot ? 9 : 2}
              />
            );
          })}

        {marks.map(({ x, m }) => {
          if (step === 3 && m !== "offline") {
            return (
              <AdvancedMarker key={x.id} position={x.at} zIndex={10} title={x.name} onClick={() => pick("shelter", x.id)}>
                <div className="m-dot m-context" />
              </AdvancedMarker>
            );
          }
          const selected = sel?.kind === "shelter" && sel.id === x.id;
          const big = !["recYes", "recNo", "unrec"].includes(m);
          return (
            <AdvancedMarker key={x.id} position={x.at} zIndex={selected ? 100 : big ? 40 : 15} title={markTitle(x, m)} onClick={() => pick("shelter", x.id)}>
              <div className={`m-wrap ${selected ? "is-selected" : ""} ${s.verifying.includes(x.id) ? "is-verifying" : ""}`}>
                <div className={`m-${m}`}>{MARK_GLYPH[m] && <span>{MARK_GLYPH[m]}</span>}</div>
                {x.nameInactive && big && <div className="m-tag">INACTIVE</div>}
              </div>
            </AdvancedMarker>
          );
        })}

        {/* Step 4: fixes, unseated tracts, and where one site would seat the most */}
        {unassigned.map((u) => {
          const at = coords(u) || tAt.get(u.geo_id);
          return at ? (
            <Circle key={`u${u.geo_id}`} center={at} radius={Math.max(400, 40 * Math.sqrt(u.persons))} strokeColor={T.danger} strokeWeight={1.5} strokeOpacity={0.9} fillColor={T.danger} fillOpacity={0.12} clickable={false} zIndex={2} />
          ) : null;
        })}
        {sites.map((o, i) => {
          const at = coords(o.ref) || tAt.get(o.ref?.id || "");
          if (!at) return null;
          const hl = sel?.id === `site${i}` || (i === 0 && !sel);
          return (
            <Fragment key={`site${i}`}>
              {hl &&
                (o.tracts_covered || []).map((g) => {
                  const c = tAt.get(g);
                  return c ? <Circle key={`${i}:${g}`} center={c} radius={700} strokeColor={T.site} strokeWeight={1} strokeOpacity={0.8} fillColor={T.site} fillOpacity={0.12} clickable={false} zIndex={3} /> : null;
                })}
              <AdvancedMarker position={at} zIndex={90} title={o.subject} onClick={() => pick("fix", `site${i}`)}>
                <div className={`m-site ${hl ? "is-selected" : ""}`}>
                  ★ <span>{i === 0 ? "One site" : `#${i + 1}`}</span>
                </div>
              </AdvancedMarker>
            </Fragment>
          );
        })}
        {fixMarks.map((g) => {
          const id = g.source === "brief" ? `b${g.idx}` : `p${g.idx - 1000}`;
          const selected = sel?.kind === "fix" && sel.id === id;
          return (
            <AdvancedMarker key={g.key} position={g.at} zIndex={selected ? 100 : 60} title={g.item.subject} onClick={() => pick("fix", id)}>
              <div className={`m-fix sev-${g.severity.toLowerCase()} ${selected ? "is-selected" : ""}`}>{g.severity === "HIGH" ? "▲" : g.severity === "LOW" ? "●" : "◆"}</div>
            </AdvancedMarker>
          );
        })}
      </GoogleMap>
      <Legend keys={[...legend]} />
    </div>
  );
}

function markTitle(x: ShelterView, m: Mark): string {
  const what: Record<Mark, string> = {
    dropped: "Dropped after the live check",
    offline: "Offline in the Ian rehearsal",
    conflict: "Checked, still unconfirmed",
    renamed: "Confirmed, renamed",
    confirmed: "Confirmed on Google Maps",
    recYes: "Wheelchair access recorded yes",
    recNo: "Wheelchair access recorded no",
    unrec: "Wheelchair access not recorded",
  };
  return `${x.name}\n${what[m]}${x.nameInactive ? "\nFEMA's own name says INACTIVE" : ""}`;
}

function Focus() {
  const map = useMap();
  const { s } = useStore();
  useEffect(() => {
    if (!map || !s.focus) return;
    map.panTo(s.focus.at);
    if (s.focus.zoom) map.setZoom(s.focus.zoom);
  }, [map, s.focus?.n]);
  const [centeredFips, setCenteredFips] = useState<string | null>(null);
  useEffect(() => {
    if (!map || centeredFips === s.countyFips) return;
    const c = s.map?.county;
    if (c && c.lat != null && c.lng != null) {
      map.panTo({ lat: c.lat, lng: c.lng });
      setCenteredFips(s.countyFips);
    }
  }, [map, s.map, s.countyFips, centeredFips]);
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
          strokeColor={T.text}
          strokeOpacity={0.7}
          strokeWeight={1.5}
          fillColor={T.text}
          fillOpacity={0.08}
          clickable={false}
          zIndex={1}
        />
      ))}
    </>
  );
}

function TractCircle({ t, ranked, must, selected, hovered, onClick }: { t: TractView; ranked: boolean; must: boolean; selected: boolean; hovered: boolean; onClick: () => void }) {
  const nv = t.householdsNoVehicle || t.entry?.people?.households_no_vehicle || 0;
  const radius = Math.max(250, 55 * Math.sqrt(nv));
  const color = must && !ranked ? T.must : T.tract;
  return (
    <Circle
      center={t.at}
      radius={radius}
      onClick={onClick}
      strokeColor={selected || hovered ? T.text : color}
      strokeWeight={selected || hovered ? 2.5 : ranked || must ? 1.5 : 0.5}
      strokeOpacity={0.9}
      fillColor={color}
      fillOpacity={ranked || must ? 0.35 : Math.min(0.25, 0.05 + (t.share || 0) * 1.0)}
      zIndex={ranked ? 6 : 1}
    />
  );
}
