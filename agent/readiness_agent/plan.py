"""Who goes where, and what a Hurricane Ian replay does to it. Never calls Google Maps.

Ported from main's agent/allocate.py and agent/rehearse.py (same rules, same OR-Tools model), fed
from this branch's tables: sql/14 demand, sql/15 tract-shelter pairs, sql/16 Ian shelters down,
sql/17-18 the NHC archive the data lane loaded into readiness_ops.

Allocation: min-cost flow over straight-line person-km. Demand = households_no_vehicle x persons per
household (group quarters excluded) x SHELTER_SEEKING_SHARE: a seat for every resident of a no-car
household, an upper bound. Capacity = FEMA evacuation_capacity as filed; unrecorded or implausible
capacity is left out and listed, never guessed. Recorded accessibility is not rewarded: that would
steer the plan toward good paperwork rather than toward people.

Rehearsal: a tabletop August exercise, not response. Solve the plan as filed, take the top
DEFAULT_SHELTERS_DOWN in-plan shelters the Ian rule puts at risk offline, solve again, and diff.
Grounding is never used here: Google's terms exclude emergency-response use, and a rehearsal has to
behave like the real activation would, from recorded status alone.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from readiness_agent.tools import bq

WALK_KM = 1.5  # a short walk for an older adult
PARATRANSIT_KM = 15.0  # straight-line proxy for a 30-minute para-transit run
CAPACITY_PLAUSIBLE_MAX = 100_000
SHELTER_SEEKING_SHARE = 1.0
SITE_OPTIONS = 3
UNASSIGNED_COST = 10_000_000  # tract -> sink arc priced above any real assignment: always feasible
KM_COST_SCALE = 100  # min-cost flow needs integer costs
DEFAULT_ADVISORY = "19"  # first NHC advisory whose cone covered Fort Myers (22 h before landfall)
DEFAULT_SHELTERS_DOWN = 2  # enough to move people, few enough to follow every reassignment
_INACTIVE = re.compile(r"\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\b", re.IGNORECASE)

IAN = {
    "id": "ian_2022", "name": "Hurricane Ian (2022) tabletop replay", "storm_id": "AL092022",
    "landfall_at": "2022-09-28T19:05:00Z", "landfall": {"lat": 26.7, "lon": -82.2},
    "source": "NOAA NHC GIS archive (public domain); landfall per NHC Tropical Cyclone Report AL092022",
}
NOTE = "Tabletop rehearsal: no Google Maps grounding is used."


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def km_between(a: Mapping[str, Any], b: Mapping[str, Any]) -> float | None:
    if None in (a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon")):
        return None
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# ---- allocation (pure) ------------------------------------------------------------------------


def _shelters(edges: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for e in edges:
        sid = str(e["shelter_id"])
        if sid in out:
            continue
        cap = _num(e.get("evacuation_capacity"))
        out[sid] = {
            "shelter_id": sid, "name": e.get("shelter_name") or sid,
            "lat": _num(e.get("shelter_lat")), "lon": _num(e.get("shelter_lng")),
            "county_fips": e.get("shelter_county_fips"),
            "capacity": None if cap is None else int(cap),
            "capacity_implausible": bool(e.get("capacity_implausible")) or (cap or 0) > CAPACITY_PLAUSIBLE_MAX,
            "wheelchair_accessible": e.get("wheelchair_accessible") or "unrecorded",
            "generator_onsite": e.get("generator_onsite") or "unrecorded",
            "in_surge_slosh_area": e.get("in_surge_slosh_area") or "unrecorded",
            "is_medical": bool(e.get("is_medical")),
            "name_says_inactive": bool(_INACTIVE.search(str(e.get("shelter_name") or ""))),
        }
    return out


def _duplicates(shelters: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """Secondary FEMA rows for the same building (same name and coordinates): keep the best one."""
    groups: dict[tuple, list] = defaultdict(list)
    for s in shelters.values():
        if s["lat"] is None or s["lon"] is None:
            continue
        groups[(re.sub(r"\s+", " ", str(s["name"]).strip().upper()), round(s["lat"], 4), round(s["lon"], 4))].append(s)
    dups: set[str] = set()
    for rows in groups.values():
        rows.sort(key=lambda s: (0 if s["capacity"] and not s["capacity_implausible"] and not s["name_says_inactive"] else 1,
                                 -(s["capacity"] or 0), s["shelter_id"]))
        dups.update(s["shelter_id"] for s in rows[1:])
    return dups


def _excluded(s: Mapping[str, Any], down: set[str], dups: set[str]) -> str | None:
    if s["shelter_id"] in down:
        return "DOWN"
    if s["name_says_inactive"]:
        return "NAME_SAYS_INACTIVE"
    if s["shelter_id"] in dups:
        return "DUPLICATE_RECORD"
    if not s["capacity"]:
        return "CAPACITY_UNRECORDED"
    if s["capacity_implausible"]:
        return "CAPACITY_IMPLAUSIBLE"
    return None


def site_options(unassigned: list[Mapping[str, Any]], max_km: float, n: int = SITE_OPTIONS) -> list[dict[str, Any]]:
    """Greedy: the unplaced-tract centroids that would each seat the most remaining unplaced people.
    The second option counts only people the first did not cover. Seats needed = people covered."""
    left = {u["geo_id"]: dict(u) for u in unassigned if u["persons"] > 0}
    out: list[dict[str, Any]] = []
    while left and len(out) < n:
        best = None
        for c in left.values():
            covered = [u for u in left.values() if (d := km_between(c, u)) is not None and d <= max_km]
            persons = sum(u["persons"] for u in covered)
            if best is None or persons > best[1]:
                best = (c, persons, covered)
        if best is None or best[1] <= 0:
            break
        c, persons, covered = best
        out.append({"geo_id": c["geo_id"], "lat": c["lat"], "lon": c["lon"], "persons_covered": persons,
                    "tracts_covered": sorted(u["geo_id"] for u in covered)})
        for u in covered:
            left.pop(u["geo_id"], None)
    return out


def solve(tracts: list[Mapping[str, Any]], edges: list[Mapping[str, Any]], *, shelters_down: Iterable[str] = (),
          max_km: float = PARATRANSIT_KM, share: float = SHELTER_SEEKING_SHARE, county_fips: str = "") -> dict[str, Any]:
    """tracts (sql/14 rows) + edges (sql/15 rows) -> allocation. Pure apart from OR-Tools."""
    from ortools.graph.python import min_cost_flow

    down = {str(s) for s in shelters_down}
    shelters = _shelters(edges)
    dups = _duplicates(shelters)
    for s in shelters.values():
        s["excluded_reason"] = _excluded(s, down, dups)
        s["assigned_persons"] = 0
    rows: dict[str, dict[str, Any]] = {}
    for t in tracts:
        est = _num(t.get("est_persons_no_vehicle")) or 0.0
        rows[str(t["geo_id"])] = {
            "geo_id": str(t["geo_id"]), "lat": _num(t.get("lat")), "lon": _num(t.get("lon")),
            "households_no_vehicle": int(_num(t.get("households_no_vehicle")) or 0),
            "persons_per_household": _num(t.get("persons_per_household")),
            "demand_persons": max(0, int(round(est * share))), "assigned_persons": 0, "unassigned_persons": 0,
        }
    usable = [e for e in edges if str(e["geo_id"]) in rows and shelters[str(e["shelter_id"])]["excluded_reason"] is None
              and _num(e.get("straight_line_km")) is not None and float(e["straight_line_km"]) <= max_km]
    total = sum(t["demand_persons"] for t in rows.values())
    assignments: list[dict[str, Any]] = []
    unassigned: list[dict[str, Any]] = []
    status = "NO_DEMAND" if total == 0 else "OPTIMAL"
    if total:
        mcf = min_cost_flow.SimpleMinCostFlow()
        tids = [g for g, t in rows.items() if t["demand_persons"] > 0]
        sids = sorted({str(e["shelter_id"]) for e in usable})
        node = {f"t:{g}": i for i, g in enumerate(tids)}
        node.update({f"s:{s}": len(node) + i for i, s in enumerate(sids)})
        sink = len(node)
        arcs = []
        for e in usable:
            g, s = str(e["geo_id"]), str(e["shelter_id"])
            if f"t:{g}" in node:
                arcs.append((mcf.add_arc_with_capacity_and_unit_cost(
                    node[f"t:{g}"], node[f"s:{s}"], rows[g]["demand_persons"],
                    int(round(float(e["straight_line_km"]) * KM_COST_SCALE))), e))
        for s in sids:
            mcf.add_arc_with_capacity_and_unit_cost(node[f"s:{s}"], sink, int(shelters[s]["capacity"]), 0)
        spill = {}
        for g in tids:
            spill[g] = mcf.add_arc_with_capacity_and_unit_cost(node[f"t:{g}"], sink, rows[g]["demand_persons"], UNASSIGNED_COST)
            mcf.set_node_supply(node[f"t:{g}"], rows[g]["demand_persons"])
        mcf.set_node_supply(sink, -total)
        if mcf.solve() != mcf.OPTIMAL:
            status = "SOLVER_ERROR"
        else:
            for arc, e in arcs:
                flow = mcf.flow(arc)
                if flow <= 0:
                    continue
                g, s, km = str(e["geo_id"]), str(e["shelter_id"]), float(e["straight_line_km"])
                rows[g]["assigned_persons"] += flow
                shelters[s]["assigned_persons"] += flow
                assignments.append({"geo_id": g, "shelter_id": s, "persons": int(flow), "straight_line_km": round(km, 2),
                                    "mode": "WALK" if km <= WALK_KM else "PARATRANSIT"})
            reachable = {str(e["geo_id"]) for e in usable}
            for g, arc in spill.items():
                left = mcf.flow(arc)
                if left > 0:
                    rows[g]["unassigned_persons"] = int(left)
                    unassigned.append({"geo_id": g, "persons": int(left), "lat": rows[g]["lat"], "lon": rows[g]["lon"],
                                       "reason": "CAPACITY_EXHAUSTED" if g in reachable else "NO_SHELTER_WITHIN_MAX_KM"})
    for s in shelters.values():
        cap = s["capacity"] if s["excluded_reason"] is None else None
        s["utilization"] = round(s["assigned_persons"] / cap, 4) if cap else None
    unassigned.sort(key=lambda u: -u["persons"])
    excluded = defaultdict(int)
    for s in shelters.values():
        if s["excluded_reason"]:
            excluded[s["excluded_reason"]] += 1
    return {
        "county_fips": county_fips, "status": status, "max_km": max_km, "shelter_seeking_share": share,
        "totals": {"demand_persons": total, "assigned_persons": sum(a["persons"] for a in assignments),
                   "unassigned_persons": sum(u["persons"] for u in unassigned),
                   "usable_capacity": sum(s["capacity"] for s in shelters.values() if s["excluded_reason"] is None),
                   "shelters_used": sum(1 for s in shelters.values() if s["assigned_persons"] > 0)},
        "tracts": sorted(rows.values(), key=lambda t: -t["demand_persons"]),
        "shelters": sorted(({k: v for k, v in s.items() if k != "name_says_inactive"} for s in shelters.values()),
                           key=lambda s: -s["assigned_persons"]),
        "assignments": sorted(assignments, key=lambda a: (a["geo_id"], -a["persons"])),
        "unassigned": unassigned,
        "site_options": site_options(unassigned, max_km),
        "excluded_counts": dict(excluded),
        "rules": {
            "demand": "households_no_vehicle x persons per household (group quarters excluded) x shelter_seeking_share",
            "capacity": "FEMA evacuation_capacity as filed; unrecorded or implausible capacity excluded, never assumed",
            "cost": "straight-line person-km; recorded accessibility is not rewarded",
            "distance": "Straight line (a lower bound on road distance)",
            "solver": "OR-Tools SimpleMinCostFlow",
        },
        "maps_used": False, "freshness": "historical",
    }


# ---- rehearsal (pure) -------------------------------------------------------------------------


def pick_down(alloc_shelters: Mapping[str, Mapping[str, Any]], down_rows: list[Mapping[str, Any]],
              n: int = DEFAULT_SHELTERS_DOWN) -> list[dict[str, Any]]:
    """Ranked at-risk shelters until n that the plan actually uses are taken. At-risk shelters the
    plan never counted on (no capacity on file) are listed with in_plan false: losing them moves
    nobody, which is itself a finding."""
    picked: list[dict[str, Any]] = []
    for d in sorted(down_rows, key=lambda d: d.get("down_rank") or 0):
        if sum(1 for p in picked if p["in_plan"]) >= n:
            break
        sid = str(d["shelter_id"])
        s = alloc_shelters.get(sid)
        in_plan = bool(s and s["excluded_reason"] is None)
        recorded = d["reason"] == "IN_SURGE_ZONE"
        picked.append({
            "shelter_id": sid, "name": d.get("name") or sid, "lat": d.get("lat"), "lon": d.get("lon"),
            "reason": d["reason"], "in_plan": in_plan, "capacity": s["capacity"] if s else None,
            "evidence": ("RECORDED: FEMA in_surge_slosh_area = yes" if recorded else
                         f"APPROXIMATE: inside the cone, and {round(100 * (d.get('flood_share') or 0))}% of the nearest "
                         "tract's residents are coastal-flood exposed (FEMA NRI)"),
        })
    return picked


def diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Pair each tract's lost seats with the seats it gained; whatever is left is newly unplaced."""
    tract_at = {t["geo_id"]: t for t in after["tracts"]}
    shelter_at = {s["shelter_id"]: s for s in before["shelters"]}

    def by_tract(alloc):
        m: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for a in alloc["assignments"]:
            m[a["geo_id"]][a["shelter_id"]] += a["persons"]
        return m

    b, a = by_tract(before), by_tract(after)
    moved = []
    for g in sorted(set(b) | set(a)):
        lost = [[s, b[g][s] - a[g].get(s, 0)] for s in b[g] if b[g][s] > a[g].get(s, 0)]
        gained = [[s, a[g][s] - b[g].get(s, 0)] for s in a[g] if a[g][s] > b[g].get(s, 0)]
        for lo in lost:
            for ga in gained:
                if lo[1] <= 0:
                    break
                if ga[1] <= 0:
                    continue
                n = min(lo[1], ga[1])
                t, s_old, s_new = tract_at.get(g, {}), shelter_at.get(lo[0], {}), shelter_at.get(ga[0], {})
                moved.append({"geo_id": g, "from_shelter_id": lo[0], "to_shelter_id": ga[0], "persons": n,
                              "tract_lat": t.get("lat"), "tract_lon": t.get("lon"),
                              "to_lat": s_new.get("lat"), "to_lon": s_new.get("lon"),
                              "from_lat": s_old.get("lat"), "from_lon": s_old.get("lon")})
                lo[1] -= n
                ga[1] -= n
    before_un = {u["geo_id"]: u["persons"] for u in before["unassigned"]}
    newly = [{"geo_id": u["geo_id"], "persons": u["persons"] - before_un.get(u["geo_id"], 0), "lat": u["lat"],
              "lon": u["lon"], "reason": u["reason"]}
             for u in after["unassigned"] if u["persons"] > before_un.get(u["geo_id"], 0)]
    moved.sort(key=lambda m: -m["persons"])
    return {"reassigned": moved, "newly_unassigned": sorted(newly, key=lambda u: -u["persons"])}


