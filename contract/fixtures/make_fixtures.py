"""Regenerate contract/fixtures/*.json from synthetic Lee County rows, through the real solver.

    .venv/bin/python contract/fixtures/make_fixtures.py

Values are synthetic (marked "_fixture": true). Places sit at plausible Lee County coordinates, but
the names, capacities and flags are invented. Real Lee counts from the data lane: 84 pre-landfall
shelters, 72 with a recorded capacity, wheelchair 2 YES / 5 NO / 77 UNRECORDED, power-dependent
Medicare beneficiaries 7,381.
"""

import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.allocate import solve  # noqa: E402
from agent.rehearse import SCENARIOS, pick_shelters_down, rehearse_from, storm_track_payload  # noqa: E402

OUT = Path(__file__).resolve().parent
GEN = "2026-10-20T16:00:00+00:00"
FIPS = "12071"

TRACTS = [  # geo_id, lat, lon, households, no_vehicle, pph
    ("12071000301", 26.6406, -81.8723, 1450, 290, 2.3),   # Fort Myers downtown
    ("12071000502", 26.6290, -81.8460, 1320, 240, 2.6),   # Dunbar
    ("12071000600", 26.6152, -81.8601, 1610, 205, 2.4),
    ("12071010203", 26.5629, -81.9495, 2100, 96, 2.5),    # Cape Coral
    ("12071010410", 26.6600, -81.9800, 1880, 61, 2.6),
    ("12071050100", 26.7010, -81.8810, 1600, 150, 2.2),   # North Fort Myers
    ("12071060200", 26.6081, -81.6301, 2400, 180, 3.1),   # Lehigh Acres
    ("12071060410", 26.5900, -81.6020, 2250, 120, 3.2),
    ("12071040100", 26.4520, -81.9480, 1300, 70, 1.9),    # Fort Myers Beach
    ("12071080100", 26.3398, -81.7787, 1700, 85, 2.2),    # Bonita Springs
    ("12071070300", 26.4300, -81.8100, 1900, 60, 2.1),    # Estero
    ("12071090100", 26.6100, -82.1300, 1100, 55, 2.0),    # Pine Island (far west)
]

SHELTERS = [  # id, name, lat, lng, capacity|None, wheelchair, generator, surge, medical
    ("FL0412", "Dunbar Community Center (fixture)", 26.6335, -81.8478, 600, "UNRECORDED", "UNRECORDED", "UNRECORDED", False),
    ("FL0418", "Fort Myers High School (fixture)", 26.6230, -81.8710, 700, "YES", "YES", "UNRECORDED", False),
    ("FL0433", "Island Coast High School (fixture)", 26.6420, -81.9900, 300, "UNRECORDED", "UNRECORDED", "UNRECORDED", False),
    ("FL0437", "Cape Coral Middle School (fixture)", 26.5700, -81.9600, 500, "NO", "UNRECORDED", "UNRECORDED", False),
    ("FL0451", "North Fort Myers Academy (fixture)", 26.7100, -81.8900, 400, "UNRECORDED", "YES", "UNRECORDED", True),
    ("FL0462", "Harns Marsh Middle School (fixture)", 26.6200, -81.6200, 600, "UNRECORDED", "UNRECORDED", "UNRECORDED", False),
    ("FL0470", "Estero Recreation Center (fixture)", 26.4350, -81.8050, 800, "UNRECORDED", "UNRECORDED", "YES", False),
    ("FL0473", "Beach Elementary (fixture)", 26.4560, -81.9420, 350, "UNRECORDED", "UNRECORDED", "YES", False),
    ("FL0480", "Bonita Springs Middle (fixture)", 26.3450, -81.7700, None, "UNRECORDED", "UNRECORDED", "UNRECORDED", False),
    ("FL0491", "Riverdale High School (fixture)", 26.6700, -81.7500, 400, "UNRECORDED", "UNRECORDED", "NO", False),
]


def km(a_lat, a_lng, b_lat, b_lng):
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def rows():
    tracts = [
        {"geo_id": g, "county_fips": FIPS, "lat": la, "lon": lo, "total_pop": int(hh * pph), "households": hh,
         "households_no_vehicle": nv, "persons_per_household": pph, "est_persons_no_vehicle": nv * pph,
         "share_no_vehicle": round(nv / hh, 4)}
        for g, la, lo, hh, nv, pph in TRACTS
    ]
    edges = []
    for t in tracts:
        dists = sorted(((km(t["lat"], t["lon"], s[2], s[3]), s) for s in SHELTERS), key=lambda x: x[0])
        for k, (d, s) in enumerate(dists[:15], start=1):
            if d > 40:
                continue
            edges.append({
                "geo_id": t["geo_id"], "county_fips": FIPS, "shelter_id": s[0], "shelter_name": s[1],
                "shelter_county_fips": FIPS, "shelter_lat": s[2], "shelter_lng": s[3],
                "straight_line_km": round(d, 2), "rank_k": k, "usage": "EVAC", "is_pre_landfall": True,
                "evacuation_capacity": s[4], "capacity_status": "RECORDED" if s[4] else "UNRECORDED",
                "capacity_implausible": False, "wheelchair_status": s[5], "generator_status": s[6],
                "surge_status": s[7], "is_medical": s[8],
            })
    return tracts, edges


