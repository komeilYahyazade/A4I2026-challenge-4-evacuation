import type { EvidenceLabel, ThreeState } from "../contract";
import { BASIS_TEXT, basisCode } from "../derive";

const LABEL_CLASS: Record<string, string> = {
  RECORDED: "lb-recorded",
  UNRECORDED: "lb-unrecorded",
  "VERIFIED LIVE": "lb-live",
  MODELED: "lb-modeled",
  "COUNTY-LEVEL": "lb-county",
  APPROXIMATE: "lb-approx",
};

export function Label({ label, title }: { label?: EvidenceLabel | string | null; title?: string }) {
  if (!label) return null;
  return (
    <span className={`label ${LABEL_CLASS[label] || ""}`} title={title}>
      {label}
    </span>
  );
}

/** Three-state chip. `bad` says which recorded answer is the problem (YES for surge, NO for wheelchair). */
export function Tri({ name, value, bad = "NO" }: { name: string; value?: ThreeState | string | null; bad?: "YES" | "NO" }) {
  const v = (value || "UNRECORDED") as ThreeState;
  const tone = v === "UNRECORDED" ? "unrecorded" : v === bad ? "bad" : "good";
  return (
    <span className={`tri tri-${tone}`} title={v === "UNRECORDED" ? "Nobody recorded it. Not the same as NO." : `Recorded ${v}`}>
      {name}: <b>{v}</b>
    </span>
  );
}

export function Basis({ basis, km, minutes }: { basis?: string | null; km?: number | null; minutes?: number | null }) {
  const code = basisCode(basis);
  const value =
    code !== "STRAIGHT_LINE" && minutes != null
      ? `${Math.round(minutes)} min drive`
      : km != null
        ? `${km.toFixed(1)} km straight-line`
        : "distance unknown";
  return (
    <span className={`basis basis-${code.toLowerCase()}`} title={BASIS_TEXT[code]}>
      {value} <small>({code === "ROUTED" ? "ROUTED" : code === "MODEL_REPORTED" ? "MODEL-REPORTED" : "STRAIGHT-LINE"})</small>
    </span>
  );
}

export function SourceBadge({ source, note }: { source?: "live" | "mock"; note?: string }) {
  if (!source) return null;
  return source === "mock" ? (
    <span className="mock-badge" title={note || "Synthetic fixtures, not live data"}>
      MOCK DATA
    </span>
  ) : (
    <span className="live-badge" title="From the deployed agent">
      LIVE
    </span>
  );
}
