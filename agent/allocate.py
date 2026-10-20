"""Who goes where: assign people in no-vehicle households to pre-landfall shelters within capacity.

OR-Tools min-cost flow over straight-line distance. Never calls Google Maps: the allocator also
runs in tabletop rehearsal, where grounding is not used, and a tract-by-shelter drive-time matrix
would be hundreds of five-second calls. Drive time is refined afterwards, for chosen pairs only,
through verify_shelters.

SHELTER_SEEKING_SHARE = 1.0
    Plan a public-shelter seat for every resident of a no-vehicle household. Most evacuees with
    a car never use a public shelter, but this plan is about the people who have no car, and for
    them a seat that sits empty costs less than a household with nowhere to go. It is an upper
    bound, and the output says so; pass a lower share to test a what-if.

Demand: households_no_vehicle x persons_per_household, where persons per household excludes group
quarters (nursing homes and dorms are not households). ACS vehicle ownership, not ride access.

Capacity: FEMA evacuation_capacity as filed. Unrecorded capacity is not zero spaces and is not
assumed either: those shelters are left out of the solve and listed. Implausible capacities
(reachability.CAPACITY_PLAUSIBLE_MAX) are left out and listed, never overwritten.

Cost: person-kilometres only. Recorded accessibility is deliberately not rewarded: that would
steer the plan toward the shelters with good paperwork rather than toward the people. Shelters
whose wheelchair access nobody recorded become "assess this shelter" items, ranked by how many
people the plan sends there.

NAME_SAYS_INACTIVE
    FEMA still designates some shelters EVAC whose own name says INACTIVE, CLOSED, DEMOLISHED or
    DO NOT USE. They are left out of the solve and listed, the same rule the brief applies. The
    query's name_says_inactive column wins when present; the pattern is the fallback.

SITE_OPTIONS = 3
    Allocator part 2: where one newly designated or retrofitted pre-landfall site would place the
    most people the plan leaves without a seat. Candidates are the unplaced tracts' own centroids
    (a public building there is found later, with Google Maps, by a person); coverage is every
    unplaced person within max_km straight-line. Chosen greedily, so the second option counts only
    people the first did not already cover. Seats needed is the people covered: it says how big
    the site must be, not that one exists.

COMPACT_GAPS = 12
    The chat tool returns a compact payload (compact: true): totals, unplaced tracts, site options and
    the top gaps, without the per-pair assignment layer. A grounded request has a hard input-token cap,
    and a full Lee allocation is about 250 KB. The map fetches the full payload from the REST route the
    compact one points at.

UNASSIGNED_COST_PER_PERSON
    A direct tract-to-sink arc priced above any real assignment, so the solve is always feasible
    and demand that cannot be placed comes back as a named gap instead of an infeasible model.
"""

import asyncio
import datetime
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping, Optional

from .config import AgentConfig, county_fips_or_error
from .evidence import RECORDED, UNRECORDED, YES, payload, three_state
from .reachability import CAPACITY_PLAUSIBLE_MAX, PARATRANSIT_PROXY_KM, STRAIGHT_LINE, WALK_KM

try:  # ADK is present at runtime; tests may import the pure solver without it.
    from google.adk.tools import ToolContext
except ImportError:  # pragma: no cover
    ToolContext = Any  # type: ignore[misc,assignment]

SHELTER_SEEKING_SHARE = 1.0
SITE_OPTIONS = 3
COMPACT_GAPS = 12
UNASSIGNED_COST_PER_PERSON = 10_000_000
KM_COST_SCALE = 100  # integer cost units per km (min-cost flow needs integers)

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
_INACTIVE_RE = re.compile(r"\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\b", re.IGNORECASE)


def name_says_inactive(row: Mapping[str, Any]) -> bool:
    flag = row.get("name_says_inactive")
    if flag is not None:
        return bool(flag)
    return bool(_INACTIVE_RE.search(str(row.get("shelter_name") or row.get("name") or "")))

TRACT_NEED_SQL = """
SELECT geo_id, county_fips, lat, lon, total_pop, households, households_no_vehicle,
       persons_per_household, est_persons_no_vehicle, share_no_vehicle
FROM ${tract_need}
WHERE county_fips = @county_fips
"""

