"""Shared rendering: attributed segments (BUILD_SPEC.md section 11), flag chips, badges.

Each segment is followed, on the next line in the same card, by its Google Maps sources. The
provider name is rendered exactly as <span translate="no" style="white-space: nowrap">Google Maps</span>.
"""

from __future__ import annotations

import html
from datetime import datetime

GOOGLE_MAPS = '<span translate="no" style="white-space: nowrap">Google Maps</span>'

# Status palette (fixed): every state also carries an icon and a label, never colour alone.
STATUS = {
    "good": ("#0ca30c", "✓"),
    "warning": ("#fab219", "!"),
    "critical": ("#d03b3b", "✕"),
    "neutral": ("#9a9890", "–"),
}

CSS = """
<style>
.er-card { border: 1px solid rgba(128,128,128,.35); border-radius: 8px; padding: 10px 12px; margin: 0 0 10px 0; }
.er-muted { opacity: .75; font-size: .85em; }
.er-seg { margin: 4px 0 8px 0; }
.er-src { font-size: .85em; opacity: .85; margin-top: 2px; }
.er-src a { text-decoration: underline; }
.er-fresh { font-size: .72em; border: 1px solid rgba(128,128,128,.5); border-radius: 8px; padding: 0 6px; margin-left: 4px; white-space: nowrap; }
.er-chip { display: inline-block; font-size: .8em; border: 1.5px solid; border-radius: 10px; padding: 0 8px; margin: 2px 4px 2px 0; white-space: nowrap; }
.er-badge { display: inline-block; font-weight: 600; font-size: .85em; border-radius: 6px; padding: 1px 8px; color: #111; white-space: nowrap; }
.er-phase { display: inline-block; font-size: .78em; padding: 2px 8px; border-radius: 10px; margin: 2px 2px; border: 1px solid rgba(128,128,128,.45); opacity: .55; }
.er-phase.on { opacity: 1; font-weight: 600; border-width: 2px; }
.er-refused { border-left: 4px solid #d03b3b; padding-left: 10px; }
.er-legend span { margin-right: 12px; white-space: nowrap; font-size: .85em; }
.er-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; vertical-align: middle; }
</style>
"""


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def hhmm(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M UTC")
    except ValueError:
        return iso


def segments_html(segments: list[dict], sources: list[dict], checked_at: str | None = None) -> str:
    by_id = {s["id"]: s for s in sources or []}
    out = []
    for seg in segments or []:
        label = "live" + (f" · {hhmm(checked_at)}" if checked_at else "") if seg["freshness"] == "live" else "historical"
        out.append(f'<div class="er-seg">{esc(seg["text"])}<span class="er-fresh">{label}</span>')
        srcs = [by_id[i] for i in seg.get("source_ids", []) if i in by_id]
        if srcs:
            links = " · ".join(
                f'<a href="{esc(s.get("uri"))}" target="_blank" rel="noopener">{esc(s.get("title") or "place")}</a>'
                for s in srcs)
            out.append(f'<div class="er-src">Source: {GOOGLE_MAPS} — {links}</div>')
        out.append("</div>")
    return "".join(out)


def flag_chip(label: str, value: str) -> str:
    kind, text = {"yes": ("good", "yes"), "no": ("warning", "no"),
                  "unrecorded": ("neutral", "Not recorded")}.get(value, ("neutral", "Not recorded"))
    color, icon = STATUS[kind]
    return f'<span class="er-chip" style="border-color:{color}">{icon} {esc(label)}: {text}</span>'


def badge(kind: str, label: str) -> str:
    color, icon = STATUS[kind]
    return f'<span class="er-badge" style="background:{color}">{icon} {esc(label)}</span>'


VERDICT = {"holds_up": ("good", "Holds up"), "verify_before_season": ("warning", "Verify before season"),
           "do_not_rely": ("critical", "Do not rely")}
VSTATUS = {"confirmed": ("good", "Confirmed on Google Maps"), "renamed": ("warning", "Renamed"),
           "closed": ("critical", "Permanently closed"), "not_found": ("critical", "Not found"),
           "unclear": ("neutral", "No definite answer"), "error": ("neutral", "Check failed")}
