"""get_power_dependence: HHS emPOWER, county total only."""

from __future__ import annotations

import asyncio

from google.adk.tools import ToolContext

from readiness_agent.tools import bq, facts

CAVEAT = "County total: how many people, not where they live."
CENSORED = "emPOWER shows 11 for any count from 1 to 10, so this value means 1 to 10."


async def get_power_dependence(county_fips: str, tool_context: ToolContext) -> dict:
    """How many Medicare beneficiaries in one county rely on electricity-dependent medical
    equipment (HHS emPOWER), and the county's medical-needs shelter count and recorded capacity.
    County level only: never say which block or tract they live in.

    Args:
        county_fips: 5-digit county FIPS.

    Returns:
        power_dependent_dme, is_censored, medical_shelters, medical_capacity, and the caveat text.
    """
    row = (await asyncio.to_thread(bq.run, "06_power_dependence.sql", county_fips=county_fips))[0]
    caveats = [CAVEAT] + ([CENSORED] if row["is_censored"] else [])
    result = {**row, "caveat": CAVEAT}
    facts.put(tool_context, "power", county_fips, result)
    return {"status": "ok" if row["power_dependent_dme"] is not None else "not_published",
            "source": ["hhs_empower", "fema_nss"], "freshness": "historical",
            **row, "caveats": caveats}
