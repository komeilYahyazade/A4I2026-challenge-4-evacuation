import { useState, type ReactNode } from "react";
import { fmt, percentileRanks, rampColor, tractLabel } from "../format";
import type { AuditRow, Tract } from "../types";

// ---- tooltip -----------------------------------------------------------------------------------

type Tip = { x: number; y: number; body: ReactNode } | null;
function useTip() {
  const [tip, setTip] = useState<Tip>(null);
  const node = tip ? (
    <div className="tooltip" style={{ left: Math.min(tip.x + 14, window.innerWidth - 270), top: tip.y + 12 }}>{tip.body}</div>
  ) : null;
  return { node, show: (e: React.MouseEvent, body: ReactNode) => setTip({ x: e.clientX, y: e.clientY, body }), hide: () => setTip(null) };
}

// ---- three-state bar: yes / no / unrecorded ----------------------------------------------------------

export function TriBar({ label, yes, no, unrecorded, note }: { label: string; yes: number; no: number; unrecorded: number; note?: string }) {
  const total = yes + no + unrecorded || 1;
  const parts = [["yes", yes], ["no", no], ["unrecorded", unrecorded]] as const;
  return (
    <div className="tribar">
      <div className="lbl">
        {label}: <b>{fmt(yes + no)} I can speak to; {fmt(unrecorded)} nobody has assessed.</b>
      </div>
      <div className="bar" role="img" aria-label={`${label}: ${yes} yes, ${no} no, ${unrecorded} not recorded`}>
        {parts.map(([k, v]) => v > 0 && <div key={k} className={k} style={{ width: `${(100 * v) / total}%` }} title={`${k}: ${v}`} />)}
      </div>
      <div className="legend">
        ✓ YES {fmt(yes)} · – NO {fmt(no)} · ? NOT RECORDED {fmt(unrecorded)}{note ? ` · ${note}` : ""}
      </div>
    </div>
  );
}

// ---- KPI tile -------------------------------------------------------------------------------------

export function Tile({ value, label, ctx, tag }: { value: ReactNode; label: ReactNode; ctx?: ReactNode; tag?: ReactNode }) {
  return (
    <div className="tile">
      <div className="v">{value}</div>
      <div className="l">{label}</div>
      {tag && <div style={{ marginTop: 3 }}>{tag}</div>}
      {ctx && <div className="ctx">{ctx}</div>}
    </div>
  );
}

// ---- paired horizontal bars (county vs state), 0-100% --------------------------------------------

export function PairBars({ rows, a = "County", b = "State", max = 100, unit = "%" }: {
  rows: { label: string; a: number | null; b: number | null; note?: string }[]; a?: string; b?: string; max?: number; unit?: string;
}) {
  const tip = useTip();
  const W = 520, labelW = 190, barW = W - labelW - 60, rowH = 34;
  return (
    <div>
      <div className="legend-row">
        <span><i style={{ background: "var(--series-1)" }} />{a}</span>
        <span><i style={{ background: "var(--series-2)" }} />{b}</span>
      </div>
      <svg className="chart" viewBox={`0 0 ${W} ${rows.length * rowH + 4}`} role="img" aria-label={`${a} versus ${b}`}>
        {rows.map((r, i) => {
          const y = i * rowH + 4;
          const wa = r.a == null ? 0 : (barW * Math.min(r.a, max)) / max;
          const wb = r.b == null ? 0 : (barW * Math.min(r.b, max)) / max;
          return (
            <g key={r.label} onMouseMove={(e) => tip.show(e, <><b>{r.label}</b><br />{a}: {fmt(r.a, 1)}{unit} · {b}: {fmt(r.b, 1)}{unit}{r.note && <><br /><span className="muted">{r.note}</span></>}</>)} onMouseLeave={tip.hide}>
              <rect x={0} y={y - 2} width={W} height={rowH - 2} fill="transparent" />
              <text x={0} y={y + 10}>{r.label}</text>
              <rect x={labelW} y={y} width={Math.max(wa, 1)} height={10} rx={3} fill="var(--series-1)" />
              <text x={labelW + wa + 4} y={y + 9}>{fmt(r.a, 1)}{unit}</text>
              <rect x={labelW} y={y + 13} width={Math.max(wb, 1)} height={10} rx={3} fill="var(--series-2)" />
              <text x={labelW + wb + 4} y={y + 22}>{fmt(r.b, 1)}{unit}</text>
            </g>
          );
        })}
      </svg>
      {tip.node}
    </div>
  );
}