def _geom(v: Any) -> Any:
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _track(v: Any) -> list[dict[str, Any]]:
    obj = _geom(v)
    if isinstance(obj, dict) and obj.get("type") == "FeatureCollection":
        obj = [{"lat": f["geometry"]["coordinates"][1], "lon": f["geometry"]["coordinates"][0],
                "valid_at": (f.get("properties") or {}).get("valid_at"), "wind_kt": (f.get("properties") or {}).get("wind_kt")}
               for f in obj.get("features", []) if (f.get("geometry") or {}).get("type") == "Point"]
    if isinstance(obj, dict) and obj.get("type") == "LineString":
        obj = [{"lat": c[1], "lon": c[0]} for c in obj.get("coordinates", [])]
    out = []
    for p in obj if isinstance(obj, list) else []:
        lat, lon = p.get("lat"), p.get("lon", p.get("lng"))
        if lat is not None and lon is not None:
            out.append({"lat": lat, "lon": lon, "valid_at": p.get("valid_at"), "wind_kt": p.get("wind_kt")})
    return out


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return v.isoformat().replace("+00:00", "Z") if hasattr(v, "isoformat") else str(v)


# ---- loaders ----------------------------------------------------------------------------------


def allocation(county_fips: str, max_km: float = PARATRANSIT_KM, shelters_down: Iterable[str] = ()) -> dict[str, Any]:
    tracts = bq.run("14_tract_demand.sql", county_fips=county_fips)
    edges = bq.run("15_tract_shelter_edges.sql", county_fips=county_fips, max_km=float(max_km))
    return solve(tracts, edges, shelters_down=shelters_down, max_km=max_km, county_fips=county_fips)


