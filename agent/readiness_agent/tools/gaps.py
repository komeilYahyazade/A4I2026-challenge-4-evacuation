"""find_readiness_gaps: deterministic rules over the facts already gathered (BUILD_SPEC.md 9.2).

Every gap carries the ids it affects and the numbers behind it. The writer only words them.
"""

from __future__ import annotations

from google.adk.tools import ToolContext

from readiness_agent import config
from readiness_agent.tools import facts

DROPPED = ("closed", "not_found")
CATEGORY_ORDER = ("no_accessible_destination", "generator_gap", "record_mismatch",
                  "shelter_in_surge_zone", "unassessed_access", "medical_needs_gap")


def _first(d: dict | None) -> dict | None:
    return next(iter(d.values())) if d else None


def _affected(tracts=(), shelters=(), care=()) -> dict:
    return {"tracts": sorted(set(tracts)), "shelters": sorted(set(shelters)), "care_facilities": sorted(set(care))}


def compute_gaps(f: dict, reachable_km: float = config.REACHABLE_KM_NO_VEHICLE,
                 generator_km: float = config.GENERATOR_GAP_KM) -> list[dict]:
    verifs = f.get("verifications", {})
    tracts = (_first(f.get("tracts")) or {}).get("tracts", [])
    candidates = {k: v for k, v in f.get("candidates", {}).items() if not k.startswith("county:")}
    gaps: list[dict] = []

    # 1. no_accessible_destination: a top-need tract with no recorded-yes shelter within reach.
    #    Split into "nobody assessed" vs "recorded no" vs "nothing within reach at all".
    for t in tracts:
        cand = candidates.get(t["geo_id"])
        if cand is None:
            continue
        within = [s for s in cand["shelters"] if s["within_reach"]
                  and verifs.get(s["shelter_id"], {}).get("status") not in DROPPED]
        if any(s["wheelchair_accessible"] == "yes" for s in within):
            continue
        unrec = [s for s in within if s["wheelchair_accessible"] == "unrecorded"]
        maps_yes = [s["shelter_id"] for s in within
                    if (verifs.get(s["shelter_id"]) or {}).get("wheelchair", {}).get("maps") == "yes"]
        subtype = ("none_within_reach" if not within else "nobody_assessed" if unrec else "recorded_no")
        gaps.append({
            "category": "no_accessible_destination", "subtype": subtype,
            "affected": _affected([t["geo_id"]], [s["shelter_id"] for s in within]),
            "metrics": {"reachable_km": reachable_km, "shelters_within_reach": len(within),
                        "wheelchair_no": sum(1 for s in within if s["wheelchair_accessible"] == "no"),
                        "wheelchair_unrecorded": len(unrec),
                        "maps_reports_accessible_entrance": len(maps_yes),
                        "nearest_wheelchair_yes_km": t.get("nearest_wheelchair_yes_km"),
                        "households_no_vehicle": t.get("households_no_vehicle")},
        })

    # 2. unassessed_access: candidate shelters within reach, unrecorded, and Maps had no answer
    #    (or was never asked).
    seen: dict[str, dict] = {}
    for geo, cand in candidates.items():
        for s in cand["shelters"]:
            if s["within_reach"]:
                seen.setdefault(s["shelter_id"], {**s, "serves": set()})["serves"].add(geo)
    unassessed = [s for sid, s in seen.items() if s["wheelchair_accessible"] == "unrecorded"
                  and (verifs.get(sid) or {}).get("wheelchair", {}).get("maps", "no_answer") == "no_answer"
                  and verifs.get(sid, {}).get("status") not in DROPPED]
    if unassessed:
        checked = [s for s in unassessed if s["shelter_id"] in verifs]
        gaps.append({
            "category": "unassessed_access", "subtype": None,
            "affected": _affected([g for s in unassessed for g in s["serves"]], [s["shelter_id"] for s in unassessed]),
            "metrics": {"shelters": len(unassessed), "checked_on_maps_no_answer": len(checked),
                        "not_checked_on_maps": len(unassessed) - len(checked)},
        })

    # 3. generator_gap: a nursing home with no generator-yes shelter within generator_km.
    care = (_first(f.get("care")) or {}).get("facilities", [])
    far = [c for c in care if c["nearest_generator_yes_km"] is None or c["nearest_generator_yes_km"] > generator_km]
    if far:
        gaps.append({
            "category": "generator_gap", "subtype": None,
            "affected": _affected(care=[c["ccn"] for c in far]),
            "metrics": {"facilities": len(far), "of_facilities": len(care), "threshold_km": generator_km,
                        "certified_beds": sum(c["certified_beds"] or 0 for c in far)},
        })

    # 4. shelter_in_surge_zone: candidate shelters recorded inside the surge zone or 100-yr floodplain.
    surge = [s for s in seen.values() if s["in_surge_slosh_area"] == "yes" or s["in_100_yr_floodplain"] == "yes"]
    if surge:
        gaps.append({
            "category": "shelter_in_surge_zone", "subtype": None,
            "affected": _affected([g for s in surge for g in s["serves"]], [s["shelter_id"] for s in surge]),
            "metrics": {"shelters": len(surge), "evac_shelters": sum(1 for s in surge if s["usage"] == "EVAC"),
                        "in_surge_slosh_area": sum(1 for s in surge if s["in_surge_slosh_area"] == "yes"),
                        "in_100_yr_floodplain": sum(1 for s in surge if s["in_100_yr_floodplain"] == "yes")},
        })

    # 5. record_mismatch: Maps says closed / not found / renamed, or disagrees on wheelchair access.
    for sid, v in sorted(verifs.items()):
        reasons = []
        if v["status"] in ("closed", "not_found", "renamed"):
            reasons.append(v["status"])
        if v["wheelchair"]["agreement"] == "disagree":
            reasons.append("wheelchair_disagree")
        if reasons:
            gaps.append({
                "category": "record_mismatch", "subtype": ",".join(reasons),
                "affected": _affected(sorted(seen.get(sid, {}).get("serves", ())), [sid]),
                "metrics": {"status": v["status"], "record_name": v["record_name"], "maps_name": v.get("maps_name"),
                            "wheelchair_record": v["wheelchair"]["record"], "wheelchair_maps": v["wheelchair"]["maps"]},
            })

    # 6. medical_needs_gap: medical-needs capacity small relative to power-dependent residents.
    power = _first(f.get("power"))
    if power and power.get("power_dependent_dme"):
        cap = power.get("medical_capacity") or 0
        ratio = round(power["power_dependent_dme"] / cap, 1) if cap else None
        if not cap or ratio > 1:
            gaps.append({
                "category": "medical_needs_gap", "subtype": None,
                "affected": _affected(),
                "metrics": {"power_dependent_dme": power["power_dependent_dme"], "is_censored": power["is_censored"],
                            "medical_shelters": power.get("medical_shelters"), "medical_capacity": cap,
                            "people_per_medical_space": ratio},
            })

    gaps.sort(key=lambda g: CATEGORY_ORDER.index(g["category"]))
    return gaps


