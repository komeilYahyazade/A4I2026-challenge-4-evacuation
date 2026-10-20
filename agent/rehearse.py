"""Tabletop rehearsal: replay Hurricane Ian against the plan, take shelters offline, re-solve.

This is an August exercise, not response. It runs on the filed plan and recorded data only and
never calls Google Maps grounding: Google's terms exclude emergency-response use, and a rehearsal
has to behave the way the real activation would, from reported shelter status alone.

Which shelters go offline, in this order:
1. shelters_down passed explicitly (reason MANUAL).
2. The data lane's v_ian_shelters_down: Ian's forecast cone plus FEMA NRI coastal-flood exposure
   at the shelter's nearest tract centroid. Approximate; each row says which rule picked it.
3. Fallback: the candidate shelters FEMA records inside the SLOSH surge area (IN_SURGE_ZONE).
   In Lee that flag is blank for 81 of 84 pre-landfall shelters, which is why it is the fallback
   and not the rule.

DEFAULT_ADVISORY = "19"
    NHC advisory 19, issued 22 hours before landfall, is the first whose cone covered Fort Myers
    (Tampa was inside every cone before it). Earlier advisories drop few Lee shelters, which is
    the point the drill makes: decisions that take days cannot wait for the cone.

DEFAULT_SHELTERS_DOWN = 2
    Two shelters lost is the planning case the tabletop starts from: enough to move people, few
    enough that the manager can follow every reassignment on one screen.
"""

import asyncio
import datetime
import json
from collections import defaultdict
from typing import Any, Mapping, Optional

from .allocate import SHELTER_SEEKING_SHARE, compact, fetch_inputs, ops_table, solve
from .config import AgentConfig, county_fips_or_error
from .evidence import YES, payload, three_state
from .reachability import PARATRANSIT_PROXY_KM

try:
    from google.adk.tools import ToolContext
except ImportError:  # pragma: no cover
    ToolContext = Any  # type: ignore[misc,assignment]

DEFAULT_ADVISORY = "19"
DEFAULT_SHELTERS_DOWN = 2

SCENARIOS: dict[str, dict[str, Any]] = {
    "ian_2022": {
        "id": "ian_2022",
        "name": "Hurricane Ian (2022) tabletop replay",
        "storm_id": "AL092022",
        "landfall_at": "2022-09-28T19:05:00Z",
        "landfall": {"lat": 26.7, "lng": -82.2},
        "source": "NOAA NHC GIS archive (public domain); landfall per NHC Tropical Cyclone Report AL092022",
    }
}

NOTE = "Tabletop rehearsal: no Google Maps grounding is used in response-mode planning."

DOWN_SQL = """
SELECT shelter_id, shelter_name, county_fips, lat, lng, reason, evidence, advisory, down_rank,
       capacity_status, evacuation_capacity
FROM ${down}
WHERE advisory = @advisory
ORDER BY down_rank
"""

STORM_SQL = """
SELECT storm_id, advisory, issued_at, hours_before_landfall,
       ST_ASGEOJSON(ST_SIMPLIFY(cone, 2000)) AS cone_geojson, track_json
FROM ${storms}
WHERE storm_id = @storm_id
ORDER BY issued_at
"""

BEST_TRACK_SQL = """
SELECT valid_at, lat, lng, wind_kt
FROM ${best}
WHERE storm_id = @storm_id
ORDER BY valid_at
"""


def _geom(value: Any) -> Any:
    if value is None or isinstance(value, dict):
        return value
    try:
        obj = json.loads(value)
    except (TypeError, ValueError):
        return None
    return obj.get("geometry", obj) if isinstance(obj, dict) and obj.get("type") == "Feature" else obj


def _track(value: Any) -> list[dict[str, Any]]:
    obj = _geom(value)
    if isinstance(obj, list):
        return [p for p in obj if isinstance(p, dict)]
    if isinstance(obj, dict) and obj.get("type") == "LineString":
        return [{"lat": c[1], "lng": c[0], "valid_at": None, "wind_kt": None} for c in obj.get("coordinates", [])]
    if isinstance(obj, dict) and obj.get("type") == "FeatureCollection":
        pts = []
        for f in obj.get("features", []):
            g, p = f.get("geometry") or {}, f.get("properties") or {}
            if g.get("type") == "Point":
                pts.append({"lat": g["coordinates"][1], "lng": g["coordinates"][0],
                            "valid_at": p.get("valid_at"), "wind_kt": p.get("wind_kt")})
        return pts
    return []