def storm_track() -> dict[str, Any]:
    rows = bq.run("17_storm_track.sql", storm_id=IAN["storm_id"])
    best = bq.run("18_storm_best_track.sql", storm_id=IAN["storm_id"])
    return {
        "storm_id": IAN["storm_id"], "name": "Ian", "landfall_at": IAN["landfall_at"], "landfall": IAN["landfall"],
        "source": IAN["source"],
        "advisories": [{"advisory": str(r["advisory"]), "issued_at": _iso(r["issued_at"]),
                        "hours_before_landfall": r["hours_before_landfall"], "cone": _geom(r["cone_geojson"]),
                        "track": _track(r["track_json"])} for r in rows],
        "best_track": [{"lat": b["lat"], "lon": b["lng"], "valid_at": _iso(b["valid_at"]), "wind_kt": b["wind_kt"]} for b in best],
    }


def rehearse(county_fips: str, advisory: str = DEFAULT_ADVISORY, max_km: float = PARATRANSIT_KM,
             n_down: int = DEFAULT_SHELTERS_DOWN) -> dict[str, Any]:
    tracts = bq.run("14_tract_demand.sql", county_fips=county_fips)
    edges = bq.run("15_tract_shelter_edges.sql", county_fips=county_fips, max_km=float(max_km))
    down_rows = bq.run("16_ian_shelters_down.sql", county_fips=county_fips, max_km=float(max_km), advisory=advisory)
    adv = next((a for a in bq.run("17_storm_track.sql", storm_id=IAN["storm_id"]) if str(a["advisory"]) == advisory), None)
    before = solve(tracts, edges, max_km=max_km, county_fips=county_fips)
    picked = pick_down({s["shelter_id"]: s for s in before["shelters"]}, down_rows, n_down)
    down_ids = [p["shelter_id"] for p in picked if p["in_plan"]]
    after = solve(tracts, edges, shelters_down=down_ids, max_km=max_km, county_fips=county_fips)
    usable = {s["shelter_id"]: s for s in before["shelters"] if s["excluded_reason"] is None}
    d = diff(before, after)
    return {
        "county_fips": county_fips,
        "scenario": {**{k: v for k, v in IAN.items() if k != "landfall"}, "advisory": advisory,
                     "issued_at": _iso(adv["issued_at"]) if adv else None,
                     "hours_before_landfall": adv["hours_before_landfall"] if adv else None},
        "at_risk_total": len(down_rows),
        "shelters_down": [p for p in picked if p["in_plan"]],
        "at_risk_not_in_plan": [p for p in picked if not p["in_plan"]],
        "before": before["totals"], "after": after["totals"],
        "capacity_lost": sum(usable[i]["capacity"] for i in down_ids if i in usable),
        "reassigned": d["reassigned"][:40], "reassigned_total": len(d["reassigned"]),
        "newly_unassigned": d["newly_unassigned"],
        "cone": _geom(adv["cone_geojson"]) if adv else None,
        "maps_used": False, "note": NOTE, "freshness": "historical",
    }
