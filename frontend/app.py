"""readiness-ui: a season console for the county emergency manager (BUILD_SPEC.md section 12).

streamlit run frontend/app.py   (API_BASE_URL points at readiness-api)
"""

from __future__ import annotations

import copy
import json
import math

import pandas as pd
import pydeck as pdk
import streamlit as st

import api_client as api
from attribution import (CSS, GOOGLE_MAPS, STATUS, VERDICT, VSTATUS, badge, esc, flag_chip, hhmm,
                         segments_html)

SCENARIO = ("Our agent helps the Lee County (Florida) emergency manager decide which shelters can really "
            "take the people who cannot drive themselves out, and what to fix, before the September peak "
            "of hurricane season.")
PHASES = ["Inventory", "Plan", "Brief", "Watch", "Activate", "Learn"]
TRACKS = {"shelter_readiness": "Shelter readiness", "getting_people_out": "Getting people out",
          "medically_dependent": "Medically dependent"}
# Sequential blue ramp (need score), light -> dark.
RAMP = ["#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf",
        "#1c5cab", "#184f95", "#104281"]
CARE_COLOR = "#6250d6"
EOC = "For anything happening now, contact the county emergency operations center or call 911."

st.set_page_config(page_title="Evacuation Readiness", page_icon="🌀", layout="wide")
st.html(CSS)

ss = st.session_state
for k, v in {"county_fips": "12071", "brief_id": None, "brief": None, "brief_status": None,
             "chat_session": None, "chat": [], "highlight": {"tracts": [], "shelters": [], "care_facilities": []},
             "selected_shelter": None, "verifications": {}, "audit": None}.items():
    ss.setdefault(k, v)
# ?brief_id=... reopens a brief the API still holds in memory (jobs expire after two hours).
if ss.brief_id is None and st.query_params.get("brief_id"):
    ss.brief_id = st.query_params["brief_id"]
    ss.county_fips = st.query_params.get("county", ss.county_fips)


def rgba(hex_color: str, alpha: int = 255) -> list[int]:
    h = hex_color.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)] + [alpha]


def show_error(e: api.ApiError, what: str) -> None:
    st.error(f"{what}: {e.message}" + (" Try again shortly." if e.retryable else ""))


@st.cache_data(ttl=60, show_spinner=False)
def load_health():
    return api.health()


@st.cache_data(ttl=600, show_spinner=False)
def load_counties():
    return api.counties()


@st.cache_data(ttl=300, show_spinner=False)
def load_map(fips: str):
    return api.county_map(fips)


# ---- header -----------------------------------------------------------------------------------

try:
    health = load_health()
except api.ApiError as e:
    st.title("Evacuation Readiness Agent")
    show_error(e, "The readiness API is not reachable")
    st.stop()

mode_kind = "good" if health["mode"] == "live" else "warning"
st.html(f'<h2 style="margin-bottom:0">Evacuation Readiness Agent</h2>'
        f'<div class="er-muted">{esc(SCENARIO)}</div>'
        f'<div style="margin-top:6px">{badge(mode_kind, health["mode"].title())} '
        f'<span class="er-muted">model {esc(health["model"])} · state {esc(health["state"])} · '
        f'planning tool, not for emergency response</span></div>')

counties = load_counties()["counties"]
by_fips = {c["county_fips"]: c for c in counties}
left, centre, right = st.columns([0.95, 2.1, 1.75], gap="medium")

# ---- left: controls ---------------------------------------------------------------------------

