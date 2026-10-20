import type { Segment, Source } from "../types";
import { hhmm } from "../format";

/** The provider name, rendered exactly as required (BUILD_SPEC.md section 11). */
export const GoogleMaps = () => (
  <span translate="no" style={{ whiteSpace: "nowrap" }}>Google Maps</span>
);

/** Each segment, then its Google Maps sources on the next line in the same card. */
export function Segments({ segments, sources, checkedAt }: { segments: Segment[]; sources: Source[]; checkedAt?: string | null }) {
  const byId = new Map(sources.map((s) => [s.id, s]));
  return (
    <>
      {segments.map((seg, i) => {
        const srcs = seg.source_ids.map((id) => byId.get(id)).filter((s): s is Source => !!s);
        return (
          <div className="seg" key={i}>
            {seg.text}
            <span className={`fresh ${seg.freshness}`}>
              {seg.freshness === "live" ? `live${checkedAt ? " · " + hhmm(checkedAt) : ""}`
                : seg.freshness === "field" ? "field assessment" : "historical"}
            </span>
            {srcs.length > 0 && (
              <div className="src">
                Source: <GoogleMaps /> —{" "}
                {srcs.map((s, j) => (
                  <span key={s.id}>
                    {j > 0 && " · "}
                    <a href={s.uri ?? undefined} target="_blank" rel="noopener noreferrer">{s.title || "place"}</a>
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

export function FlagChip({ label, value }: { label: string; value: "yes" | "no" | "unrecorded" }) {
  const icon = value === "yes" ? "✓" : value === "no" ? "–" : "?";
  return (
    <span className={`chip ${value}`}>
      {icon} {label}: <b>{value === "unrecorded" ? "Not recorded" : value}</b>
    </span>
  );
}

export const Tag = ({ kind }: { kind: "recorded" | "modeled" | "live" | "county" }) => (
  <span className={`tag ${kind}`}>{kind === "county" ? "COUNTY-LEVEL" : kind.toUpperCase()}</span>
);
