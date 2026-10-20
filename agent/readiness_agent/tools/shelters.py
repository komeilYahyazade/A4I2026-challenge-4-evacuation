"""find_candidate_shelters: where people would go, with recorded flags in three states."""

from __future__ import annotations

import asyncio

from google.adk.tools import ToolContext

from readiness_agent import config, shapes
from readiness_agent.tools import bq, facts


def summarise(rows: list[dict]) -> dict:
    within = [r for r in rows if r["within_reach"]]
    return {"within_reach": len(within), "wheelchair": shapes.flag_counts(within),
            "evac_within_reach": sum(1 for r in within if r["usage"] == "EVAC")}


REQUIRE = ("any", "medical", "generator", "medical_or_generator")


async def find_candidate_shelters(tool_context: ToolContext, geo_id: str | None = None,
                                  county_fips: str | None = None,
                                  reachable_km: float = config.REACHABLE_KM_NO_VEHICLE,
                                  usage: str | None = None, ccn: str | None = None,
                                  require: str = "any") -> dict:
    """Find candidate shelters for one census tract or one nursing home (nearest first, by
    straight-line km, any county), or list a county's shelters. Recorded flags are three-state:
    yes, no or unrecorded. Unrecorded is not no.

    Args:
        geo_id: 11-digit tract id. Give this, OR ccn, OR county_fips.
        county_fips: 5-digit county FIPS, to list the county's shelters instead.
        ccn: a nursing home's CMS certification number, to search from that facility.
        reachable_km: what counts as reachable without a car, straight line (default 3.0).
        usage: optional FEMA usage code filter: "EVAC" (before landfall) or "POST" (after impact).
        require: "any" (default), "medical" (medical-needs shelters), "generator" (generator
            recorded yes), or "medical_or_generator".

    Returns:
        shelters (with straight_line_km and within_reach), and counts within reach by wheelchair
        state, e.g. {"yes": 0, "no": 1, "unrecorded": 4}.
    """
    if not geo_id and not county_fips and not ccn:
        return {"status": "error", "message": "Give geo_id, ccn or county_fips."}
    require = require if require in REQUIRE else "any"
    origin = geo_id or ccn
    limit = 20 if origin else 50
    rows = await asyncio.to_thread(bq.run, "03_candidate_shelters.sql", geo_id=geo_id, ccn=None if geo_id else ccn,
                                   county_fips=None if origin else county_fips, reachable_km=float(reachable_km),
                                   usage=usage, limit=limit, require=require)
    counts = summarise(rows)
    key = geo_id or (f"ccn:{ccn}" if ccn else f"county:{county_fips}")
    facts.put(tool_context, "candidates", key, {
        "geo_id": geo_id, "ccn": ccn, "county_fips": county_fips, "reachable_km": float(reachable_km),
        "usage": usage, "require": require, "counts_within_reach": counts, "shelters": rows})
    return {
        "status": "ok" if rows else "none_found",
        "source": "fema_nss",
        "freshness": "historical",
        "reachable_km": float(reachable_km),
        "require": require,
        "counts_within_reach": counts,
        "caveats": ["straight_line_km is straight-line distance, not a drive time.",
                    "Flags are FEMA records (historical). 'unrecorded' means nobody filled it in."],
        "shelters": [{k: r[k] for k in (
            "shelter_id", "name", "address", "usage", "straight_line_km", "within_reach",
            "evacuation_capacity", "wheelchair_accessible", "generator_onsite", "in_surge_slosh_area",
            "in_100_yr_floodplain", "is_medical", "place_id")} for r in rows],
    }