with left:
    st.html('<div>' + "".join(f'<span class="er-phase{" on" if p == "Brief" else ""}">{p}</span>'
                              for p in PHASES) + '</div>')
    order = sorted(counties, key=lambda c: c["rank"])
    fips_list = [c["county_fips"] for c in order]
    fips = st.selectbox("County", fips_list, index=fips_list.index(ss.county_fips) if ss.county_fips in fips_list else 0,
                        format_func=lambda f: f"#{by_fips[f]['rank']} {by_fips[f]['name']}",
                        help="Ranked by need × hurricane/coastal-flood exposure (mean of tract scores). "
                             "Never ranked on shelter paperwork.")
    if fips != ss.county_fips:
        ss.county_fips, ss.brief_id, ss.brief, ss.brief_status, ss.audit = fips, None, None, None, None
        ss.highlight = {"tracts": [], "shelters": [], "care_facilities": []}
        ss.selected_shelter = None
    track = st.selectbox("Track", list(TRACKS), format_func=TRACKS.get)
    reach = st.slider("Reachable without a car (km, straight line)", 1.0, 10.0, 3.0, 0.5,
                      help="Default 3 km: roughly a 40-minute walk for an older adult.")
    max_verify = st.select_slider("Shelters to check on Google Maps", [6, 12, 20], value=12)
    routes = st.checkbox("Ask Google Maps routing for drive times", value=True)
    if st.button("Generate brief", type="primary", width="stretch"):
        try:
            r = api.create_brief(fips, track, reach, max_verify, routes)
            ss.brief_id, ss.brief, ss.brief_status, ss.audit = r["brief_id"], None, None, None
            st.query_params.update({"brief_id": r["brief_id"], "county": fips})
        except api.ApiError as e:
            show_error(e, "Could not start the brief")
    c = by_fips[fips]
    wc = c["shelters"]["wheelchair"]
    st.html(f'<div class="er-card"><b>{esc(c["name"])}</b> <span class="er-fresh">historical</span><br>'
            f'{c["shelters"]["total"]} shelters on file · {c["shelters"]["medical_needs"]} medical-needs<br>'
            f'Wheelchair access: {wc["yes"]} yes · {wc["no"]} no · <b>{wc["unrecorded"]} not recorded</b><br>'
            f'<span class="er-muted">Not recorded is not no: nobody filled the field in.</span></div>')

# ---- brief data used by map and tabs ----------------------------------------------------------

brief = ss.brief
dest_by_id = {d["shelter"]["shelter_id"]: d for d in (brief or {}).get("destinations", [])}


def evidence(s: dict) -> tuple[str, str]:
    sid = s["shelter_id"]
    d = dest_by_id.get(sid)
    v = ss.verifications.get(sid) or (d or {}).get("verification")
    if d:
        return VERDICT[d["verdict"]]
    if v and v["status"] in ("closed", "not_found", "renamed", "confirmed"):
        return VSTATUS[v["status"]]
    if s["in_surge_slosh_area"] == "yes":
        return "critical", "In surge zone (record)"
    if s["wheelchair_accessible"] == "yes":
        return "good", "Wheelchair: yes (record)"
    if s["wheelchair_accessible"] == "no":
        return "warning", "Wheelchair: no (record)"
    return "neutral", "Not recorded"


# ---- centre: map ------------------------------------------------------------------------------

