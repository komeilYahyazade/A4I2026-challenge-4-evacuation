import { useState } from "react";
import { useStore, type LayerKey } from "../store";
import { ACCESS, GAP_RED, MODE_COLOR, TRACT_MUST, TRACT_TOP } from "../theme";

const LAYERS: [LayerKey, string][] = [
  ["tracts", "Tracts (need)"],
  ["shelters", "Shelters"],
  ["lines", "Assignments"],
  ["gaps", "Gaps to fix"],
  ["storm", "Storm (Ian replay)"],
];

export function Legend() {
  const { s, d } = useStore();
  const [open, setOpen] = useState(false);
  return (
    <div className={`legend ${open ? "" : "closed"}`}>
      <button className="legend-toggle" onClick={() => setOpen(!open)}>
        {open ? "Hide legend" : "Legend and layers"}
      </button>
      {!open && (
        <div className="mini-key">
          {Object.entries(ACCESS).map(([k, v]) => (
            <span key={k} title={v.text}>
              <span className="swatch" style={{ background: v.color }}>
                {v.glyph}
              </span>
              {k === "NOBODY_KNOWS" ? "UNRECORDED" : k.replace("_", " ").toLowerCase()}
            </span>
          ))}
        </div>
      )}
      {open && (
        <>
          <div className="legend-sec">
            <div className="legend-title">Pin fill: wheelchair evidence</div>
            {Object.entries(ACCESS).map(([k, v]) => (
              <div key={k} className="legend-row">
                <span className="swatch" style={{ background: v.color }}>
                  {v.glyph}
                </span>
                {v.text}
              </div>
            ))}
          </div>
          <div className="legend-sec">
            <div className="legend-title">Pin ring: building checked live?</div>
            <div className="legend-row"><span className="ring ring-live" /> Confirmed on Google Maps</div>
            <div className="legend-row"><span className="ring ring-renamed" /> Confirmed, renamed</div>
            <div className="legend-row"><span className="ring ring-conflict" /> Live problem</div>
            <div className="legend-row"><span className="ring ring-dropped" /> Dropped (not found / closed)</div>
            <div className="legend-row"><span className="ring ring-fema" /> FEMA record only</div>
            <div className="legend-row"><span className="ring ring-post" /> Square: post-impact only</div>
          </div>
          <div className="legend-sec">
            <div className="legend-title">Lines and areas</div>
            <div className="legend-row"><span className="line" style={{ background: MODE_COLOR.PARATRANSIT }} /> Para-transit assignment</div>
            <div className="legend-row"><span className="line" style={{ background: MODE_COLOR.WALK }} /> Walkable (≤ 1.5 km)</div>
            <div className="legend-row"><span className="line" style={{ background: "#ff6f00" }} /> Reassigned in rehearsal</div>
            <div className="legend-row"><span className="dot" style={{ background: TRACT_TOP }} /> Can't drive: top tracts by car-less households</div>
            <div className="legend-row"><span className="dot" style={{ background: TRACT_MUST }} /> Must leave, may need help</div>
            <div className="legend-row"><span className="dot" style={{ background: "#ffb300" }} /> ★ One designated site would seat these</div>
            <div className="legend-row"><span className="dot" style={{ background: GAP_RED }} /> Gap to fix before the season</div>
          </div>
          <div className="legend-sec">
            <div className="legend-title">Layers</div>
            {LAYERS.map(([k, label]) => (
              <label key={k} className="legend-row">
                <input type="checkbox" checked={s.layers[k]} onChange={(e) => d({ type: "layer", key: k, on: e.target.checked })} /> {label}
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