CONE = {"type": "Polygon", "coordinates": [[
    [-84.6, 23.6], [-83.9, 24.9], [-83.2, 25.9], [-82.6, 26.6], [-82.0, 27.3], [-81.3, 28.0],
    [-80.9, 28.6], [-81.7, 29.0], [-82.4, 28.3], [-83.0, 27.5], [-83.6, 26.6], [-84.3, 25.6],
    [-85.1, 24.3], [-84.6, 23.6]]]}
TRACK = [
    {"lat": 24.5, "lng": -83.3, "valid_at": "2022-09-27T12:00:00Z", "wind_kt": 105},
    {"lat": 25.6, "lng": -82.7, "valid_at": "2022-09-28T00:00:00Z", "wind_kt": 120},
    {"lat": 26.7, "lng": -82.2, "valid_at": "2022-09-28T19:05:00Z", "wind_kt": 130},
    {"lat": 27.4, "lng": -81.6, "valid_at": "2022-09-29T06:00:00Z", "wind_kt": 65},
]


def mark(obj, note):
    return {"_fixture": True, "_note": note, **obj}


def main():
    tracts, edges = rows()
    alloc = solve(tracts, edges, county_fips=FIPS, generated_at=GEN)
    down_rows = [
        {"shelter_id": "FL0473", "shelter_name": "Beach Elementary (fixture)", "lat": 26.4560, "lng": -81.9420,
         "reason": "IN_CONE_AND_SURGE", "advisory": "22", "down_rank": 1},
        {"shelter_id": "FL0412", "shelter_name": "Dunbar Community Center (fixture)", "lat": 26.6335, "lng": -81.8478,
         "reason": "IN_CONE_AND_SURGE", "advisory": "22", "down_rank": 2},
    ]
    down = pick_shelters_down(edges, down_rows)
    sc = dict(SCENARIOS["ian_2022"], advisory="22", issued_at="2022-09-27T15:00:00Z", cone=CONE)
    reh = rehearse_from(tracts, edges, down, sc, county_fips=FIPS)
    reh["generated_at"] = GEN
    storm = storm_track_payload("ian_2022", [
        {"advisory": "18", "issued_at": "2022-09-26T03:00:00Z", "cone_geojson": json.dumps(CONE), "track_geojson": json.dumps(TRACK)},
        {"advisory": "22", "issued_at": "2022-09-27T15:00:00Z", "cone_geojson": json.dumps(CONE), "track_geojson": json.dumps(TRACK)},
    ])

    county_map = {
        "payload_type": "county_map.v1", "schema_version": 1, "county_fips": FIPS, "generated_at": GEN,
        "county": {"fips": FIPS, "name": "Lee", "lat": 26.5767, "lng": -81.8464},
        "tracts": [{"geo_id": t["geo_id"], "lat": t["lat"], "lng": t["lon"], "total_pop": t["total_pop"],
                    "households": t["households"], "households_no_vehicle": t["households_no_vehicle"],
                    "share_no_vehicle": t["share_no_vehicle"], "est_persons_no_vehicle": t["est_persons_no_vehicle"]}
                   for t in tracts],
        "shelters": [{"shelter_id": s[0], "name": s[1], "address": None, "lat": s[2], "lng": s[3], "usage": "EVAC",
                      "is_pre_landfall": True, "capacity": s[4], "capacity_status": "RECORDED" if s[4] else "UNRECORDED",
                      "capacity_implausible": False, "wheelchair": s[5], "generator": s[6], "surge": s[7],
                      "is_medical": s[8], "name_says_inactive": False} for s in SHELTERS] + [
            {"shelter_id": "FL0499", "name": "Lee Civic Center (fixture, POST only)", "address": None, "lat": 26.7200,
             "lng": -81.8000, "usage": "POST", "is_pre_landfall": False, "capacity": 0, "capacity_status": "RECORDED",
             "capacity_implausible": False, "wheelchair": "UNRECORDED", "generator": "UNRECORDED",
             "surge": "UNRECORDED", "is_medical": False}],
    }

    brief = json.loads((ROOT / "docs/samples/readiness_brief.v1.json").read_text())
    brief.pop("_note", None)
    brief["county"].update(lat=26.5767, lng=-81.8464)
    if brief.get("power_dependence"):
        brief["power_dependence"].update(power_dependent_dme=7381, power_dependent_is_censored=False)
    tract_xy = {t["geo_id"]: (t["lat"], t["lon"]) for t in tracts}
    for i, w in enumerate(brief.get("who_cannot_leave", [])):
        lat, lng = tract_xy.get(w["geo_id"], (TRACTS[i % len(TRACTS)][1], TRACTS[i % len(TRACTS)][2]))
        w.update(lat=lat, lng=lng)
    for i, w in enumerate(brief.get("where_they_would_go", [])):
        s = SHELTERS[i % len(SHELTERS)]
        w.update(lat=s[2], lng=s[3], flags=w.get("flags", []))

    verification = {
        "payload_type": "shelter_verification.v1", "schema_version": 1, "verified_at": "2026-10-20T16:01:12+00:00",
        "results": [
            {"shelter_id": "FL0418", "shelter_name": "Fort Myers High School (fixture)", "name": "Fort Myers High School (fixture)",
             "lat": 26.6230, "lng": -81.8710, "address": "2635 Cortez Blvd, Fort Myers, 33901",
             "record": {"label": "RECORDED", "source": "FEMA National Shelter System (historical registry)",
                        "evacuation_capacity": 1400, "capacity_implausible": False, "usage": "EVAC",
                        "wheelchair": "YES", "generator": "YES", "surge": "UNRECORDED", "is_medical": False},
             "live": {"label": "VERIFIED LIVE", "verified_at": "2026-10-20T16:01:12+00:00",
                      "sources": [{"title": "Fort Myers High School", "uri": "https://maps.google.com/?cid=0000000000000000001",
                                   "place_id": "ChIJfixture0001"}],
                      "exists": True, "current_name": "Fort Myers High School", "place_type": "High school",
                      "closed": "no", "wheelchair_entrance": "yes", "drive_minutes": 7.5, "road_km": 3.4,
                      "drive_basis": "ROUTED (Google Maps routing, typical traffic, not evacuation traffic)",
                      "notes": "Public high school at this address.", "status": "VERIFIED"},
             "accessibility": {"label": "RECORDED", "summary": "FEMA records wheelchair access as YES. Google Maps reports the entrance as yes."},
             "verdict": "KEEP", "why": "Google Maps confirms a place at this address.", "flags": [], "fixes": [],
             "stored_pointer": {"place_id": "ChIJfixture0001"}},
            {"shelter_id": "FL0412", "shelter_name": "Dunbar Community Center (fixture)", "name": "Dunbar Community Center (fixture)",
             "lat": 26.6335, "lng": -81.8478, "address": "3250 Dr Martin Luther King Jr Blvd, Fort Myers, 33916",
             "record": {"label": "RECORDED", "source": "FEMA National Shelter System (historical registry)",
                        "evacuation_capacity": 600, "capacity_implausible": False, "usage": "EVAC",
                        "wheelchair": "UNRECORDED", "generator": "UNRECORDED", "surge": "UNRECORDED", "is_medical": False},
             "live": {"label": "VERIFIED LIVE", "verified_at": "2026-10-20T16:01:12+00:00",
                      "sources": [{"title": "STARS Complex", "uri": "https://maps.google.com/?cid=0000000000000000002",
                                   "place_id": "ChIJfixture0002"}],
                      "exists": True, "current_name": "STARS Complex", "place_type": "Community center",
                      "closed": "no", "wheelchair_entrance": "unknown", "drive_minutes": None, "road_km": None,
                      "drive_basis": None, "notes": "Same address, different name.", "status": "VERIFIED"},
             "accessibility": {"label": "UNRECORDED", "summary": "Nobody has recorded whether a wheelchair user can get in, and Google Maps did not say."},
             "verdict": "KEEP", "why": "Google Maps confirms a place at this address. Known on Google Maps as 'STARS Complex'.",
             "flags": ["NAME_DIFFERS"],
             "fixes": [{"type": "NAME_DIFFERS", "subject": "Dunbar Community Center (fixture) (FL0412)",
                        "evidence": "FEMA: 'Dunbar Community Center (fixture)'. Google Maps: 'STARS Complex'.",
                        "action": "Update the registry name so a name-only lookup does not miss it.", "label": "VERIFIED LIVE"}],
             "stored_pointer": {"place_id": "ChIJfixture0002"}},
        ],
        "not_in_registry": [], "skipped_over_limit": [],
        "grounding_ledger": {"maps_calls": 2, "with_maps_sources": 2, "definite": 2, "unknown": 0, "dropped": 0,
                             "wheelchair_answers_from_maps": 1, "routing_requested": 1, "routes_returned": 1,
                             "changes": ["KEEP: Dunbar Community Center (fixture): Known on Google Maps as 'STARS Complex'."]},
        "display_rule": "Show each result's Google Maps sources immediately after the sentence they support.",
    }

    note = "Synthetic Lee County fixture generated by contract/fixtures/make_fixtures.py through the real solver."
    out = {
        "allocation.sample.json": mark(alloc, note),
        "rehearsal.ian.sample.json": mark(reh, note),
        "storm_track.ian.sample.json": mark(storm, "Approximate cone and track for UI work; real NHC geometry comes from readiness_ops.storm_advisories."),
        "county_map.lee.json": mark(county_map, note),
        "readiness_brief.lee.json": mark(brief, "komeil-d2's docs/samples/readiness_brief.v1.json with v0.1 coordinate additions and real Lee emPOWER count (7,381)."),
        "shelter_verification.sample.json": mark(verification, "Shape of verify.verify_shelters output plus v0.1 name/lat/lng. Maps titles/URIs are fake."),
    }
    for name, obj in out.items():
        (OUT / name).write_text(json.dumps(obj, indent=2) + "\n")
        print("wrote", name)


if __name__ == "__main__":
    main()