with centre:
    try:
        m = load_map(fips)
    except api.ApiError as e:
        show_error(e, "Could not load the map")
        st.stop()
    hl = ss.highlight
    tracts = pd.DataFrame(m["tracts"])
    layers = []
    if not tracts.empty:
        ranks = tracts["need_score"].rank(pct=True).fillna(0)
        tracts["color"] = [rgba(RAMP[min(len(RAMP) - 1, int(r * len(RAMP)))], 190) for r in ranks]
        tracts["radius"] = [200 + 40 * math.sqrt(max(h or 0, 0)) for h in tracts["households_no_vehicle"]]
        tracts["tooltip"] = [
            f"<b>Tract {esc(g)}</b><br>{esc(round(h or 0))} households own no car (ACS, car ownership)<br>"
            f"need score {esc(n)}<br>nearest shelter {esc(a)} km · nearest recorded wheelchair-yes {esc(w)} km "
            f"(straight line)"
            for g, h, n, a, w in zip(tracts.geo_id, tracts.households_no_vehicle, tracts.need_score,
                                     tracts.nearest_shelter_km, tracts.nearest_wheelchair_yes_km)]
        layers.append(pdk.Layer("ScatterplotLayer", data=tracts, id="tracts", get_position=["lon", "lat"],
                                get_radius="radius", get_fill_color="color", pickable=True,
                                radius_min_pixels=2))
    shelters = pd.DataFrame(m["shelters"])
    if not shelters.empty:
        states = [evidence(s) for s in m["shelters"]]
        shelters["color"] = [rgba(STATUS[k][0]) for k, _ in states]
        shelters["state"] = [label for _, label in states]
        shelters["tooltip"] = [
            f"<b>{esc(n)}</b><br>{esc(STATUS[k][1])} {esc(lbl)}<br>shelter {esc(i)} · {esc(u or 'usage n/a')}"
            for n, (k, lbl), i, u in zip(shelters.name, states, shelters.shelter_id, shelters.usage)]
        lines = []
        for sid, d in dest_by_id.items():
            for g in d["serves"]:
                t = tracts[tracts.geo_id == g]
                if not t.empty:
                    lines.append({"from": [float(t.lon.iloc[0]), float(t.lat.iloc[0])],
                                  "to": [d["shelter"]["lon"], d["shelter"]["lat"]], "tooltip": f"Tract {g} → {sid}"})
        if lines:
            layers.append(pdk.Layer("LineLayer", data=lines, id="serves", get_source_position="from",
                                    get_target_position="to", get_color=[70, 70, 70, 170], get_width=2))
        # Brief destinations draw last and larger, so a Maps-driven drop is visible at a glance.
        shelters["in_brief"] = shelters.shelter_id.isin(dest_by_id)
        for lid, part, radius, px in (("shelters", shelters[~shelters.in_brief], 160, 5),
                                      ("shelters_brief", shelters[shelters.in_brief], 300, 8)):
            if not part.empty:
                layers.append(pdk.Layer("ScatterplotLayer", data=part, id=lid, get_position=["lon", "lat"],
                                        get_radius=radius, radius_min_pixels=px, get_fill_color="color",
                                        stroked=True, get_line_color=[255, 255, 255, 230],
                                        line_width_min_pixels=1.5, pickable=True))
    care = pd.DataFrame(m["care_facilities"])
    if not care.empty:
        care["radius"] = [150 + 3 * (b or 0) for b in care["certified_beds"]]
        care["tooltip"] = [f"<b>{esc(n.title())}</b><br>nursing home · {esc(b)} certified beds (CMS)<br>"
                           f"nearest recorded generator-yes shelter {esc(k)} km (straight line)"
                           for n, b, k in zip(care.name, care.certified_beds, care.nearest_generator_yes_km)]
        layers.append(pdk.Layer("ScatterplotLayer", data=care, id="care", get_position=["lon", "lat"],
                                get_radius="radius", filled=False, stroked=True, get_line_color=rgba(CARE_COLOR),
                                line_width_min_pixels=2.5, pickable=True))
    rings = []
    for g in hl["tracts"]:
        rings += [{"lon": r.lon, "lat": r.lat, "tooltip": f"Tract {g}"} for r in tracts[tracts.geo_id == g].itertuples()]
    for s in m["shelters"]:
        if s["shelter_id"] in hl["shelters"] or s["shelter_id"] == ss.selected_shelter:
            rings.append({"lon": s["lon"], "lat": s["lat"], "tooltip": s["name"]})
    for cf in m["care_facilities"]:
        if cf["ccn"] in hl["care_facilities"]:
            rings.append({"lon": cf["lon"], "lat": cf["lat"], "tooltip": cf["name"]})
    if rings:
        layers.append(pdk.Layer("ScatterplotLayer", data=rings, id="highlight", get_position=["lon", "lat"],
                                get_radius=550, filled=False, stroked=True, get_line_color=[20, 20, 20, 255],
                                line_width_min_pixels=3, radius_min_pixels=12))
    center = m["center"] or {"lat": 27.8, "lon": -81.7}
    deck = pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(latitude=center["lat"], longitude=center["lon"],
                                                                    zoom=9.6), tooltip={"html": "{tooltip}"},
                    map_style="light")
    event = st.pydeck_chart(deck, on_select="rerun", selection_mode="single-object", key=f"map_{fips}",
                            height=560)
    picked = (getattr(event, "selection", None) or {}).get("objects", {}) if event else {}
    for lid in ("shelters", "shelters_brief"):
        if picked.get(lid):
            ss.selected_shelter = picked[lid][0]["shelter_id"]
    legend = "".join(f'<span><i class="er-dot" style="background:{STATUS[k][0]}"></i>{STATUS[k][1]} {lbl}</span>'
                     for k, lbl in [("good", "Holds up / verified"), ("warning", "Verify before season / renamed"),
                                    ("critical", "Do not rely / closed / surge zone"), ("neutral", "Not recorded")])
    st.html(f'<div class="er-legend">{legend}<span><i class="er-dot" style="border:2px solid {CARE_COLOR}"></i>'
            f'Nursing home (size = beds)</span><span><i class="er-dot" style="background:{RAMP[7]}"></i>Tract '
            f'(size = households with no car; darker = higher need)</span></div>'
            f'<div class="er-muted">Map shows only our own coordinates (FEMA, ACS/NRI, CMS). Tracts are centroids. '
            f'Power dependence: {m["power_dependence"]["power_dependent_dme"] if m["power_dependence"] else "n/a"} '
            f'Medicare beneficiaries county-wide (HHS emPOWER) — how many, not where.</div>')

