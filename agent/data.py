"""Data fetchers. Each returns plain dicts with the column names the SQL declares.

Kept separate from the tools so the brief can run several of them concurrently and so the
assembly logic in brief.py can be tested against exactly these row shapes.
"""

import copy
import functools
from typing import Any, Callable, Optional

from . import queries as Q
from .bq import (
    ACS_DATASET,
    COUNTIES_TABLE,
    columns,
    newest_acs_table,
    pick,
    render,
    require,
    run,
)
from .config import AgentConfig
from .reachability import (
    CAPACITY_PLAUSIBLE_MAX,
    CAR_KM,
    PARATRANSIT_PROXY_KM,
    PRE_LANDFALL_USAGE,
    WALK_KM,
)

NO_WALKABLE_PENALTY = 1.0  # one standard deviation: a whole tract with nothing on foot

_CACHED: list[Any] = []


def _memo(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Cache static lookups (county geometry scans ~190 MB each). Returns copies so callers can't
    mutate the cache. AgentConfig is a frozen dataclass, so it is a valid cache key."""
    cached = functools.lru_cache(maxsize=256)(fn)
    _CACHED.append(cached)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return copy.deepcopy(cached(*args, **kwargs))

    return wrapper


def clear_caches() -> None:
    for cached in _CACHED:
        cached.cache_clear()


def _base_params() -> dict[str, Any]:
    return {"pre_landfall_usage": list(PRE_LANDFALL_USAGE), "capacity_max": CAPACITY_PLAUSIBLE_MAX}


def _shelter_sql(template: str, cfg: AgentConfig) -> str:
    return render(
        template,
        shelter_columns=Q.SHELTER_COLUMNS,
        shelters=cfg.table("shelters"),
        tracts=cfg.table("vulnerability_tracts"),
    )


@_memo
def find_county(cfg: AgentConfig, name: str = "", county_fips: str = "") -> list[dict[str, Any]]:
    clean = name.strip()
    for suffix in (" county", " parish", " borough"):
        if clean.lower().endswith(suffix):
            clean = clean[: -len(suffix)]
    sql = render(Q.FIND_COUNTY, counties=COUNTIES_TABLE)
    return run(sql, {"state_fips": cfg.state_fips, "name": clean, "county_fips": county_fips}, cfg)


def county(cfg: AgentConfig, county_fips: str) -> dict[str, Any]:
    rows = find_county(cfg, county_fips=county_fips)
    match = [r for r in rows if r["county_fips"] == county_fips]
    if not match:
        raise ValueError(f"County {county_fips} not found in {cfg.state}")
    return match[0]


def county_shelters(cfg: AgentConfig, county_fips: str) -> list[dict[str, Any]]:
    return run(_shelter_sql(Q.COUNTY_SHELTERS, cfg), {**_base_params(), "county_fips": county_fips}, cfg)


def shelters_by_id(cfg: AgentConfig, shelter_ids: list[str]) -> list[dict[str, Any]]:
    ids = [str(i).strip() for i in shelter_ids if str(i).strip()]
    if not ids:
        return []
    return run(_shelter_sql(Q.SHELTERS_BY_ID, cfg), {**_base_params(), "shelter_ids": ids}, cfg)


def lookup_shelter(cfg: AgentConfig, name: str, county_fips: str = "") -> list[dict[str, Any]]:
    return run(
        _shelter_sql(Q.LOOKUP_SHELTER, cfg),
        {**_base_params(), "name": name.strip(), "county_fips": county_fips},
        cfg,
    )


RANKINGS: tuple[str, ...] = ("no_vehicle", "may_need_help", "composite_v0")


def ranked_tracts(
    cfg: AgentConfig, county_fips: str, limit: int = 10, ranking: str = "no_vehicle"
) -> list[dict[str, Any]]:
    if ranking not in RANKINGS:
        raise ValueError(f"ranking must be one of {RANKINGS}, got {ranking!r}")
    sql = render(
        Q.RANK_TRACTS,
        tracts=cfg.table("vulnerability_tracts"),
        shelters=cfg.table("shelters"),
        hazard=cfg.table("hazard_tracts"),
    )
    params = {
        "county_fips": county_fips,
        "pre_landfall_usage": list(PRE_LANDFALL_USAGE),
        "car_m": CAR_KM * 1000.0,
        "walk_m": WALK_KM * 1000.0,
        "proxy_m": PARATRANSIT_PROXY_KM * 1000.0,
        "no_walkable_penalty": NO_WALKABLE_PENALTY,
        "ranking": ranking,
        "limit": int(max(1, min(limit, 100))),
    }
    return run(sql, params, cfg)


def county_totals(cfg: AgentConfig, county_fips: str) -> dict[str, Any]:
    rows = run(render(Q.COUNTY_NO_VEHICLE_TOTAL, tracts=cfg.table("vulnerability_tracts")),
               {"county_fips": county_fips}, cfg)
    return rows[0] if rows else {}


def shelters_for_tract(cfg: AgentConfig, geo_id: str, limit: int = 15) -> list[dict[str, Any]]:
    params = {
        **_base_params(),
        "geo_id": geo_id,
        "car_m": CAR_KM * 1000.0,
        "limit": int(max(1, min(limit, 50))),
    }
    return run(_shelter_sql(Q.SHELTERS_FOR_TRACT, cfg), params, cfg)


def tract_origin(cfg: AgentConfig, geo_id: str) -> Optional[dict[str, Any]]:
    rows = run(render(Q.TRACT_ORIGIN, tracts=cfg.table("vulnerability_tracts")), {"geo_id": geo_id}, cfg)
    return rows[0] if rows else None


@_memo
def power_dependence(cfg: AgentConfig, county_fips: str) -> Optional[dict[str, Any]]:
    cols = columns(cfg, "power_dependent_counties")
    fips = require(pick(cols, "fips_code", "fips", "county_fips"), "FIPS", "power_dependent_counties", list(cols))
    dme = require(
        pick(cols, "power_dependent_dme", "power_dependent_devices_dme", "power_dependent_devices"),
        "power-dependent count",
        "power_dependent_counties",
        list(cols),
    )
    censored = pick(cols, "power_dependent_is_censored") or f"(SAFE_CAST({dme} AS INT64) = 11)"
    sql = render(Q.POWER_DEPENDENCE, power=cfg.table("power_dependent_counties"), fips=fips, dme=dme, censored=censored)
    rows = run(sql, {"county_fips": county_fips}, cfg)
    return rows[0] if rows else None


@_memo
def care_facilities(cfg: AgentConfig, county_fips: str) -> list[dict[str, Any]]:
    cols = columns(cfg, "care_facilities")
    t = "care_facilities"
    cf_lat = require(pick(cols, "latitude", "lat"), "latitude", t, list(cols))
    cf_lon = require(pick(cols, "longitude", "lon", "lng"), "longitude", t, list(cols))
    cf_name = require(pick(cols, "provider_name", "facility_name", "name"), "name", t, list(cols))
    cf_id = pick(cols, "cms_certification_number_ccn", "federal_provider_number", "ccn") or cf_name
    cf_beds = pick(cols, "number_of_certified_beds", "certified_beds") or "NULL"
    cf_city = pick(cols, "citytown", "city", "city_town") or "CAST(NULL AS STRING)"
    sql = render(
        Q.CARE_FACILITIES,
        counties=COUNTIES_TABLE,
        care=cfg.table("care_facilities"),
        shelters=cfg.table("shelters"),
        cf_id=cf_id,
        cf_name=cf_name,
        cf_city=cf_city,
        cf_beds=cf_beds,
        cf_lat=cf_lat,
        cf_lon=cf_lon,
    )
    params = {"county_fips": county_fips, "pre_landfall_usage": list(PRE_LANDFALL_USAGE), "car_m": CAR_KM * 1000.0}
    return run(sql, params, cfg)


# ACS measures used only by the equity audit. Discovered, not assumed.
_AUDIT_GROUPS = [
    ("black_pop", "share_black"),
    ("hispanic_pop", "share_hispanic"),
    ("white_pop", "share_white"),
    ("asian_pop", "share_asian"),
    ("amerindian_pop", "share_american_indian"),
]


def equity_audit(cfg: AgentConfig, county_fips: str, geo_ids: list[str]) -> dict[str, Any]:
    acs_table = newest_acs_table(cfg)
    acs_cols = columns(cfg, acs_table, dataset_path=ACS_DATASET)
    measures, shares, used = [], [], []
    for col, alias in _AUDIT_GROUPS:
        if col in acs_cols:
            measures.append(f"SAFE_CAST(a.{col} AS FLOAT64) AS {col}")
            shares.append(f"ROUND(SAFE_DIVIDE(SUM({col}), SUM(total_pop)), 4) AS {alias}")
            used.append(col)
    if {"poverty", "pop_determined_poverty_status"} <= set(acs_cols):
        measures.append("SAFE_CAST(a.poverty AS FLOAT64) AS poverty")
        measures.append("SAFE_CAST(a.pop_determined_poverty_status AS FLOAT64) AS poverty_universe")
        shares.append("ROUND(SAFE_DIVIDE(SUM(poverty), SUM(poverty_universe)), 4) AS poverty_rate")
        used.append("poverty")
    if not measures:
        raise RuntimeError(f"No audit measures found in {acs_table}")
    sql = render(
        Q.EQUITY_AUDIT,
        acs_dataset=ACS_DATASET,
        acs_table=acs_table,
        acs_measures=",\n    ".join(measures),
        acs_shares=",\n  ".join(shares),
    )
    demo = run(sql, {"state_fips": cfg.state_fips, "county_fips": county_fips, "geo_ids": geo_ids}, cfg)
    bias_sql = render(Q.REPORTING_BIAS, tracts=cfg.table("vulnerability_tracts"), shelters=cfg.table("shelters"))
    bias = run(
        bias_sql,
        {
            "geo_ids": geo_ids,
            "county_fips": county_fips,
            "pre_landfall_usage": list(PRE_LANDFALL_USAGE),
            "proxy_m": PARATRANSIT_PROXY_KM * 1000.0,
        },
        cfg,
    )
    return {"acs_table": acs_table, "measures_used": used, "demographics": demo, "reporting": bias}


def site_hazard(cfg: AgentConfig, shelter_ids: list[str]) -> dict[str, dict[str, Any]]:
    """APPROXIMATE flood exposure per shelter from the data lane's view, keyed by shelter_id."""
    ids = sorted({str(i) for i in shelter_ids if str(i).strip()})
    if not ids:
        return {}
    sql = render(Q.SITE_HAZARD, site_hazard=cfg.ops_table("v_shelter_site_hazard"))
    return {r["shelter_id"]: r for r in run(sql, {"shelter_ids": ids}, cfg)}
