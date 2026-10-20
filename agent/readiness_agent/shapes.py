"""Turn sql/ rows into contract-shaped dicts. One place, so tools and data routes agree."""

SHELTER_KEYS = (
    "shelter_id", "name", "address", "county_fips", "lat", "lon", "nearest_geo_id", "usage",
    "evacuation_capacity", "post_impact_capacity", "wheelchair_accessible", "generator_onsite",
    "in_surge_slosh_area", "in_100_yr_floodplain", "is_medical", "place_id",
)
DETAIL_KEYS = ("pet_accommodations_code", "population_code", "shelter_status_code", "incident_id")
FLAG_KEYS = ("wheelchair_accessible", "generator_onsite", "in_surge_slosh_area", "in_100_yr_floodplain")


def shelter(row: dict) -> dict:
    out = {k: row.get(k) for k in SHELTER_KEYS}
    for k in FLAG_KEYS:  # belt and braces: the SQL boundary already emits three-state strings
        if out[k] not in ("yes", "no", "unrecorded"):
            out[k] = "unrecorded"
    out["is_medical"] = bool(out["is_medical"])
    out["source"], out["freshness"] = "fema_nss", "historical"
    return out


def shelter_detail(row: dict, last_verification: dict | None = None) -> dict:
    out = shelter(row)
    out.update({k: row.get(k) for k in DETAIL_KEYS})
    out["last_verification"] = last_verification
    return out


def flag_counts(shelters: list[dict], key: str = "wheelchair_accessible") -> dict:
    counts = {"yes": 0, "no": 0, "unrecorded": 0}
    for s in shelters:
        counts[s.get(key) if s.get(key) in counts else "unrecorded"] += 1
    return counts
