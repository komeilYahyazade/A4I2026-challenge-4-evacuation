"""Function tools we wrote ourselves.

These hold the judgment calls the managed BigQuery MCP server can't: what
"reachable" means without a car, and keeping "unrecorded" apart from "no".
"""

from google.cloud import bigquery

from . import config

_client: bigquery.Client | None = None


def _bq() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(project=config.PROJECT or None)
    return _client


def _rows(sql: str, params: list) -> list[dict]:
    job = _bq().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
    return [dict(r) for r in job.result()]


# What "reachable" means is our modeling decision, not a fact in the data.
# Distances are straight-line; real drive times come from Maps routing.
REACH_BANDS_KM = [
    (3.0, "near: walkable or a short para-transit run"),
    (15.0, "needs organized transport for households without a vehicle"),
    (float("inf"), "out of reach without a pre-arranged pickup"),
]


def _reach_band(km: float) -> str:
    return next(label for limit, label in REACH_BANDS_KM if km <= limit)


def _flag(sql_col: str) -> str:
    """A nullable YES/NO boolean as yes / no / unrecorded. Unrecorded is never 'no'."""
    return (f"CASE WHEN {sql_col} IS TRUE THEN 'yes' "
            f"WHEN {sql_col} IS FALSE THEN 'no' ELSE 'unrecorded' END")


def resolve_county(county_name: str) -> dict:
    """Look up a county in the configured state by name and return its 5-digit FIPS code.

    Args:
        county_name: County name, e.g. "Pinellas" or "Miami-Dade".
    """
    state_fips = config.STATE_FIPS.get(config.STATE)
    if not state_fips:
        return {"error": f"No FIPS mapping for state {config.STATE}; add it to config.STATE_FIPS."}
    rows = _rows(
        """
        SELECT geo_id AS county_fips, county_name
        FROM `bigquery-public-data.geo_us_boundaries.counties`
        WHERE state_fips_code = @state AND LOWER(county_name) LIKE LOWER(@name)
        ORDER BY county_name
        LIMIT 5
        """,
        [bigquery.ScalarQueryParameter("state", "STRING", state_fips),
         bigquery.ScalarQueryParameter("name", "STRING", f"%{county_name.strip()}%")],
    )
    if not rows:
        return {"error": f"No county matching '{county_name}' in {config.STATE}."}
    return {"matches": rows}


def rank_tracts_by_evacuation_need(county_fips: str, limit: int = 10) -> dict:
    """Rank census tracts in a county by how hard it would be for residents to self-evacuate.

    Ranks on households with no vehicle, then reports age, disability and mobile-home
    figures alongside. Vehicle ownership is not the same as having no ride: say which
    measure you are quoting.

    Args:
        county_fips: 5-digit county FIPS code (use resolve_county first).
        limit: How many tracts to return.
    """
    rows = _rows(
        f"""
        SELECT geo_id, households_no_vehicle, pct_no_vehicle, pct_age_65_plus,
               pct_disability, mobile_homes, lat, lon
        FROM {config.table("vulnerability_tracts")}
        WHERE county_fips = @county
        ORDER BY households_no_vehicle DESC NULLS LAST
        LIMIT @limit
        """,
        [bigquery.ScalarQueryParameter("county", "STRING", county_fips),
         bigquery.ScalarQueryParameter("limit", "INT64", limit)],
    )
    return {
        "county_fips": county_fips,
        "ranked_by": "households_no_vehicle (ACS, vehicle ownership)",
        "tracts": rows,
    }


def find_shelters_near_tract(geo_id: str, max_km: float = 25.0, limit: int = 15) -> dict:
    """Find shelters near a census tract, with distance, a reachability band and record status.

    Every accessibility, generator and surge answer is 'yes', 'no' or 'unrecorded'.
    Report unrecorded as unknown, never as no. Distances are straight-line km from the
    tract centroid; ground the top candidates with Google Maps for drive time and to
    confirm the building still exists.

    Args:
        geo_id: 11-digit census tract id.
        max_km: Search radius in kilometers (straight line).
        limit: Maximum number of shelters to return.
    """
    rows = _rows(
        f"""
        WITH t AS (
          SELECT ST_GEOGPOINT(lon, lat) AS pt
          FROM {config.table("vulnerability_tracts")}
          WHERE geo_id = @geo_id
        )
        SELECT s.shelter_id, s.shelter_name, s.address_1, s.city, s.zip, s.county_name,
               s.evacuation_capacity, s.usage, s.is_medical,
               {_flag("s.wheelchair_accessible")} AS wheelchair,
               {_flag("s.generator_onsite")} AS generator,
               {_flag("s.in_surge_slosh_area")} AS in_surge_zone,
               ROUND(ST_DISTANCE(t.pt, ST_GEOGPOINT(s.longitude, s.latitude)) / 1000, 1) AS km
        FROM {config.table("shelters")} s, t
        WHERE ST_DWITHIN(t.pt, ST_GEOGPOINT(s.longitude, s.latitude), @max_m)
        ORDER BY km
        LIMIT @limit
        """,
        [bigquery.ScalarQueryParameter("geo_id", "STRING", geo_id),
         bigquery.ScalarQueryParameter("max_m", "FLOAT64", max_km * 1000),
         bigquery.ScalarQueryParameter("limit", "INT64", limit)],
    )
    for r in rows:
        r["reach"] = _reach_band(r["km"])
        r["source"] = "FEMA National Shelter System (historical registry, not live status)"

    counts = {v: sum(r["wheelchair"] == v for r in rows) for v in ("yes", "no", "unrecorded")}
    return {
        "geo_id": geo_id,
        "shelters": rows,
        "wheelchair_summary": (
            f"{counts['yes']} recorded accessible, {counts['no']} recorded not accessible, "
            f"{counts['unrecorded']} nobody has assessed"),
        "distance_note": "Straight-line distance. Use Google Maps routing for drive time.",
    }
