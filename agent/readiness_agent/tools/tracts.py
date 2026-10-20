"""rank_vulnerable_tracts: who cannot leave, by census tract (never by person)."""

from __future__ import annotations

import asyncio

from google.adk.tools import ToolContext

from readiness_agent import config
from readiness_agent.tools import bq, facts

TRANSPORT_CAVEAT = ("households_no_vehicle (ACS) and pct_no_vehicle (CDC SVI) measure car ownership, "
                    "not lacking a ride. pct_lacktrpt (CDC PLACES) is not published for Florida.")


def county_ref(county_fips: str) -> dict:
    rows = bq.run("12_county_ref.sql", county_fips=county_fips)
    return rows[0] if rows else {"county_fips": county_fips, "name": None, "shelters_total": 0}


RANK_BY = ("need_score", "households_no_vehicle", "pop_65_plus")


async def rank_vulnerable_tracts(county_fips: str, tool_context: ToolContext, limit: int = 10,
                                 rank_by: str = "need_score") -> dict:
    """Rank the census tracts in one county by need: where people who cannot self-evacuate are
    concentrated. Drivers: households_no_vehicle, pct_no_vehicle, pop_65_plus, pct_disability,
    mobile_homes, group_quarters_pop. Also gives straight-line km to the nearest shelter and to the
    nearest shelter recorded as wheelchair accessible.

    Args:
        county_fips: 5-digit county FIPS, e.g. "12071" for Lee County, FL.
        limit: how many tracts to return (max 50).
        rank_by: "need_score" (composite, default), "households_no_vehicle" (who needs a ride),
            or "pop_65_plus".

    Returns:
        tracts ranked by need_score, with the drivers and distances, plus the county's shelter
        flag counts (yes / no / unrecorded) and the caveats that must be stated.
    """
    limit = max(1, min(int(limit), 50))
    rank_by = rank_by if rank_by in RANK_BY else "need_score"
    tracts, county = await asyncio.gather(
        asyncio.to_thread(bq.run, "02_rank_tracts.sql", county_fips=county_fips, limit=limit, rank_by=rank_by),
        asyncio.to_thread(county_ref, county_fips))
    facts.put(tool_context, "county", county_fips, county)
    facts.put(tool_context, "tracts", county_fips, {"county_fips": county_fips, "rank_by": rank_by, "tracts": tracts})
    return {
        "status": "ok" if tracts else "no_tracts",
        "source": ["acs", "cdc_svi", "fema_nri", "fema_nss"],
        "freshness": "historical",
        "county": county,
        "rank_by": rank_by,
        "need_score": config.NEED_SCORE_FORMULA,
        "caveats": [TRANSPORT_CAVEAT, "Distances are straight-line km from the tract centroid.",
                    "nearest_wheelchair_yes_km counts only shelters where somebody recorded yes."],
        "tracts": tracts,
    }