// ---- access-gap scatter: one dot per tract ---------------------------------------------------------

export function AccessScatter({ tracts, reachKm, onSelect, selected }: {
  tracts: Tract[]; reachKm: number; onSelect?: (geoId: string) => void; selected?: string | null;
}) {
  const tip = useTip();
  const pts = tracts.filter((t) => t.households_no_vehicle != null);
  const ranks = percentileRanks(pts.map((t) => t.need_score));
  const W = 640, H = 300, L = 46, B = 34, T = 10, R = 10;
  const xMax = Math.max(reachKm * 2, ...pts.map((t) => Math.min(t.nearest_wheelchair_yes_km ?? 40, 40)));
  const yMax = Math.max(10, ...pts.map((t) => t.households_no_vehicle ?? 0));
  const sx = (v: number) => L + ((W - L - R) * Math.min(v, xMax)) / xMax;
  const sy = (v: number) => H - B - ((H - B - T) * v) / yMax;
  const xticks = [0, reachKm, ...[10, 20, 30, 40].filter((v) => v < xMax && v !== reachKm)];
  const yticks = [0, Math.round(yMax / 2), Math.round(yMax)];
  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Tracts by distance to a shelter recorded as wheelchair accessible and households without a car">
        <g className="grid">{yticks.map((v) => <line key={v} x1={L} x2={W - R} y1={sy(v)} y2={sy(v)} />)}</g>
        <rect x={sx(reachKm)} y={T} width={W - R - sx(reachKm)} height={H - B - T} fill="var(--critical)" opacity={0.05} />
        <line x1={sx(reachKm)} x2={sx(reachKm)} y1={T} y2={H - B} stroke="var(--critical)" strokeDasharray="4 3" />
        <text x={sx(reachKm) + 4} y={T + 12} style={{ fill: "var(--critical)" }}>beyond {reachKm} km: no recorded-accessible shelter in reach</text>
        {pts.map((t, i) => {
          const x = sx(t.nearest_wheelchair_yes_km ?? xMax), y = sy(t.households_no_vehicle ?? 0);
          const sel = t.geo_id === selected;
          return (
            <circle key={t.geo_id} cx={x} cy={y} r={sel ? 7 : 4.5} fill={rampColor(ranks[i])} stroke={sel ? "var(--text)" : "var(--surface)"} strokeWidth={sel ? 2 : 1.5}
              style={{ cursor: onSelect ? "pointer" : undefined }}
              onClick={() => onSelect?.(t.geo_id)}
              onMouseMove={(e) => tip.show(e, <><b>{tractLabel(t.geo_id)}</b> <span className="muted">{t.geo_id}</span><br />
                {fmt(t.households_no_vehicle)} households without a car<br />
                nearest recorded-accessible shelter {t.nearest_wheelchair_yes_km == null ? "none in state record" : `${fmt(t.nearest_wheelchair_yes_km, 1)} km`}<br />
                nearest shelter of any kind {fmt(t.nearest_shelter_km, 1)} km · need {fmt(t.need_score, 2)}<br />
                <span className="muted">straight line from the tract centroid</span></>)}
              onMouseLeave={tip.hide} />
          );
        })}
        <g className="axis">
          <line x1={L} x2={W - R} y1={H - B} y2={H - B} />
          {xticks.map((v) => <text key={v} x={sx(v)} y={H - B + 14} textAnchor="middle">{v}</text>)}
          <text x={(L + W) / 2} y={H - 4} textAnchor="middle">km to nearest shelter recorded as wheelchair accessible (straight line)</text>
          {yticks.map((v) => <text key={v} x={L - 6} y={sy(v) + 4} textAnchor="end">{fmt(v)}</text>)}
          <text transform={`translate(11 ${(H - B) / 2}) rotate(-90)`} textAnchor="middle">households without a car</text>
        </g>
      </svg>
      <div className="legend-row">
        <span><i style={{ background: "#b7d3f6" }} />lower need</span>
        <span><i style={{ background: "#104281" }} />higher need score</span>
        <span className="muted">Dot colour is need-score percentile within the county. Hover for detail; click to select.</span>
      </div>
      {tip.node}
    </div>
  );
}

