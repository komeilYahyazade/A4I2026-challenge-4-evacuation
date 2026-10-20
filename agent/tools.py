"""Conversational tools. Each answers one of the manager's questions with labelled evidence.

The heavy lifting (verification, the full brief) lives in verify.py and brief.py.
"""

import asyncio
from typing import Any, Optional

from google.adk.tools import ToolContext

from . import data
from .brief import _shelter_summary, tract_reasons
from .config import AgentConfig, county_fips_or_error, tract_geo_id_or_error
from .evidence import COUNTY_LEVEL, RECORDED, UNRECORDED, payload
from .reachability import assess, assumptions


async def find_county(name: str, tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """Look up a county in the loaded state by name and return its 5-digit FIPS code.

    Call this first when the manager names a county ("Lee County"). Every other tool takes the
    FIPS code. Also points live Google Maps lookups at that county.
    """
    cfg = AgentConfig.from_env()
    rows = await asyncio.to_thread(data.find_county, cfg, name)
    if rows and tool_context is not None:
        exact = [r for r in rows if r["county_name"].lower() == name.lower().replace(" county", "").strip()]
        best = (exact or rows)[0]
        tool_context.state["county_fips"] = best["county_fips"]
        tool_context.state["county_centroid"] = {"lat": best["lat"], "lng": best["lng"]}
    return payload("county_lookup", {"state": cfg.state, "matches": rows})


async def county_overview(county_fips: str) -> dict[str, Any]:
    """Shelter capacity and record quality for one county, plus power dependence.

    Answers "how many shelters do I have", "how many take a wheelchair", "how many people here
    depend on electricity for medical equipment". Every flag is split into yes, no and
    unrecorded; unrecorded is never folded into no. Capacity totals exclude implausible records.
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    shelters, power = await asyncio.gather(
        asyncio.to_thread(data.county_shelters, cfg, fips),
        asyncio.to_thread(data.power_dependence, cfg, fips),
    )
    summary = _shelter_summary(shelters)
    wc = summary["pre_landfall_wheelchair"]
    return payload("county_overview", {
        "county_fips": fips,
        "shelters": {**summary, "label": RECORDED, "source": "FEMA National Shelter System"},
        "headline": f"Wheelchair access at pre-landfall shelters: {wc['yes'] + wc['no']} I can speak to; "
                    f"{wc['unrecorded']} nobody has assessed.",
        "power_dependence": None if power is None else {
            **power, "label": COUNTY_LEVEL,
            "note": "County-level only. A value of 11 means 1 to 10 (HHS suppresses small counts).",
        },
        "implausible_capacity_rows": [
            {"shelter_id": s["shelter_id"], "name": s["shelter_name"], "evacuation_capacity": s["evacuation_capacity"]}
            for s in shelters if s.get("capacity_implausible")
        ],
    })


async def rank_tracts(county_fips: str, limit: int = 10, ranking: str = "no_vehicle") -> dict[str, Any]:
    """Rank the census tracts in a county by evacuation need.

    ranking="no_vehicle" (default): who cannot leave. Tracts with the most households without a
    vehicle. ranking="may_need_help": who must leave and may need help. Most mobile homes, residents
    65+ and residents with a disability, by count. ranking="composite_v0": the original ranking,
    kept only to reproduce the before-and-after of the equity audit; do not use it to plan.
    Inputs are physical and demographic only. Each tract comes with reasons and evidence labels,
    the nearest pre-landfall shelter, and how many shelters within a 30-minute run are recorded
    wheelchair accessible, recorded not accessible, or never assessed.
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    rows = await asyncio.to_thread(data.ranked_tracts, cfg, fips, limit, ranking)
    out = []
    for rank, t in enumerate(rows, start=1):
        out.append({
            "rank": rank,
            **t,
            "reasons": tract_reasons(t, focus="may_need_help" if ranking == "may_need_help" else "no_vehicle"),
            "mobility_to_nearest_pre_landfall": assess(
                t.get("km_pre_landfall_shelter"), t.get("households_no_vehicle"), t.get("households")),
        })
    return payload("tract_ranking", {"county_fips": fips, "ranking": ranking, "assumptions": assumptions(),
                                     "tracts": out})


async def shelters_for_tract(tract_geo_id: str, limit: int = 10) -> dict[str, Any]:
    """Where people in one census tract would go: nearest pre-landfall shelters with full records.

    Returns straight-line distance (a lower bound on road distance), capacity, and wheelchair,
    generator and surge status as YES, NO or UNRECORDED. Use verify_shelters on the ones you
    intend to recommend to confirm they exist and to get routed drive times.
    """
    cfg = AgentConfig.from_env()
    gid = tract_geo_id_or_error(tract_geo_id, cfg)
    rows, origin = await asyncio.gather(
        asyncio.to_thread(data.shelters_for_tract, cfg, gid, limit),
        asyncio.to_thread(data.tract_origin, cfg, gid),
    )
    wc = [r["wheelchair_status"] for r in rows]
    return payload("tract_destinations", {
        "tract": gid,
        "shelters": rows,
        "wheelchair": {
            "recorded_yes": wc.count("YES"), "recorded_no": wc.count("NO"), "unrecorded": wc.count("UNRECORDED"),
            "summary": f"{wc.count('YES') + wc.count('NO')} I can speak to; {wc.count('UNRECORDED')} nobody has assessed.",
            "label": UNRECORDED if wc.count("UNRECORDED") else RECORDED,
        },
        "mobility_to_nearest": assess(
            rows[0]["straight_line_km"] if rows else None,
            (origin or {}).get("households_no_vehicle"), (origin or {}).get("households")),
    })


async def lookup_shelter(name: str, county_fips: str = "") -> dict[str, Any]:
    """Find shelters in the FEMA registry by (part of) their name, optionally within one county.

    Use this to get the shelter_id when the manager asks about a specific building, then call
    verify_shelters with that id to check it against Google Maps.
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg) if county_fips else ""
    rows = await asyncio.to_thread(data.lookup_shelter, cfg, name, fips)
    return payload("shelter_lookup", {"matches": rows, "label": RECORDED})


async def care_facility_gaps(county_fips: str) -> dict[str, Any]:
    """Nursing homes in a county and how far each is from a pre-landfall shelter recorded with a generator.

    Answers the medically dependent questions: where the concentrations of people who cannot
    self-evacuate are, and where a generator or transfer agreement should exist before September.
    Pairs with county-level power dependence from HHS emPOWER.
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    care, power = await asyncio.gather(
        asyncio.to_thread(data.care_facilities, cfg, fips),
        asyncio.to_thread(data.power_dependence, cfg, fips),
    )
    return payload("care_facility_gaps", {
        "county_fips": fips,
        "nursing_homes": care,
        "label": RECORDED,
        "source": "CMS Nursing Home Provider Information; FEMA NSS generator_onsite",
        "note": "Distance is to shelters where a generator is recorded. Unrecorded generators are not counted as present.",
        "power_dependence": None if power is None else {**power, "label": COUNTY_LEVEL},
        "not_covered": "Assisted living facilities: no national public roster exists.",
    })


def coverage(ranked: list[dict[str, Any]], totals: dict[str, Any]) -> dict[str, Any]:
    """How much of the county's need the recommended tracts hold."""
    out: dict[str, Any] = {}
    for key in ("households_no_vehicle", "mobile_homes", "pop_65_plus"):
        covered = sum(float(r.get(key) or 0) for r in ranked)
        total = float(totals.get(key) or 0)
        out[key] = {"covered": int(round(covered)), "county_total": int(round(total)),
                    "share": round(covered / total, 4) if total else None}
    return out


async def audit_equity(county_fips: str, top_tracts: int = 15, ranking: str = "no_vehicle") -> dict[str, Any]:
    """Audit who the recommendations land on, after the fact.

    Takes the top-ranked tracts and compares their racial, ethnic and poverty composition with
    the county and the state, population-weighted, from the public Census ACS. Race is never a
    ranking input; it is used here only to check the output. Also reports coverage (what share of
    the county's car-less households, mobile homes and older residents the list reaches) and
    reporting bias (whether nearby shelters have their wheelchair field filled in less often than
    shelters statewide). Call again with ranking="composite_v0" to show the ranking before the
    audit changed it. Report the result either way, including "no difference".
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    ranked, totals = await asyncio.gather(
        asyncio.to_thread(data.ranked_tracts, cfg, fips, max(5, min(int(top_tracts), 100)), ranking),
        asyncio.to_thread(data.county_totals, cfg, fips),
    )
    ids = [r["geo_id"] for r in ranked]
    result = await asyncio.to_thread(data.equity_audit, cfg, fips, ids)
    rep = {r["cohort"]: r for r in result["reporting"]}
    for r in rep.values():
        r["recorded_share"] = round(r["wheelchair_recorded"] / r["shelters"], 4) if r["shelters"] else None
    near, state = rep.get("near_recommended", {}), rep.get("state", {})
    gap = None
    if near.get("recorded_share") is not None and state.get("recorded_share") is not None:
        gap = round(near["recorded_share"] - state["recorded_share"], 4)
    return payload("equity_audit", {
        "county_fips": fips,
        "ranking": ranking,
        "recommended_tracts": ids,
        "coverage": coverage(ranked, totals),
        **result,
        "reporting_gap_vs_state": gap,
        "how_to_read": (
            "demographics: population-weighted shares for the recommended tracts, the county and the state. "
            "reporting: share of nearby pre-landfall shelters whose wheelchair field is filled in. A negative "
            "reporting gap means the people we prioritize are served by worse paperwork, so any ranking built on "
            "recorded accessibility would have pushed help away from them."
        ),
    })
