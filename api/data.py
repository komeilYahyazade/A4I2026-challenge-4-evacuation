"""Live data routes: SQL from sql/ straight to the contract, plus /verify."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

from api import config
from api.errors import ApiError
from contract import schemas
from readiness_agent import plan, shapes
from readiness_agent.tools import bq
from readiness_agent.tools import verify as verify_tool

CACHE_TTL_S = 600
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()

# Verifications made through the API this process lifetime (Maps content is never stored
# anywhere durable; this dies with the instance).
LAST_VERIFICATION: dict[str, dict] = {}


def _cached(key: str, fn: Callable[[], object]):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def counties() -> dict:
    rows = _cached("counties", lambda: bq.run("01_county_ranking.sql"))
    return {
        "state": config.STATE,
        "counties": [{
            "county_fips": r["county_fips"],
            "name": r["name"] or r["county_fips"],
            "rank": r["rank"],
            "need_score": r["need_score"],
            "exposure_score": r["exposure_score"],
            "total_pop": r["total_pop"],
            "shelters": {
                "total": r["shelters_total"], "medical_needs": r["medical_needs"],
                "in_surge_zone": r["in_surge_zone"],
                "wheelchair": {"yes": r["wheelchair_yes"], "no": r["wheelchair_no"],
                               "unrecorded": r["wheelchair_unrecorded"]},
            },
            "center": {"lat": r["lat"], "lon": r["lon"]} if r["lat"] is not None else None,
        } for r in rows],
    }


def county_name(county_fips: str) -> str:
    for c in counties()["counties"]:
        if c["county_fips"] == county_fips:
            return c["name"]
    raise ApiError(404, "county_not_found", f"No county {county_fips} in {config.STATE}")


def county_map(county_fips: str) -> dict:
    county_name(county_fips)  # 404 for a county outside the state
    row = _cached(f"map:{county_fips}", lambda: bq.run("07_county_map.sql", county_fips=county_fips)[0])
    power = None
    if row["power_dependent_dme"] is not None:
        power = {"county_fips": county_fips, "power_dependent_dme": row["power_dependent_dme"],
                 "is_censored": bool(row["is_censored"]), "scope": "county"}
    return {
        "county_fips": county_fips,
        "center": {"lat": row["lat"], "lon": row["lon"]} if row["lat"] is not None else None,
        "tracts": row["tracts"],
        "shelters": [shapes.shelter(s) for s in row["shelters"]],
        "care_facilities": row["care_facilities"],
        "power_dependence": power,
    }


def shelter_detail(shelter_id: str) -> dict:
    row = verify_tool.load_shelter(shelter_id)
    if row is None:
        raise ApiError(404, "shelter_not_found", f"No shelter {shelter_id} in {config.STATE}")
    return shapes.shelter_detail(row, LAST_VERIFICATION.get(shelter_id))


async def verify(shelter_id: str, req: schemas.VerifyRequest) -> dict:
    row = await asyncio.to_thread(verify_tool.load_shelter, shelter_id)
    if row is None:
        raise ApiError(404, "shelter_not_found", f"No shelter {shelter_id} in {config.STATE}")
    origin = None
    if req.from_geo_id:
        origin = await asyncio.to_thread(verify_tool.load_tract, req.from_geo_id)
        if origin is None:
            raise ApiError(404, "tract_not_found", f"No tract {req.from_geo_id} with a centroid")
    v = await verify_tool.verify(row, origin, req.include_route)
    LAST_VERIFICATION[shelter_id] = v
    with _cache_lock:  # a new place_id may now exist for this county's map
        _cache.pop(f"map:{row['county_fips']}", None)
    return v


def insights(county_fips: str, reachable_km: float) -> dict:
    name = county_name(county_fips)
    r = _cached(f"insights:{county_fips}:{reachable_km}",
                lambda: bq.run("13_county_insights.sql", county_fips=county_fips, reachable_km=reachable_km)[0])

    def access(p: str) -> dict:
        return {"tracts": r[f"{p}_tracts"], "median_km_any": r[f"{p}_median_km_any"],
                "median_km_wheelchair_yes": r[f"{p}_median_km_wc"],
                "people_over_10km_any": r[f"{p}_people_over_10km"],
                "households_no_vehicle": r[f"{p}_hh_no_vehicle"],
                "households_no_vehicle_within_reach_any": r[f"{p}_hh_reach_any"],
                "households_no_vehicle_within_reach_wheelchair_yes": r[f"{p}_hh_reach_wc"],
                "tracts_no_wheelchair_yes_within_reach": r[f"{p}_tracts_no_wc"]}

    def baseline(p: str) -> dict:
        return {"pct_no_vehicle": r[f"{p}_pct_no_vehicle"], "pct_age_65_plus": r[f"{p}_pct_65"],
                "pct_disability": r[f"{p}_pct_disability"], "pct_mobile_homes": r[f"{p}_pct_mobile_homes"],
                "poverty_rate": r[f"{p}_poverty_rate"], "pct_mobility": r[f"{p}_pct_mobility"]}

    return {
        "county_fips": county_fips, "name": r["name"] or name, "reachable_km": reachable_km,
        "shelters": {"total": r["shelters_total"], "evac": r["evac_total"], "medical_needs": r["medical_total"],
                     "capacity_recorded": r["capacity_recorded"], "capacity_unrecorded": r["capacity_unrecorded"],
                     "capacity_implausible": r["capacity_implausible"], "verified_on_maps": r["verified_on_maps"]},
        "flags": r["flags"],
        "access": {"county": access("county"), "state": access("state")},
        "baseline": {"county": baseline("county"), "state": baseline("state")},
        "care": {"facilities": r["care_facilities"] or 0, "certified_beds": r["care_beds"],
                 "far_from_generator": r["care_far_from_generator"] or 0,
                 "certified_beds_far_from_generator": r["care_beds_far_from_generator"], "threshold_km": 10.0},
        "power": {"power_dependent_dme": r["power_dependent_dme"], "is_censored": bool(r["power_is_censored"]),
                  "medical_capacity": r["medical_capacity"], "scope": "county"},
        "freshness": "historical",
        "sources": ["fema_nss", "acs", "cdc_svi", "cdc_places", "fema_nri", "cms", "hhs_empower"],
    }


def allocation(county_fips: str, max_km: float) -> dict:
    county_name(county_fips)
    return _cached(f"alloc:{county_fips}:{max_km}", lambda: plan.allocation(county_fips, max_km))


def rehearsal(county_fips: str, advisory: str) -> dict:
    county_name(county_fips)
    advisories = {a["advisory"] for a in storm()["advisories"]}
    if advisory not in advisories:
        raise ApiError(404, "advisory_not_found", f"No Ian advisory {advisory}; have {sorted(advisories, key=int)}")
    return _cached(f"rehearsal:{county_fips}:{advisory}", lambda: plan.rehearse(county_fips, advisory))


def storm() -> dict:
    return _cached("storm:ian_2022", plan.storm_track)