# ---- right: tabs -------------------------------------------------------------------------------


def set_highlight(affected: dict) -> None:
    ss.highlight = {"tracts": affected.get("tracts", []), "shelters": affected.get("shelters", []),
                    "care_facilities": affected.get("care_facilities", [])}


def export_brief(b: dict) -> str:
    """FEMA-derived content and place_id only; never Maps text (BUILD_SPEC.md section 11)."""
    out = copy.deepcopy(b)
    out["destinations"] = [{"shelter": d["shelter"], "serves": d["serves"], "straight_line_km": d["straight_line_km"],
                            "verdict": d["verdict"]} for d in b["destinations"]]
    out["fixes"] = [{**f, "title": f["title"] if not f["source_ids"] else None,
                     "recommendation": f["recommendation"] if not f["source_ids"] else None}
                    for f in b["fixes"]]
    out["sources"] = [{"id": s["id"], "place_id": s["place_id"]} for s in b["sources"] if s.get("place_id")]
    return json.dumps(out, indent=1)


def render_brief(b: dict) -> None:
    st.html(f'<div class="er-card"><b>{esc(b["headline"])}</b><div class="er-muted">{esc(b["county"]["name"])} · '
            f'generated {hhmm(b["generated_at"])} · {esc(b["model"])}</div></div>')
    with st.expander("How we measured"):
        for k, v in b["assumptions"].items():
            st.markdown(f"- **{k.replace('_', ' ')}**: {v}")
    st.markdown("#### 1. Who cannot leave")
    for w in b["who_cannot_leave"]:
        drivers = " · ".join(f"{d['metric'].replace('_', ' ')} {d['value']:g} {d['unit']}" for d in w["drivers"])
        st.html(f'<div class="er-card"><b>{w["rank"]}. {esc(w["label"])}</b> '
                f'<span class="er-muted">{"tract" if w["kind"] == "tract" else "nursing home"} {esc(w["id"])} · '
                f'{esc(w["people"])} {"people" if w["kind"] == "tract" else "beds"}</span>'
                f'<div class="er-seg">{esc(w["reason"])}<span class="er-fresh">historical</span></div>'
                f'<div class="er-muted">{esc(drivers)}</div></div>')
    if b.get("power_dependence"):
        p = b["power_dependence"]
        st.html(f'<div class="er-card">Power-dependent Medicare beneficiaries: <b>{p["power_dependent_dme"]}</b>'
                f'{" (1 to 10, censored)" if p["is_censored"] else ""} <span class="er-fresh">historical</span>'
                f'<div class="er-muted">{esc(p["caveat"])}</div></div>')
    st.markdown("#### 2. Where they would go, and whether it holds up")
    for d in b["destinations"]:
        s, v = d["shelter"], d["verification"]
        kind, label = VERDICT[d["verdict"]]
        vk, vl = VSTATUS[v["status"]] if v else ("neutral", "Not checked")
        drive = (v or {}).get("drive") or {}
        dist = f'{d["straight_line_km"]} km straight line' if d["straight_line_km"] is not None else ""
        if drive.get("drive_minutes") is not None:
            dist += f' · {round(drive["drive_minutes"])} min drive ({GOOGLE_MAPS} routing)'
        chips = "".join(flag_chip(n, s[k]) for n, k in [("Wheelchair", "wheelchair_accessible"),
                                                        ("Generator", "generator_onsite"),
                                                        ("Surge zone", "in_surge_slosh_area"),
                                                        ("100-yr floodplain", "in_100_yr_floodplain")])
        cap = s["evacuation_capacity"] if s["evacuation_capacity"] is not None else "not recorded"
        maps_block = ""
        if v:
            maps_block = (f'<div style="margin-top:6px">{badge(vk, vl)} <span class="er-muted">'
                          f'{esc(v.get("maps_name") or "")} · checked {hhmm(v["checked_at"])}</span></div>'
                          + segments_html(v["summary"], v["sources"], v["checked_at"]))
        st.html(f'<div class="er-card">{badge(kind, label)} <b>{esc(s["name"])}</b> '
                f'<span class="er-muted">{esc(s["shelter_id"])} · {esc(s["usage"] or "usage n/a")} · '
                f'capacity {esc(cap)} · serves {esc(", ".join(d["serves"]) or "n/a")}</span>'
                f'<div class="er-muted">{dist}</div><div><span class="er-muted">FEMA record (historical):</span> '
                f'{chips}</div>{maps_block}'
                f'<div style="margin-top:6px"><b>Why this verdict</b></div>'
                f'{segments_html(d["verdict_reason"], b["sources"], v["checked_at"] if v else None)}</div>')
    st.markdown("#### 3. What to fix before the season")
    for fx in b["fixes"]:
        a = fx["affected"]
        ids = ", ".join(a["tracts"] + a["shelters"] + a["care_facilities"])
        st.html(f'<div class="er-card"><b>{fx["priority"]}. {esc(fx["title"])}</b> '
                f'<span class="er-muted">{esc(fx["category"].replace("_", " "))}</span>'
                f'<div class="er-seg">{esc(fx["recommendation"])}</div><div class="er-muted">Affects: {esc(ids) or "county-wide"}'
                f'</div>{segments_html([{"text": "Google Maps evidence for this fix.", "source_ids": fx["source_ids"], "freshness": "live"}], b["sources"]) if fx["source_ids"] else ""}</div>')
        if ids and st.button("Show on map", key=f"fix_{fx['priority']}"):
            set_highlight(a)
            st.rerun()
    st.markdown("#### 4. What we could not tell you")
    for u in b["unknowns"]:
        count = (f'<b>{u["count"]}</b>' + (f' of {u["of"]}' if u["of"] is not None else "") + f' {esc(u["unit"])}'
                 if u["count"] is not None else '<b>Not known</b>')
        st.html(f'<div class="er-card"><b>{esc(u["topic"])}</b>: {count}<div class="er-seg">{esc(u["why"])}</div>'
                f'<div class="er-muted">How to close it: {esc(u["how_to_close"])}</div></div>')
    st.download_button("Download brief (FEMA data + place_id only)", export_brief(b),
                       file_name=f"readiness_brief_{b['county']['county_fips']}.json", mime="application/json")