def _in_plan(e: Mapping[str, Any]) -> bool:
    status = e.get("capacity_status") or ("RECORDED" if e.get("evacuation_capacity") else "UNRECORDED")
    return status == "RECORDED" and bool(e.get("evacuation_capacity")) and e.get("is_pre_landfall") is not False


def pick_shelters_down(
    edges: list[Mapping[str, Any]],
    down_rows: Optional[list[Mapping[str, Any]]],
    explicit: Optional[list[str]] = None,
    n: int = DEFAULT_SHELTERS_DOWN,
) -> list[dict[str, Any]]:
    """Pure: choose the shelters a rehearsal takes offline, each with the rule that picked it.

    At-risk shelters the plan never counted on (no recorded capacity) are returned too, with
    in_plan false: losing them moves nobody, which is itself a finding. The n shelters taken
    offline to drive the re-solve are the highest-ranked ones the plan actually uses.
    """
    known: dict[str, Mapping[str, Any]] = {}
    for e in edges:
        known.setdefault(str(e["shelter_id"]), e)

    def row(sid: str, reason: str, rule: str, base: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        e = known.get(sid, {})
        b = base or {}
        return {
            "shelter_id": sid,
            "name": b.get("shelter_name") or e.get("shelter_name"),
            "lat": b.get("lat", e.get("shelter_lat")),
            "lng": b.get("lng", e.get("shelter_lng")),
            "reason": reason,
            "rule": rule,
            "in_plan": _in_plan(e) if e else False,
        }

    if explicit:
        return [row(str(s), "MANUAL", "Named by the exercise lead") for s in explicit]
    candidates: list[dict[str, Any]] = []
    for d in sorted(down_rows or [], key=lambda d: d.get("down_rank") or 0):
        sid = str(d["shelter_id"])
        if sid in known:
            reason = d.get("reason") or "IN_CONE_AND_SURGE"
            rule = {
                "IN_SURGE_ZONE": "FEMA in_surge_slosh_area = YES (recorded)",
                "COASTAL_FLOOD_TRACT": "Nearest tract has at least 50% of residents coastal-flood exposed (FEMA NRI; approximate, no cone)",
            }.get(reason, "Inside the Ian cone, and the nearest tract has at least 50% coastal-flood exposed (FEMA NRI; approximate)")
            candidates.append(row(sid, reason, d.get("evidence") or rule, d))
    if down_rows is None:  # hazard layer not loaded: the recorded surge flag is all we have
        for sid, e in known.items():
            if three_state(e.get("surge_status")) == YES and e.get("is_pre_landfall") is not False:
                candidates.append(row(sid, "IN_SURGE_ZONE", "FEMA in_surge_slosh_area = YES (recorded)"))
    seen: set[str] = set()
    picked: list[dict[str, Any]] = []
    for c in candidates:
        if sum(1 for p in picked if p["in_plan"]) >= n:
            break  # only at-risk shelters ranked above the last one taken offline are listed
        if c["shelter_id"] in seen:
            continue
        seen.add(c["shelter_id"])
        picked.append(c)  # in_plan false: listed, but changes nothing in the solve
    return picked


def diff_allocations(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Pair each tract's lost seats with the seats it gained; whatever is left is newly unplaced."""
    tract_at = {t["geo_id"]: {"lat": t["lat"], "lng": t["lng"]} for t in after["tracts"]}
    shelter_at = {s["shelter_id"]: {"lat": s["lat"], "lng": s["lng"]} for s in before["shelters"]}

    def by_tract(alloc: Mapping[str, Any]) -> dict[str, dict[str, int]]:
        m: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for a in alloc["assignments"]:
            m[a["geo_id"]][a["shelter_id"]] += a["persons"]
        return m

    b, a = by_tract(before), by_tract(after)
    reassigned: list[dict[str, Any]] = []
    for gid in sorted(set(b) | set(a)):
        lost = [[sid, b[gid][sid] - a[gid].get(sid, 0)] for sid in b[gid] if b[gid][sid] > a[gid].get(sid, 0)]
        gained = [[sid, a[gid][sid] - b[gid].get(sid, 0)] for sid in a[gid] if a[gid][sid] > b[gid].get(sid, 0)]
        for lo in lost:
            for ga in gained:
                if lo[1] <= 0:
                    break
                if ga[1] <= 0:
                    continue
                moved = min(lo[1], ga[1])
                reassigned.append({
                    "geo_id": gid, "from_shelter_id": lo[0], "to_shelter_id": ga[0], "persons": moved,
                    "from": tract_at.get(gid), "to_old": shelter_at.get(lo[0]), "to_new": shelter_at.get(ga[0]),
                })
                lo[1] -= moved
                ga[1] -= moved
    before_un = {u["geo_id"]: u["persons"] for u in before["unassigned"]}
    newly = [
        {"geo_id": u["geo_id"], "persons": u["persons"] - before_un.get(u["geo_id"], 0), "lat": u["lat"], "lng": u["lng"]}
        for u in after["unassigned"] if u["persons"] > before_un.get(u["geo_id"], 0)
    ]
    return {
        "reassigned": reassigned,
        "newly_unassigned": newly,
        "unassigned_before": before["totals"]["unassigned_persons"],
        "unassigned_after": after["totals"]["unassigned_persons"],
    }


def rehearse_from(
    tracts: list[Mapping[str, Any]],
    edges: list[Mapping[str, Any]],
    down: list[Mapping[str, Any]],
    scenario: Mapping[str, Any],
    *,
    max_km: float = PARATRANSIT_PROXY_KM,
    shelter_seeking_share: float = SHELTER_SEEKING_SHARE,
    county_fips: str = "",
) -> dict[str, Any]:
    """Pure: solve the plan as filed, solve again without the downed shelters, and diff them."""
    before = solve(tracts, edges, max_km=max_km, shelter_seeking_share=shelter_seeking_share, county_fips=county_fips)
    ids = [d["shelter_id"] for d in down]
    not_in_plan = [d for d in down if d.get("in_plan") is False]
    after = solve(tracts, edges, shelters_down=ids, max_km=max_km,
                  shelter_seeking_share=shelter_seeking_share, county_fips=county_fips)
    usable_before = {s["shelter_id"]: s for s in before["shelters"] if s["excluded_reason"] is None}
    diff = diff_allocations(before, after)
    diff["capacity_lost"] = sum(usable_before[i]["capacity"] for i in ids if i in usable_before)
    return payload("rehearsal", {
        "county_fips": county_fips,
        "generated_at": before["generated_at"],
        "scenario": dict(scenario),
        "shelters_down": [dict(d) for d in down],
        "at_risk_not_in_plan": [
            {**dict(d), "note": "At risk, but the plan never counted on it: no capacity on file."} for d in not_in_plan
        ],
        "before": before,
        "after": after,
        "diff": diff,
        "maps_used": False,
        "note": NOTE,
    })


def compact_rehearsal(r: Mapping[str, Any], body: Mapping[str, Any], top: int = 15) -> dict[str, Any]:
    """Pure: the LLM-sized view of rehearsal.v1 (no cone, no full before/after layers)."""
    out = {k: v for k, v in r.items() if k not in ("before", "after", "diff", "scenario")}
    diff = dict(r["diff"])
    moved = sorted(diff.pop("reassigned"), key=lambda x: -x["persons"])
    diff.update(reassigned=moved[:top], reassigned_total=len(moved))
    out.update(
        compact=True,
        full={"method": "POST", "path": "/api/rehearse", "body": dict(body)},
        scenario={k: v for k, v in r["scenario"].items() if k != "cone"},
        before=compact(r["before"], body={"county_fips": r["county_fips"]}, gaps=5),
        after=compact(r["after"], body={"county_fips": r["county_fips"], "shelters_down": [d["shelter_id"] for d in r["shelters_down"]]}, gaps=5),
        diff=diff,
    )
    return out


def fetch_down(cfg: AgentConfig, advisory: str = DEFAULT_ADVISORY) -> Optional[list[dict[str, Any]]]:
    from .bq import render, run

    try:
        return run(render(DOWN_SQL, down=ops_table(cfg, "v_ian_shelters_down")), {"advisory": advisory}, cfg)
    except Exception:  # view not built yet: fall back to the recorded surge flag
        return None


def fetch_storm(cfg: AgentConfig, storm_id: str) -> list[dict[str, Any]]:
    from .bq import render, run

    try:
        return run(render(STORM_SQL, storms=ops_table(cfg, "storm_advisories")), {"storm_id": storm_id}, cfg)
    except Exception:
        return []


def fetch_best_track(cfg: AgentConfig, storm_id: str) -> list[dict[str, Any]]:
    from .bq import render, run

    try:
        return run(render(BEST_TRACK_SQL, best=ops_table(cfg, "storm_best_track")), {"storm_id": storm_id}, cfg)
    except Exception:
        return []


def storm_track_payload(scenario_id: str, rows: list[Mapping[str, Any]],
                        best: Optional[list[Mapping[str, Any]]] = None) -> dict[str, Any]:
    sc = SCENARIOS[scenario_id]
    return payload("storm_track", {
        "storm_id": sc["storm_id"],
        "name": "Ian",
        "landfall_at": sc["landfall_at"],
        "landfall": sc["landfall"],
        "source": sc["source"],
        "advisories": [
            {"advisory": str(r.get("advisory")), "issued_at": r.get("issued_at"),
             "hours_before_landfall": r.get("hours_before_landfall"),
             "cone": _geom(r.get("cone_geojson")), "track": _track(r.get("track_json", r.get("track_geojson")))}
            for r in rows
        ],
        "best_track": [{"lat": b["lat"], "lng": b["lng"], "valid_at": b.get("valid_at"), "wind_kt": b.get("wind_kt")}
                       for b in (best or [])],
    })


def _scenario_meta(scenario_id: str, storm_rows: list[Mapping[str, Any]], advisory: Optional[str]) -> dict[str, Any]:
    sc = dict(SCENARIOS[scenario_id])
    chosen = None
    if storm_rows:
        want = str(advisory or DEFAULT_ADVISORY)
        chosen = next((r for r in storm_rows if str(r.get("advisory")) == want), None) or storm_rows[-1]
    sc.update(
        advisory=None if chosen is None else str(chosen.get("advisory")),
        issued_at=None if chosen is None else chosen.get("issued_at"),
        cone=None if chosen is None else _geom(chosen.get("cone_geojson")),
    )
    return sc


async def rehearse(
    county_fips: str,
    scenario: str = "ian_2022",
    advisory: Optional[str] = None,
    shelters_down: Optional[list[str]] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Run a tabletop rehearsal: replay Hurricane Ian, take shelters offline, and re-solve the plan.

    Use this when the manager asks "what if Ian came again", "what if these shelters flood", or
    wants to drill the plan. Never uses Google Maps. Without shelters_down it takes offline the two
    shelters the Ian cone and coastal-flood exposure put most at risk (or, if that layer is not
    loaded, those FEMA records inside the surge area), says which rule picked each, and returns the
    plan before and after, every reassignment, newly unplaced demand, and the capacity lost.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario {scenario!r}. Known: {sorted(SCENARIOS)}")
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    (tracts, edges), down_rows, storm_rows = await asyncio.gather(
        asyncio.to_thread(fetch_inputs, cfg, fips, PARATRANSIT_PROXY_KM),
        asyncio.to_thread(fetch_down, cfg, str(advisory or DEFAULT_ADVISORY)) if not shelters_down
        else asyncio.sleep(0, None),
        asyncio.to_thread(fetch_storm, cfg, SCENARIOS[scenario]["storm_id"]),
    )
    down = pick_shelters_down(edges, down_rows, shelters_down)
    out = rehearse_from(tracts, edges, down, _scenario_meta(scenario, storm_rows, advisory), county_fips=fips)
    if tool_context is None:  # REST caller (the map): full before/after layers and the cone
        return out
    tool_context.state["last_rehearsal_diff"] = {k: v for k, v in out["diff"].items() if not isinstance(v, list)}
    return compact_rehearsal(out, {"county_fips": fips, "scenario": scenario, "advisory": advisory,
                                   "shelters_down": shelters_down})


async def storm_track(scenario: str = "ian_2022") -> dict[str, Any]:
    cfg = AgentConfig.from_env()
    sid = SCENARIOS[scenario]["storm_id"]
    rows, best = await asyncio.gather(asyncio.to_thread(fetch_storm, cfg, sid),
                                      asyncio.to_thread(fetch_best_track, cfg, sid))
    return storm_track_payload(scenario, rows, best)
