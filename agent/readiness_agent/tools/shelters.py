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


async def find_candidate_shelters(tool_context: ToolContext, geo_id: str | None = None,
                                  county_fips: str | None = None,
                                  reachable_km: float = config.REACHABLE_KM_NO_VEHICLE,
                                  usage: str | None = None) -> dict:
    """Find candidate shelters for one census tract (nearest first, by straight-line km from the
    tract centroid, any county) or list a county's shelters. Recorded flags are three-state:
    yes, no or unrecorded. Unrecorded is not no.

    Args:
        geo_id: 11-digit tract id. Give this OR county_fips.
        county_fips: 5-digit county FIPS, to list the county's shelters instead.
        reachable_km: what counts as reachable without a car, straight line (default 3.0).
        usage: optional FEMA usage code filter: "EVAC" (before landfall) or "POST" (after impact).

    Returns:
        shelters (with straight_line_km and within_reach), and counts within reach by wheelchair
        state, e.g. {"yes": 0, "no": 1, "unrecorded": 4}.
    """
    if not geo_id and not county_fips:
        return {"status": "error", "message": "Give geo_id or county_fips."}
    limit = 20 if geo_id else 50
    rows = await asyncio.to_thread(bq.run, "03_candidate_shelters.sql", geo_id=geo_id,
                                   county_fips=None if geo_id else county_fips,
                                   reachable_km=float(reachable_km), usage=usage, limit=limit)
    counts = summarise(rows)
    key = geo_id or f"county:{county_fips}"
    facts.put(tool_context, "candidates", key, {
        "geo_id": geo_id, "county_fips": county_fips, "reachable_km": float(reachable_km),
        "usage": usage, "counts_within_reach": counts, "shelters": rows})
    return {
        "status": "ok" if rows else "none_found",
        "source": "fema_nss",
        "freshness": "historical",
        "reachable_km": float(reachable_km),
        "counts_within_reach": counts,
        "caveats": ["straight_line_km is straight-line distance, not a drive time.",
                    "Flags are FEMA records (historical). 'unrecorded' means nobody filled it in."],
        "shelters": [{k: r[k] for k in (
            "shelter_id", "name", "address", "usage", "straight_line_km", "within_reach",
            "evacuation_capacity", "wheelchair_accessible", "generator_onsite", "in_surge_slosh_area",
            "in_100_yr_floodplain", "is_medical", "place_id")} for r in rows],
    }