async def find_readiness_gaps(county_fips: str, tool_context: ToolContext,
                              reachable_km_no_vehicle: float = config.REACHABLE_KM_NO_VEHICLE) -> dict:
    """Find what to fix before the season, from the facts already gathered this session (ranked
    tracts, candidate shelters, Google Maps verifications, nursing homes, power dependence). Call it
    after verifying shelters. Categories: no_accessible_destination (split into nobody_assessed /
    recorded_no / none_within_reach), unassessed_access, generator_gap, shelter_in_surge_zone,
    record_mismatch, medical_needs_gap. Each gap lists the tract, shelter and facility ids it affects.

    Args:
        county_fips: 5-digit county FIPS.
        reachable_km_no_vehicle: straight-line km that counts as reachable without a car.

    Returns:
        gaps, each with category, subtype, affected ids and the metrics behind it.
    """
    gaps = compute_gaps(facts.collect(tool_context.state), float(reachable_km_no_vehicle))
    facts.put(tool_context, "gaps", county_fips, {"county_fips": county_fips, "gaps": gaps,
                                                  "reachable_km": float(reachable_km_no_vehicle)})
    return {"status": "ok", "source": ["fema_nss", "acs", "cms", "hhs_empower", "google_maps"],
            "count": len(gaps), "gaps": gaps}