CANDIDATES_SQL = """
SELECT geo_id, shelter_id, shelter_name, shelter_county_fips, shelter_lat, shelter_lng,
       straight_line_km, rank_k, usage, is_pre_landfall, evacuation_capacity, capacity_status,
       capacity_implausible, wheelchair_status, generator_status, surge_status, is_medical, name_says_inactive
FROM ${candidates}
WHERE county_fips = @county_fips AND straight_line_km <= @max_km
"""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def demand_persons(tract: Mapping[str, Any], share: float) -> int:
    est = _num(tract.get("est_persons_no_vehicle"))
    if est is None:
        hh, pph = _num(tract.get("households_no_vehicle")), _num(tract.get("persons_per_household"))
        est = hh * pph if hh is not None and pph is not None else 0.0
    return max(0, int(round(est * share)))


def _shelters_from_edges(edges: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for e in edges:
        sid = str(e["shelter_id"])
        if sid in out:
            continue
        cap = _num(e.get("evacuation_capacity"))
        status = e.get("capacity_status") or ("RECORDED" if cap else "UNRECORDED")
        out[sid] = {
            "shelter_id": sid,
            "name": e.get("shelter_name"),
            "lat": _num(e.get("shelter_lat")),
            "lng": _num(e.get("shelter_lng")),
            "county_fips": e.get("shelter_county_fips"),
            "capacity": None if cap is None else int(cap),
            "capacity_status": status,
            "capacity_implausible": bool(e.get("capacity_implausible")) or (cap or 0) > CAPACITY_PLAUSIBLE_MAX,
            "is_pre_landfall": e.get("is_pre_landfall") is not False,
            "wheelchair": three_state(e.get("wheelchair_status")),
            "generator": three_state(e.get("generator_status")),
            "surge": three_state(e.get("surge_status")),
            "is_medical": bool(e.get("is_medical")),
            "name_says_inactive": name_says_inactive(e),
        }
    return out


def _duplicate_shelter_ids(shelters: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """Identify secondary FEMA shelter rows that duplicate the same physical building and coordinates."""
    groups: dict[tuple[str, float, float], list[Mapping[str, Any]]] = defaultdict(list)
    for s in shelters.values():
        norm_name = re.sub(r"\s+", " ", str(s.get("name") or "").strip().upper())
        lat, lng = s.get("lat"), s.get("lng")
        if not norm_name or lat is None or lng is None:
            continue
        groups[(norm_name, round(float(lat), 4), round(float(lng), 4))].append(s)
    dups: set[str] = set()
    for rows in groups.values():
        if len(rows) <= 1:
            continue
        ordered = sorted(
            rows,
            key=lambda s: (
                0 if (
                    s.get("is_pre_landfall")
                    and not s.get("name_says_inactive")
                    and s.get("capacity_status") == "RECORDED"
                    and s.get("capacity")
                    and not s.get("capacity_implausible")
                ) else 1,
                -(s.get("capacity") or 0),
                str(s.get("shelter_id")),
            ),
        )
        for dup in ordered[1:]:
            dups.add(str(dup["shelter_id"]))
    return dups


def _excluded_reason(
    s: Mapping[str, Any], down: set[str], exclude_surge: bool, duplicate_ids: Optional[set[str]] = None
) -> Optional[str]:
    if s["shelter_id"] in down:
        return "DOWN"
    if not s["is_pre_landfall"]:
        return "NOT_PRE_LANDFALL"
    if s["name_says_inactive"]:
        return "NAME_SAYS_INACTIVE"
    if duplicate_ids and s["shelter_id"] in duplicate_ids:
        return "DUPLICATE_RECORD"
    if s["capacity_status"] != "RECORDED" or not s["capacity"]:
        return "CAPACITY_UNRECORDED"
    if s["capacity_implausible"]:
        return "CAPACITY_IMPLAUSIBLE"
    if exclude_surge and s["surge"] == YES:
        return "IN_SURGE_ZONE"
    return None


def _gap(kind: str, subject: str, evidence: str, action: str, label: str, severity: str,
         ref: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    return {"type": kind, "subject": subject, "evidence": evidence, "action": action, "label": label,
            "severity": severity, "ref": dict(ref), **extra}


def solve(
    tracts: list[Mapping[str, Any]],
    edges: list[Mapping[str, Any]],
    *,
    shelters_down: Optional[Iterable[str]] = None,
    max_km: float = PARATRANSIT_PROXY_KM,
    shelter_seeking_share: float = SHELTER_SEEKING_SHARE,
    exclude_surge: bool = False,
    county_fips: str = "",
    generated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Pure: tracts (v_tract_need rows) and edges (v_tract_shelter_candidates rows) to allocation.v1."""
    from ortools.graph.python import min_cost_flow

    down = {str(s) for s in (shelters_down or [])}
    shelters = _shelters_from_edges(edges)
    duplicate_ids = _duplicate_shelter_ids(shelters)
    for s in shelters.values():
        s["excluded_reason"] = _excluded_reason(s, down, exclude_surge, duplicate_ids)
        s["assigned_persons"] = 0

    tract_rows: dict[str, dict[str, Any]] = {}
    for t in tracts:
        gid = str(t["geo_id"])
        tract_rows[gid] = {
            "geo_id": gid,
            "lat": _num(t.get("lat")),
            "lng": _num(t.get("lon", t.get("lng"))),
            "households_no_vehicle": int(_num(t.get("households_no_vehicle")) or 0),
            "persons_per_household": _num(t.get("persons_per_household")),
            "demand_persons": demand_persons(t, shelter_seeking_share),
            "assigned_persons": 0,
            "unassigned_persons": 0,
        }

    usable_edges = [
        e for e in edges
        if str(e["geo_id"]) in tract_rows
        and shelters[str(e["shelter_id"])]["excluded_reason"] is None
        and _num(e.get("straight_line_km")) is not None
        and float(e["straight_line_km"]) <= max_km
    ]

    total_demand = sum(t["demand_persons"] for t in tract_rows.values())
    assignments: list[dict[str, Any]] = []
    unassigned: list[dict[str, Any]] = []
    status = "NO_DEMAND" if total_demand == 0 else "OPTIMAL"

    if total_demand > 0:
        smcf = min_cost_flow.SimpleMinCostFlow()
        tract_ids = [g for g, t in tract_rows.items() if t["demand_persons"] > 0]
        used_shelters = sorted({str(e["shelter_id"]) for e in usable_edges})
        node = {f"t:{g}": i for i, g in enumerate(tract_ids)}
        node.update({f"s:{s}": len(node) + i for i, s in enumerate(used_shelters)})
        sink = len(node)

        edge_arcs: list[tuple[int, Mapping[str, Any]]] = []
        for e in usable_edges:
            gid, sid = str(e["geo_id"]), str(e["shelter_id"])
            if f"t:{gid}" not in node:
                continue
            arc = smcf.add_arc_with_capacity_and_unit_cost(
                node[f"t:{gid}"], node[f"s:{sid}"], tract_rows[gid]["demand_persons"],
                int(round(float(e["straight_line_km"]) * KM_COST_SCALE)))
            edge_arcs.append((arc, e))
        for sid in used_shelters:
            smcf.add_arc_with_capacity_and_unit_cost(node[f"s:{sid}"], sink, int(shelters[sid]["capacity"]), 0)
        spill_arcs: dict[str, int] = {}
        for gid in tract_ids:
            spill_arcs[gid] = smcf.add_arc_with_capacity_and_unit_cost(
                node[f"t:{gid}"], sink, tract_rows[gid]["demand_persons"], UNASSIGNED_COST_PER_PERSON)
            smcf.set_node_supply(node[f"t:{gid}"], tract_rows[gid]["demand_persons"])
        smcf.set_node_supply(sink, -total_demand)

        result = smcf.solve()
        if result != smcf.OPTIMAL:
            status = "SOLVER_ERROR"
        else:
            for arc, e in edge_arcs:
                flow = smcf.flow(arc)
                if flow <= 0:
                    continue
                gid, sid = str(e["geo_id"]), str(e["shelter_id"])
                km = float(e["straight_line_km"])
                t, s = tract_rows[gid], shelters[sid]
                t["assigned_persons"] += flow
                s["assigned_persons"] += flow
                assignments.append({
                    "geo_id": gid, "shelter_id": sid, "persons": int(flow),
                    "straight_line_km": round(km, 2), "drive_minutes": None, "distance_basis": STRAIGHT_LINE,
                    "mode": "WALK" if km <= WALK_KM else "PARATRANSIT",
                    "from": {"lat": t["lat"], "lng": t["lng"]}, "to": {"lat": s["lat"], "lng": s["lng"]},
                })
            reachable = {str(e["geo_id"]) for e in usable_edges}
            for gid, arc in spill_arcs.items():
                left = smcf.flow(arc)
                if left <= 0:
                    continue
                t = tract_rows[gid]
                t["unassigned_persons"] = int(left)
                unassigned.append({
                    "geo_id": gid, "persons": int(left), "lat": t["lat"], "lng": t["lng"],
                    "reason": "CAPACITY_EXHAUSTED" if gid in reachable else "NO_SHELTER_WITHIN_MAX_KM",
                })

    for s in shelters.values():
        cap = s["capacity"] if s["excluded_reason"] is None else None
        s["utilization"] = round(s["assigned_persons"] / cap, 4) if cap else None

    gaps = _gaps(tract_rows, shelters, unassigned, assignments, max_km)
    if unassigned:
        total_un = sum(u["persons"] for u in unassigned)
        gaps.insert(0, _gap(
            "UNASSIGNED_DEMAND", f"County {county_fips}" if county_fips else "County",
            f"{total_un} of {total_demand} people in no-vehicle households across {len(unassigned)} tracts have no "
            f"pre-landfall seat within {max_km:.0f} km straight-line, even with every recorded seat filled nearest-first.",
            "Designate or retrofit closer sites (see site_options), or contract buses to shelters further out.",
            RECORDED, "HIGH", {"kind": "county", "id": county_fips or "", "lat": None, "lng": None},
            persons_affected=total_un))
    assignments.sort(key=lambda a: (a["geo_id"], -a["persons"]))
    unassigned.sort(key=lambda u: -u["persons"])
    usable_capacity = sum(s["capacity"] for s in shelters.values() if s["excluded_reason"] is None)

    return payload("allocation", {
        "county_fips": county_fips,
        "generated_at": generated_at or _now(),
        "solver": "ortools.SimpleMinCostFlow",
        "status": status,
        "params": {
            "max_km": max_km,
            "shelter_seeking_share": shelter_seeking_share,
            "shelters_down": sorted(down),
            "excluded_shelters": [
                {"shelter_id": s["shelter_id"], "name": s["name"], "reason": s["excluded_reason"]}
                for s in shelters.values() if s["excluded_reason"]
            ],
            "exclude_surge": exclude_surge,
            "capacity_rule": "FEMA evacuation_capacity as filed; unrecorded or implausible capacity excluded, never assumed",
            "demand_rule": "households_no_vehicle x persons per household (group quarters excluded) x shelter_seeking_share",
            "cost_rule": "straight-line person-km; recorded accessibility is not rewarded",
            "distance_basis": STRAIGHT_LINE,
        },
        "totals": {
            "demand_persons": total_demand,
            "assigned_persons": sum(a["persons"] for a in assignments),
            "unassigned_persons": sum(u["persons"] for u in unassigned),
            "usable_capacity": usable_capacity,
            "shelters_used": sum(1 for s in shelters.values() if s["assigned_persons"] > 0),
        },
        "tracts": sorted(tract_rows.values(), key=lambda t: -t["demand_persons"]),
        "shelters": sorted(shelters.values(), key=lambda s: -s["assigned_persons"]),
        "assignments": assignments,
        "unassigned": unassigned,
        "gaps": gaps,
        "could_not_tell_you": _could_not_tell(shelters),
        "site_options": site_options(unassigned, max_km),
        "maps_used": False,
    })


def _km(a: Mapping[str, Any], b: Mapping[str, Any]) -> Optional[float]:
    import math

    if None in (a.get("lat"), a.get("lng"), b.get("lat"), b.get("lng")):
        return None
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def site_options(unassigned: list[Mapping[str, Any]], max_km: float, n: int = SITE_OPTIONS) -> list[dict[str, Any]]:
    """Pure, greedy: the n unplaced-tract centroids that would each seat the most remaining unplaced people."""
    left = {u["geo_id"]: dict(u) for u in unassigned if u["persons"] > 0}
    out: list[dict[str, Any]] = []
    while left and len(out) < n:
        best = None
        for c in left.values():
            covered = [u for u in left.values() if (d := _km(c, u)) is not None and d <= max_km]
            persons = sum(u["persons"] for u in covered)
            if best is None or persons > best[1]:
                best = (c, persons, covered)
        if best is None or best[1] <= 0:
            break
        c, persons, covered = best
        out.append({
            "type": "DESIGNATE_SITE_NEAR",
            "subject": f"Near tract {c['geo_id']}",
            "evidence": f"{persons} people in {len(covered)} unplaced tracts live within {max_km:.0f} km of this point.",
            "action": f"Designate or retrofit a pre-landfall site for about {persons} people here (a public school or "
                      "civic building outside the surge area), then verify it with Google Maps and an in-person visit.",
            "label": RECORDED, "severity": "HIGH",
            "ref": {"kind": "tract", "id": c["geo_id"], "lat": c["lat"], "lng": c["lng"]},
            "persons_covered": persons, "seats_needed": persons,
            "tracts_covered": sorted(u["geo_id"] for u in covered),
        })
        for u in covered:
            left.pop(u["geo_id"], None)
    return out


def _gaps(
    tracts: Mapping[str, Mapping[str, Any]],
    shelters: Mapping[str, Mapping[str, Any]],
    unassigned: list[Mapping[str, Any]],
    assignments: list[Mapping[str, Any]],
    max_km: float,
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for u in sorted(unassigned, key=lambda u: -u["persons"]):
        t = tracts[u["geo_id"]]
        pph = t["persons_per_household"] or 0
        hh = int(round(u["persons"] / pph)) if pph else None
        why = (f"no pre-landfall shelter with recorded capacity within {max_km:.0f} km straight-line"
               if u["reason"] == "NO_SHELTER_WITHIN_MAX_KM" else
               f"every pre-landfall shelter within {max_km:.0f} km is full")
        gaps.append(_gap(
            "UNASSIGNED_DEMAND", f"Tract {u['geo_id']}",
            f"{u['persons']} people in no-vehicle households have no seat: {why}.",
            "Designate or retrofit a closer site, or pre-arrange transport to a shelter further out.",
            RECORDED, "HIGH", {"kind": "tract", "id": u["geo_id"], "lat": t["lat"], "lng": t["lng"]},
            households_affected=hh, persons_affected=u["persons"]))

    sent: dict[str, int] = defaultdict(int)
    from_tracts: dict[str, set[str]] = defaultdict(set)
    for a in assignments:
        sent[a["shelter_id"]] += a["persons"]
        from_tracts[a["shelter_id"]].add(a["geo_id"])
    for sid, persons in sorted(sent.items(), key=lambda kv: -kv[1]):
        s = shelters[sid]
        ref = {"kind": "shelter", "id": sid, "lat": s["lat"], "lng": s["lng"]}
        subject = f"{s['name']} ({sid})"
        if s["surge"] == YES:
            gaps.append(_gap(
                "ASSIGNED_SHELTER_IN_SURGE_ZONE", subject,
                f"The plan sends {persons} people here, and the FEMA record puts it inside the SLOSH surge area.",
                "Confirm elevation and hardening, or move it to post-impact use and re-plan.",
                RECORDED, "HIGH", ref, persons_affected=persons))
        if s["wheelchair"] != YES:
            label = UNRECORDED if s["wheelchair"] == UNRECORDED else RECORDED
            state = "nobody has recorded" if label == UNRECORDED else "FEMA records NO for"
            gaps.append(_gap(
                "ASSESS_ASSIGNED_SHELTER", subject,
                f"The plan sends {persons} people from {len(from_tracts[sid])} tracts here; {state} wheelchair access.",
                "Ask the operator: can a wheelchair user get in unaccompanied, today? Record the answer.",
                label, "MEDIUM", ref, persons_affected=persons))

    for s in shelters.values():
        if s["excluded_reason"] in ("CAPACITY_UNRECORDED", "CAPACITY_IMPLAUSIBLE"):
            filed = "no capacity filed" if s["excluded_reason"] == "CAPACITY_UNRECORDED" else f"capacity {s['capacity']} filed"
            gaps.append(_gap(
                f"{s['excluded_reason']}_EXCLUDED", f"{s['name']} ({s['shelter_id']})",
                f"Pre-landfall shelter with {filed}; left out of the plan rather than guessed.",
                "Get a capacity figure from the operator and correct the FEMA record.",
                UNRECORDED if s["excluded_reason"] == "CAPACITY_UNRECORDED" else RECORDED, "LOW",
                {"kind": "shelter", "id": s["shelter_id"], "lat": s["lat"], "lng": s["lng"]}))
    return gaps


def _could_not_tell(shelters: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    cand = [s for s in shelters.values() if s["is_pre_landfall"]]
    wc_unrec = sum(1 for s in cand if s["wheelchair"] == UNRECORDED)
    cap_unrec = sum(1 for s in cand if s["capacity_status"] != "RECORDED")
    return [
        {"item": "Capacity of candidate shelters", "spoken_to": len(cand) - cap_unrec, "unassessed": cap_unrec,
         "note": f"{cap_unrec} candidate shelters have no capacity on file and are not in the plan.", "label": UNRECORDED},
        {"item": "Wheelchair access at candidate shelters", "spoken_to": len(cand) - wc_unrec, "unassessed": wc_unrec,
         "note": f"{len(cand) - wc_unrec} I can speak to; {wc_unrec} nobody has assessed.", "label": UNRECORDED},
        {"item": "Who actually needs a seat", "label": RECORDED,
         "note": "Demand counts everyone in a no-vehicle household (ACS vehicle ownership). Owning no car is not the "
                 "same as having no ride, so this is an upper bound."},
        {"item": "Travel time", "label": RECORDED,
         "note": "Assignments use straight-line distance, a lower bound on road distance. Drive times come from "
                 "Google Maps only for pairs checked with verify_shelters."},
    ]


_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def compact(alloc: Mapping[str, Any], route: str = "/api/allocate", body: Optional[Mapping[str, Any]] = None,
            gaps: int = COMPACT_GAPS) -> dict[str, Any]:
    """Pure: the LLM-sized view of allocation.v1. Same payload_type; layers dropped, counts kept."""
    out = {k: v for k, v in alloc.items() if k not in ("tracts", "shelters", "assignments", "gaps")}
    ranked = sorted(alloc.get("gaps", []), key=lambda g: (_SEVERITY_ORDER.get(g.get("severity"), 3),
                                                          -(g.get("persons_affected") or 0)))
    excluded = alloc.get("params", {}).get("excluded_shelters", [])
    out.update(
        compact=True,
        full={"method": "POST", "path": route, "body": dict(body or {"county_fips": alloc.get("county_fips")})},
        gaps=ranked[:gaps],
        gaps_total=len(ranked),
        gap_counts={t: sum(1 for g in ranked if g["type"] == t) for t in sorted({g["type"] for g in ranked})},
        tracts=[], shelters=[], assignments=[],
        excluded_counts={r: sum(1 for e in excluded if e["reason"] == r) for r in sorted({e["reason"] for e in excluded})},
    )
    return out


def ops_table(cfg: AgentConfig, view: str) -> str:
    """Backtick-quoted `project.<cfg.ops_dataset>.view`; the dataset comes from config (OPS_DATASET)."""
    if not _IDENT_RE.match(view):
        raise ValueError(f"Bad identifier {view!r}")
    prefix = f"{cfg.project_id}.{cfg.ops_dataset}" if cfg.project_id else cfg.ops_dataset
    return f"`{prefix}.{view}`"


def fetch_inputs(cfg: AgentConfig, county_fips: str, max_km: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One query per readiness_ops view, filtered to the county's tracts."""
    from .bq import render, run

    tracts = run(render(TRACT_NEED_SQL, tract_need=ops_table(cfg, "v_tract_need")), {"county_fips": county_fips}, cfg)
    edges = run(
        render(CANDIDATES_SQL, candidates=ops_table(cfg, "v_tract_shelter_candidates")),
        {"county_fips": county_fips, "max_km": float(max_km)},
        cfg,
    )
    return tracts, edges


async def allocate_county(
    county_fips: str,
    shelters_down: Optional[list[str]] = None,
    max_km: Optional[float] = None,
    shelter_seeking_share: Optional[float] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Assign the residents of no-vehicle households in a county to pre-landfall shelters within capacity.

    Use this for "where would people without a car go, does the capacity hold, and who is left
    over". Returns a compact summary (the map loads the full layers itself). Solves a min-cost flow on straight-line distance (no Google Maps call), within max_km
    (default 15 km, the 30-minute para-transit proxy). Shelters can be in any county. shelters_down
    takes shelter ids to treat as unavailable. Returns every assignment, unplaced demand by tract,
    shelter load, and gaps: unassigned demand, assigned shelters in the surge zone, and assigned
    shelters whose wheelchair access nobody recorded (ranked by people sent there).
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    km = float(max_km) if max_km else PARATRANSIT_PROXY_KM
    share = float(shelter_seeking_share) if shelter_seeking_share else SHELTER_SEEKING_SHARE
    tracts, edges = await asyncio.to_thread(fetch_inputs, cfg, fips, km)
    effective_down = list(shelters_down or [])
    if tool_context is not None and getattr(tool_context, "state", None) is not None:
        for sid in tool_context.state.get("dropped_shelter_ids") or []:
            if str(sid) not in effective_down:
                effective_down.append(str(sid))
    out = solve(
        tracts,
        edges,
        shelters_down=effective_down or None,
        max_km=km,
        shelter_seeking_share=share,
        county_fips=fips,
    )
    if tool_context is None:  # REST caller (the map): full layers
        return out
    tool_context.state["last_allocation_totals"] = out["totals"]
    return compact(out, body={"county_fips": fips, "shelters_down": effective_down or shelters_down, "max_km": km,
                              "shelter_seeking_share": share})
