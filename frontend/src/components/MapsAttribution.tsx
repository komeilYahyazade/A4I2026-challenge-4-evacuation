import { Fragment, type ReactNode } from "react";
import type { MapsSource } from "../contract";

/**
 * Google Maps attribution. Rendered immediately after the content it supports.
 * "Google Maps" is never restyled, re-capitalized, wrapped or translated.
 */
export function MapsAttribution({ sources, lead = "Source", fallbackTitle }: { sources?: MapsSource[] | null; lead?: string; fallbackTitle?: string }) {
  const list = (sources || []).filter((s) => s && (s.title || s.uri));
  if (!list.length) return null;
  return (
    <div className="maps-attr" role="note">
      <span className="maps-attr-lead">
        {lead}: <span translate="no" className="gm-text">Google Maps</span>
      </span>
      <span className="maps-attr-list">
        {list.map((s, i) => (
          <span key={(s.place_id || s.uri || s.title || "") + i} className="maps-attr-item">
            {s.uri ? (
              <a href={s.uri} target="_blank" rel="noopener noreferrer" translate="no">
                {cleanTitle(s.title, fallbackTitle)}
              </a>
            ) : (
              <span translate="no">{cleanTitle(s.title, fallbackTitle)}</span>
            )}
          </span>
        ))}
      </span>
    </div>
  );
}

/** Address-only results arrive titled " - Google Maps"; keep the place part, else the shelter name. */
export function cleanTitle(title?: string | null, fallback?: string): string {
  const t = (title || "").replace(/\s*-\s*Google Maps\s*$/i, "").trim();
  if (!t || /^google maps place$/i.test(t)) return fallback || "Place";
  // Route chunks are titled with coordinates; never use coordinates as link text.
  if (/^\(?\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+/.test(t)) return fallback ? `Route to ${fallback}` : "Route";
  return t;
}

/** Wrap every literal "Google Maps" in agent or tool text so it is never translated or wrapped. */
export function protectGM(text: string): ReactNode {
  const parts = text.split(/(Google Maps)/g);
  if (parts.length === 1) return text;
  return parts.map((p, i) =>
    p === "Google Maps" ? (
      <span key={i} translate="no" className="gm-text">
        Google Maps
      </span>
    ) : (
      <Fragment key={i}>{p}</Fragment>
    ),
  );
}