with right:
    tab_brief, tab_shelter, tab_chat, tab_audit = st.tabs(["Brief", "Shelter", "Chat", "Audit"])

    with tab_brief:
        if ss.brief:
            render_brief(ss.brief)
        elif ss.brief_id:
            @st.fragment(run_every=2)
            def poll():
                try:
                    s = api.brief(ss.brief_id)
                except api.ApiError as e:
                    show_error(e, "Lost the brief")
                    return
                p = s["progress"]
                if s["status"] == "complete":
                    ss.brief = s["brief"]
                    st.rerun()
                elif s["status"] == "failed":
                    st.error(f"The brief failed: {s['error']['message'] if s['error'] else 'unknown error'}")
                else:
                    frac = p["done"] / p["total"] if p["total"] else 0.0
                    st.progress(min(frac, 1.0), text=f"{p['message']} ({p['done']}/{p['total']})")
                    st.caption("A brief takes about two minutes: each shelter is checked on Google Maps one "
                               "building at a time.")
            poll()
        else:
            st.info("Pick a county and press **Generate brief**. The brief ranks who cannot leave, checks "
                    "candidate shelters one by one on Google Maps, and lists what to fix and what nobody knows.")

    with tab_shelter:
        opts = sorted(m["shelters"], key=lambda s: s["name"])
        ids = [s["shelter_id"] for s in opts]
        names = {s["shelter_id"]: s["name"] for s in opts}
        if ids:
            sel = st.selectbox("Shelter (or click one on the map)", ids,
                               index=ids.index(ss.selected_shelter) if ss.selected_shelter in ids else 0,
                               format_func=lambda i: f"{names[i]} ({i})")
            ss.selected_shelter = sel
            try:
                det = api.shelter(sel)
            except api.ApiError as e:
                show_error(e, "Could not load the shelter")
                det = None
            if det:
                top = [t["geo_id"] for t in m["tracts"][:10]]
                origin = st.selectbox("Drive time from tract (optional)", [None] + top,
                                      format_func=lambda g: "No drive time" if g is None else f"Tract {g}")
                if st.button("Check on Google Maps", type="primary"):
                    with st.spinner("Asking Google Maps about this one building..."):
                        try:
                            ss.verifications[sel] = api.verify(sel, origin, origin is not None)
                        except api.ApiError as e:
                            show_error(e, "The check failed")
                v = ss.verifications.get(sel) or det.get("last_verification")
                col_a, col_b = st.columns(2)
                with col_a:
                    st.html(f'<div class="er-card"><b>FEMA record (historical)</b><br>{esc(det["name"])}<br>'
                            f'<span class="er-muted">{esc(det["address"])}</span><br>'
                            f'usage {esc(det["usage"] or "n/a")} · capacity '
                            f'{esc(det["evacuation_capacity"] if det["evacuation_capacity"] is not None else "not recorded")}'
                            f'<br>{flag_chip("Wheelchair", det["wheelchair_accessible"])}'
                            f'{flag_chip("Generator", det["generator_onsite"])}'
                            f'{flag_chip("Surge zone", det["in_surge_slosh_area"])}'
                            f'{flag_chip("100-yr floodplain", det["in_100_yr_floodplain"])}<br>'
                            f'<span class="er-muted">medical-needs: {"yes" if det["is_medical"] else "no"} · '
                            f'population {esc(det["population_code"] or "n/a")} · FEMA status code '
                            f'{esc(det["shelter_status_code"] or "n/a")} (a historical registry, not operational status)'
                            f'</span></div>')
                with col_b:
                    if v:
                        vk, vl = VSTATUS[v["status"]]
                        wc = v["wheelchair"]
                        open_txt = {True: "open now", False: "closed now", None: "hours unknown"}[v["open_now"]]
                        st.html(f'<div class="er-card"><b>{GOOGLE_MAPS} (checked {hhmm(v["checked_at"])})</b><br>'
                                f'{badge(vk, vl)}<br>{esc(v.get("maps_name") or "no name returned")} · '
                                f'{esc(v.get("place_type") or "type unknown")}<br>'
                                f'<span class="er-muted">{open_txt} (business hours, not shelter status)</span><br>'
                                f'Wheelchair entrance: {esc(wc["maps"].replace("_", " "))} · record '
                                f'{esc("not recorded" if wc["record"] == "unrecorded" else wc["record"])} · '
                                f'<b>{esc(wc["agreement"].replace("_", " "))}</b>'
                                f'{segments_html(v["summary"], v["sources"], v["checked_at"])}</div>')
                    else:
                        st.html('<div class="er-card er-muted">Not checked on Google Maps yet in this session.</div>')

    with tab_chat:
        for msg in ss.chat:
            with st.chat_message(msg["role"]):
                if msg["role"] == "user":
                    st.markdown(esc(msg["text"]))
                else:
                    r = msg["resp"]
                    body = segments_html(r["reply"], r["sources"])
                    if r["refused"]:
                        body = f'<div class="er-refused">{body}<div><b>{esc(EOC)}</b></div></div>'
                    tools = ", ".join(f"{t['name']} ({t['duration_ms'] / 1000:.1f}s)" for t in r["tool_calls"])
                    st.html(body + (f'<div class="er-muted">tools: {esc(tools)}</div>' if tools else ""))
        q = st.chat_input("Ask a planning question, e.g. is a shelter still there?")
        if q:
            ss.chat.append({"role": "user", "text": q})
            with st.spinner("Thinking (Google Maps checks take a few seconds each)..."):
                try:
                    r = api.chat(q, ss.chat_session, fips, ss.brief_id)
                    ss.chat_session = r["session_id"]
                    ss.chat.append({"role": "assistant", "resp": r})
                    if any(r["highlight"].values()):
                        set_highlight(r["highlight"])
                except api.ApiError as e:
                    ss.chat.append({"role": "assistant", "resp": {
                        "reply": [{"text": f"Error: {e.message}", "source_ids": [], "freshness": "historical"}],
                        "sources": [], "tool_calls": [], "refused": False}})
            st.rerun()

    with tab_audit:
        if not ss.brief:
            st.info("The equity audit runs on a completed brief.")
        else:
            if ss.audit is None:
                try:
                    ss.audit = api.audit(ss.brief_id)
                except api.ApiError as e:
                    show_error(e, "Could not load the audit")
            if ss.audit:
                a = ss.audit
                st.caption(a["compares"])
                st.dataframe(pd.DataFrame(a["rows"]), hide_index=True, width="stretch")
                st.caption("Race and ethnicity are never inputs to the ranking; they appear here only to audit "
                           "where the recommendations landed. used_as_input shows which metrics fed the ranking.")
                if a.get("finding"):
                    st.markdown(f"**Finding:** {a['finding']}")