// ---- equity audit: grouped bars ---------------------------------------------------------------------

const AUDIT_LABEL: Record<string, string> = {
  pct_households_no_vehicle: "Households without a car", pct_age_65_plus: "Aged 65+", pct_disability: "With a disability",
  poverty_rate: "Below poverty line", median_income: "Median income", pct_limited_english: "Limited English",
  shelters_access_unrecorded: "Nearby shelters, access unrecorded", pct_black: "Black", pct_hispanic: "Hispanic",
  pct_white_non_hispanic: "White", pct_asian: "Asian",
};

export function AuditBars({ rows }: { rows: AuditRow[] }) {
  const tip = useTip();
  const pctRows = rows.filter((r) => r.unit === "%");
  const W = 560, labelW = 200, barW = W - labelW - 50, rowH = 42;
  const series = [["recommended", "var(--series-1)", "Brief's tracts"], ["county", "var(--series-2)", "County"], ["state", "var(--series-3)", "State"]] as const;
  return (
    <div>
      <div className="legend-row">
        {series.map(([, c, l]) => <span key={l}><i style={{ background: c }} />{l}</span>)}
        <span className="muted">▲ fed the ranking · others are audit only</span>
      </div>
      <svg className="chart" viewBox={`0 0 ${W} ${pctRows.length * rowH + 4}`} role="img" aria-label="Equity audit">
        {pctRows.map((r, i) => {
          const y = i * rowH + 2;
          return (
            <g key={r.metric} onMouseMove={(e) => tip.show(e, <><b>{AUDIT_LABEL[r.metric] ?? r.metric}</b> ({r.used_as_input ? "fed the ranking" : "audit only"})<br />
              brief {fmt(r.recommended, 1)}% · county {fmt(r.county, 1)}% · state {fmt(r.state, 1)}%</>)} onMouseLeave={tip.hide}>
              <rect x={0} y={y} width={W} height={rowH - 4} fill="transparent" />
              <text x={0} y={y + 16}>{r.used_as_input ? "▲ " : ""}{AUDIT_LABEL[r.metric] ?? r.metric}</text>
              {series.map(([k, c], j) => {
                const v = r[k] as number | null;
                const w = v == null ? 0 : (barW * Math.min(v, 100)) / 100;
                return (
                  <g key={k}>
                    <rect x={labelW} y={y + j * 12} width={Math.max(w, 1)} height={9} rx={3} fill={c} />
                    <text x={labelW + w + 4} y={y + j * 12 + 8}>{fmt(v, 1)}</text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      {tip.node}
    </div>
  );
}

// ---- horizontal ranked bars (county comparison) -------------------------------------------------------

export function RankBars({ rows, max, unit = "", onClick, selected }: {
  rows: { id: string; label: string; value: number | null; note?: string }[]; max?: number; unit?: string;
  onClick?: (id: string) => void; selected?: string;
}) {
  const tip = useTip();
  const m = max ?? Math.max(1e-9, ...rows.map((r) => r.value ?? 0));
  const W = 520, labelW = 150, barW = W - labelW - 50, rowH = 18;
  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${W} ${rows.length * rowH}`} role="img" aria-label="Ranked bars">
        {rows.map((r, i) => {
          const w = r.value == null ? 0 : (barW * r.value) / m;
          const sel = r.id === selected;
          return (
            <g key={r.id} style={{ cursor: onClick ? "pointer" : undefined }} onClick={() => onClick?.(r.id)}
              onMouseMove={(e) => tip.show(e, <><b>{r.label}</b><br />{fmt(r.value, 3)}{unit}{r.note && <><br />{r.note}</>}</>)} onMouseLeave={tip.hide}>
              <rect x={0} y={i * rowH} width={W} height={rowH} fill={sel ? "var(--surface-2)" : "transparent"} />
              <text x={0} y={i * rowH + 13} style={sel ? { fontWeight: 700, fill: "var(--text)" } : undefined}>{r.label}</text>
              <rect x={labelW} y={i * rowH + 4} width={Math.max(w, 1)} height={10} rx={3} fill={sel ? "var(--series-2)" : "var(--series-1)"} />
              <text x={labelW + w + 4} y={i * rowH + 13}>{fmt(r.value, 2)}</text>
            </g>
          );
        })}
      </svg>
      {tip.node}
    </div>
  );
}
