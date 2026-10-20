"""list_care_facilities: nursing homes, the buildings full of people who cannot leave."""

from __future__ import annotations

import asyncio

from google.adk.tools import ToolContext

from readiness_agent import config
from readiness_agent.tools import bq, facts


async def list_care_facilities(county_fips: str, tool_context: ToolContext) -> dict:
    """List the CMS-certified nursing homes in one county (placed by point-in-polygon), with
    certified beds and the straight-line km to the nearest shelter whose generator is recorded yes.

    Args:
        county_fips: 5-digit county FIPS.

    Returns:
        facilities (ccn, name, lat, lon, certified_beds, nearest_generator_yes_km), how many have no
        generator-yes shelter within 10 km, and caveats.
    """
    rows = await asyncio.to_thread(bq.run, "05_care_facilities.sql", county_fips=county_fips)
    facts.put(tool_context, "care", county_fips, {"county_fips": county_fips, "facilities": rows})
    far = [r for r in rows if r["nearest_generator_yes_km"] is None
           or r["nearest_generator_yes_km"] > config.GENERATOR_GAP_KM]
    return {
        "status": "ok" if rows else "none_found",
        "source": "cms",
        "freshness": "historical",
        "count": len(rows),
        "certified_beds_total": sum(r["certified_beds"] or 0 for r in rows),
        "no_generator_shelter_within_10km": len(far),
        "caveats": ["Nursing homes only (CMS). There is no national public list of assisted living "
                    "facilities; they are state-regulated.",
                    "Generator distance counts only shelters where somebody recorded a generator."],
        "facilities": rows[:50],
    }
