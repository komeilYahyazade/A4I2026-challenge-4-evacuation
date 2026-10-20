"""allocate_seats and rehearse_ian: capacity and the Ian tabletop, for the analyst, chat and voice.

Compact results only (totals, the tracts left without a seat, site options, shelters taken offline):
a full allocation is ~120 KB, and the map loads the full layers from the REST routes itself.
"""

from __future__ import annotations

import asyncio

from google.adk.tools import ToolContext

from readiness_agent import plan
from readiness_agent.tools import facts


async def allocate_seats(county_fips: str, tool_context: ToolContext, max_km: float = plan.PARATRANSIT_KM) -> dict:
    """Assign people in households without a car to pre-landfall shelters within recorded capacity
    (min-cost flow on straight-line distance, each tract to its 15 nearest shelters, any county).
    Never uses Google Maps. Use for "does the capacity hold, and who is left without a seat".

    Args:
        county_fips: 5-digit county FIPS.
        max_km: straight-line reach for a pickup run (default 15 km, a 30-minute para-transit proxy).

    Returns:
        totals (people, seated, without a seat, usable capacity), the tracts left without a seat,
        where one new site would seat the most people, and the rules used.
    """
    a = await asyncio.to_thread(plan.allocation, county_fips, float(max_km))
    out = {"status": a["status"], "source": ["acs", "fema_nss"], "freshness": "historical", "maps_used": False,
           "county_fips": county_fips, "max_km": a["max_km"], "totals": a["totals"],
           "unassigned": a["unassigned"][:10], "unassigned_tracts": len(a["unassigned"]),
           "site_options": a["site_options"], "excluded_counts": a["excluded_counts"], "rules": a["rules"]}
    facts.put(tool_context, "allocation", county_fips, out)
    return out


async def rehearse_ian(county_fips: str, tool_context: ToolContext, advisory: str = plan.DEFAULT_ADVISORY) -> dict:
    """Tabletop rehearsal: replay Hurricane Ian (2022) at one NHC advisory (9, 11, 13, 17 or 19;
    19 is 22 hours before landfall), take offline the two in-plan shelters the cone and coastal-flood
    exposure put most at risk, and re-solve. Planning drill only: never Google Maps, never a live
    emergency. Use for "what if Ian came again" or "which shelters would we lose".

    Args:
        county_fips: 5-digit county FIPS.
        advisory: NHC advisory number as a string.

    Returns:
        the shelters taken offline and why, at-risk shelters the plan never counted on, people
        without a seat before and after, capacity lost, and the tracts newly without a seat.
    """
    r = await asyncio.to_thread(plan.rehearse, county_fips, str(advisory))
    out = {k: v for k, v in r.items() if k not in ("cone", "reassigned")}
    out["newly_unassigned"] = r["newly_unassigned"][:10]
    facts.put(tool_context, "rehearsal", f"{county_fips}:{advisory}", out)
    return out
