"""The four-segment Readiness Brief: who cannot leave, where they would go and whether that
holds up, what to fix before the season, and what we could not tell you.

assemble_brief() is pure and consumes exactly the row shapes data.py returns, so the contract
between SQL and Python is tested rather than assumed.
"""

import asyncio
import datetime
import logging
from typing import Any, Mapping, Optional

from google.adk.tools import ToolContext

from . import data
from .config import AgentConfig, county_fips_or_error
from .evidence import (
    APPROXIMATE, COUNTY_LEVEL, LABELS, MODELED, NO, RECORDED, UNRECORDED, VERIFIED_LIVE, YES, claim,
    collect_maps_sources, make_fix, payload, ref,
)
from .reachability import (
    CAR_KM,
    PARATRANSIT_MAX_MINUTES,
    PARATRANSIT_PROXY_KM,
    SITE_FLOOD_SHARE_HIGH,
    WALK_KM,
    assess,
    assumptions,
    carless_need,
)
from .verify import merge_ledger, verify_rows

logger = logging.getLogger(__name__)

FIX_ORDER = [
    "NO_DESTINATION", "CLOSED", "NOT_FOUND", "SUCCESSOR_AT_ADDRESS", "NAME_SAYS_INACTIVE", "PRE_LANDFALL_IN_SURGE_ZONE",
    "NO_WALKABLE_DESTINATION", "PRE_LANDFALL_FLOOD_EXPOSED_SITE", "OUTREACH_FOR_MUST_LEAVE",
    "GENERATOR_GAP", "CARE_FACILITY_FAR_FROM_GENERATOR", "NO_ASSESSED_ACCESSIBLE_DESTINATION",
    "ACCESSIBILITY_CONFLICT", "TEMPORARILY_CLOSED", "NAME_DIFFERS", "CAPACITY_IMPLAUSIBLE",
    "UNASSESSED_ACCESSIBILITY", "UNCONFIRMED",
]


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _ratio(value: Optional[float], base: Optional[float]) -> float:
    if value is None or not base:
        return 0.0
    return float(value) / float(base)


RANKING_TEXT = {
    "who_cannot_leave": (
        "Households without a vehicle, by count (ACS vehicle ownership), share as tiebreak. These are the people "
        "who cannot drive themselves out."),
    "must_leave_may_need_help": (
        "Sum of state z-scores of counts: mobile homes, residents 65+, and residents with a disability. Mobile "
        "homes must evacuate in any hurricane; older and disabled residents may need help doing it."),
}
RANKING_CHANGE = (
    "Changed after an equity audit. The first ranking summed four state z-scores of shares (no-vehicle, 65+, "
    "disability, mobile homes). In Lee County its top 15 tracts held only 14% of the county's households without "
    "a vehicle, because small tracts with high mobile-home and 65+ shares dominated it; ranking by count raises "
    "that to 34%. The two needs are now separate lists, both ranked by how many people, not what share. "
    "The original is reproducible with rank_tracts(ranking='composite_v0'). Race was used only to audit.")


def _focus_reasons(t: Mapping[str, Any]) -> list[dict[str, str]]:
    out = []
    if t.get("mobile_homes") is not None:
        out.append(claim(
            f"{int(t['mobile_homes'])} mobile homes ({t.get('pct_mobile_homes') or 0:.0f}% of housing; state average "
            f"{t.get('state_pct_mobile_homes') or 0:.0f}%)", RECORDED, "Census ACS / CDC SVI"))
    if t.get("pop_65_plus") is not None:
        out.append(claim(
            f"{int(t['pop_65_plus'])} residents aged 65+ ({t.get('pct_age_65_plus') or 0:.0f}%)",
            RECORDED, "Census ACS / CDC SVI"))
    if t.get("est_people_with_disability") is not None:
        out.append(claim(
            f"About {int(t['est_people_with_disability'])} residents with a disability "
            f"({t.get('pct_disability') or 0:.0f}%)", RECORDED, "CDC SVI (ACS estimate) times tract population"))
    return out


def tract_reasons(t: Mapping[str, Any], focus: str = "no_vehicle") -> list[dict[str, str]]:
    """Why this tract ranks high, each reason with its evidence label.

    focus="no_vehicle" leads with households without a car; focus="may_need_help" leads with the
    counts of mobile homes, older residents and residents with a disability.
    """
    reasons: list[dict[str, str]] = _focus_reasons(t) if focus == "may_need_help" else []
    n_noveh = int(t.get("households_no_vehicle") or 0)
    share = t.get("share_no_vehicle")
    reasons.append(claim(
        f"{n_noveh} households without a vehicle ({_pct(share)}; state average {_pct(t.get('state_share_no_vehicle'))})",
        RECORDED, "Census ACS (vehicle ownership)"))
    if focus == "may_need_help":
        return reasons
    if _ratio(t.get("pct_age_65_plus"), t.get("state_pct_age_65_plus")) >= 1.25:
        reasons.append(claim(
            f"{t['pct_age_65_plus']:.0f}% aged 65+ (state average {t['state_pct_age_65_plus']:.0f}%)",
            RECORDED, "CDC SVI (ACS estimate)"))
    if _ratio(t.get("pct_disability"), t.get("state_pct_disability")) >= 1.25:
        reasons.append(claim(
            f"{t['pct_disability']:.0f}% with a disability (state average {t['state_pct_disability']:.0f}%)",
            RECORDED, "CDC SVI (ACS estimate)"))
    if _ratio(t.get("pct_mobile_homes"), t.get("state_pct_mobile_homes")) >= 2.0:
        reasons.append(claim(
            f"{t['pct_mobile_homes']:.0f}% of housing is mobile homes (state average {t['state_pct_mobile_homes']:.0f}%)",
            RECORDED, "CDC SVI (ACS estimate)"))
    if t.get("pct_mobility") is not None:
        reasons.append(claim(f"Mobility disability modeled at {t['pct_mobility']:.0f}%", MODELED, "CDC PLACES"))
    if int(t.get("n_any_walkable") or 0) == 0:
        reasons.append(claim(
            f"No shelter of any kind within {WALK_KM} km straight-line", RECORDED, "FEMA NSS, straight-line distance"))
    if t.get("km_pre_landfall_shelter") is None:
        reasons.append(claim(f"No pre-landfall shelter within {CAR_KM:.0f} km", RECORDED, "FEMA NSS usage code"))
    yes = int(t.get("n_in_run_wheelchair_yes") or 0)
    unrec = int(t.get("n_in_run_wheelchair_unrecorded") or 0)
    total = int(t.get("n_pre_landfall_in_run") or 0)
    if total and yes == 0:
        reasons.append(claim(
            f"Within about {PARATRANSIT_MAX_MINUTES} minutes: {total} pre-landfall shelters, none recorded as "
            f"wheelchair accessible, {unrec} never assessed",
            UNRECORDED, "FEMA NSS wheelchair_accessible"))
    if (t.get("coastal_flood_exposed_pop") or 0) > 0:
        reasons.append(claim(
            f"{int(t['coastal_flood_exposed_pop'])} people exposed to coastal flooding", RECORDED, "FEMA NRI"))
    return reasons


SITE_HAZARD_NOTE = ("FEMA NRI coastal-flood exposure of the nearest 2020 census tract centroid. It describes the "
                    "neighbourhood, not the building's elevation.")


def site_hazard_view(h: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if not h:
        return None
    return {
        "label": APPROXIMATE,
        "coastal_flood_exposed_share": h.get("coastal_flood_exposed_share"),
        "coastal_flood_annual_frequency": h.get("coastal_flood_annual_frequency"),
        "hurricane_annual_frequency": h.get("hurricane_annual_frequency"),
        "nearest_geo_id": h.get("nearest_geo_id"),
        "km_to_centroid": h.get("km_to_centroid"),
        "method": h.get("method"),
        "note": SITE_HAZARD_NOTE,
    }


def flood_exposed(row: Mapping[str, Any], hazard: Optional[Mapping[str, Mapping[str, Any]]]) -> bool:
    if not hazard:
        return False
    share = (hazard.get(str(row.get("shelter_id"))) or {}).get("coastal_flood_exposed_share")
    return share is not None and float(share) >= SITE_FLOOD_SHARE_HIGH


def _shelter_summary(rows: list[Mapping[str, Any]],
                     hazard: Optional[Mapping[str, Mapping[str, Any]]] = None) -> dict[str, Any]:
    pre = [r for r in rows if r.get("is_pre_landfall")]

    def tri(field: str, subset: list[Mapping[str, Any]]) -> dict[str, int]:
        return {
            "yes": sum(1 for r in subset if r.get(field) == YES),
            "no": sum(1 for r in subset if r.get(field) == NO),
            "unrecorded": sum(1 for r in subset if r.get(field) == UNRECORDED),
        }

    inactive = [r for r in pre if r.get("name_says_inactive")]
    recorded_cap = [r for r in pre if r.get("capacity_status") == "RECORDED" and not r.get("capacity_implausible")
                    and not r.get("name_says_inactive")]
    plausible_capacity = sum(int(r.get("evacuation_capacity") or 0) for r in recorded_cap)
    return {
        "shelters": len(rows),
        "pre_landfall": len(pre),
        "post_only_or_other": sum(1 for r in rows if r.get("usage") and not r.get("is_pre_landfall")),
        "usage_unrecorded": sum(1 for r in rows if not r.get("usage")),
        "pre_landfall_capacity_plausible": plausible_capacity,
        "pre_landfall_capacity_recorded": len(recorded_cap),
        "pre_landfall_capacity_unrecorded": sum(
            1 for r in pre if r.get("capacity_status") != "RECORDED" and not r.get("name_says_inactive")),
        "capacity_implausible": sum(1 for r in rows if r.get("capacity_implausible")),
        "pre_landfall_name_says_inactive": len(inactive),
        "pre_landfall_name_says_inactive_spaces": sum(
            int(r.get("evacuation_capacity") or 0) for r in inactive if r.get("capacity_status") == "RECORDED"),
        "pre_landfall_flood_exposed_approx": (
            None if hazard is None else sum(1 for r in pre if flood_exposed(r, hazard))),
        "medical_needs": sum(1 for r in rows if r.get("is_medical")),
        "medical_needs_generator_yes": sum(1 for r in rows if r.get("is_medical") and r.get("generator_status") == YES),
        "pre_landfall_wheelchair": tri("wheelchair_status", pre),
        "pre_landfall_generator": tri("generator_status", pre),
        "pre_landfall_surge": tri("surge_status", pre),
    }


_fix = make_fix


def assemble_brief(
    county: Mapping[str, Any],
    tracts: list[Mapping[str, Any]],
    county_shelter_rows: list[Mapping[str, Any]],
    candidates_by_tract: Mapping[str, list[Mapping[str, Any]]],
    verifications: Mapping[str, Mapping[str, Any]],
    power: Optional[Mapping[str, Any]],
    care: list[Mapping[str, Any]],
    ledger: Optional[Mapping[str, Any]] = None,
    generated_at: Optional[str] = None,
    site_hazard: Optional[Mapping[str, Mapping[str, Any]]] = None,
    help_tracts: Optional[list[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """site_hazard is None when the data lane's view was unavailable; {} means none matched."""
    name = county.get("county_name")
    summary = _shelter_summary(county_shelter_rows, site_hazard)
    fixes: list[dict[str, Any]] = []

    who: list[dict[str, Any]] = []
    where: list[dict[str, Any]] = []
    for rank, t in enumerate(tracts, start=1):
        gid = t["geo_id"]
        cands = list(candidates_by_tract.get(gid, []))
        live_ok = [c for c in cands
                   if verifications.get(c["shelter_id"], {}).get("verdict") != "DROP" and not c.get("name_says_inactive")]
        nearest = live_ok[0] if live_ok else None
        v = verifications.get(nearest["shelter_id"]) if nearest else None
        drive = v["live"]["drive_minutes"] if v else None
        basis = v["live"]["drive_basis"] if v else None
        mobility = assess(
            nearest["straight_line_km"] if nearest else t.get("km_pre_landfall_shelter"),
            t.get("households_no_vehicle"), t.get("households"), drive, basis,
        )
        tract_ref = ref("tract", gid, t.get("lat"), t.get("lon"))
        who.append({
            "rank": rank,
            "geo_id": gid,
            "lat": t.get("lat"),
            "lng": t.get("lon"),
            "priority_score": t.get("priority_score"),
            "people": {
                "total_pop": t.get("total_pop"),
                "households_no_vehicle": t.get("households_no_vehicle"),
                "pop_65_plus": t.get("pop_65_plus"),
                "pct_disability": t.get("pct_disability"),
                "mobile_homes": t.get("mobile_homes"),
            },
            "reach": {
                "km_any_shelter": t.get("km_any_shelter"),
                "km_pre_landfall_shelter": t.get("km_pre_landfall_shelter"),
                "km_pre_landfall_wheelchair_recorded_yes": t.get("km_pre_landfall_wheelchair_recorded_yes"),
                "shelters_within_walk": t.get("n_any_walkable"),
                "pre_landfall_within_run": t.get("n_pre_landfall_in_run"),
                "within_run_wheelchair": {
                    "yes": t.get("n_in_run_wheelchair_yes"),
                    "no": t.get("n_in_run_wheelchair_no"),
                    "unrecorded": t.get("n_in_run_wheelchair_unrecorded"),
                },
            },
            "hazard": {
                "hurricane_exposed_pop": t.get("hurricane_exposed_pop"),
                "coastal_flood_exposed_pop": t.get("coastal_flood_exposed_pop"),
            },
            "reasons": tract_reasons(t),
            "mobility": mobility,
        })

        need = carless_need(t.get("households_no_vehicle"), t.get("households"))
        if mobility["status"] == "NO_DESTINATION":
            fixes.append(_fix("NO_DESTINATION", f"Tract {gid}",
                              f"No pre-landfall shelter within {CAR_KM:.0f} km.", mobility["recommendation"], RECORDED,
                              tract_ref))
        elif int(t.get("n_any_walkable") or 0) == 0 and need["needs_pickup_plan"]:
            fixes.append(_fix(
                "NO_WALKABLE_DESTINATION", f"Tract {gid}",
                f"{need['households_no_vehicle']} households without a vehicle; no shelter within {WALK_KM} km.",
                mobility["recommendation"], RECORDED, tract_ref))
        yes = int(t.get("n_in_run_wheelchair_yes") or 0)
        unrec = int(t.get("n_in_run_wheelchair_unrecorded") or 0)
        if yes == 0 and int(t.get("n_pre_landfall_in_run") or 0) > 0:
            fixes.append(_fix(
                "NO_ASSESSED_ACCESSIBLE_DESTINATION", f"Tract {gid}",
                f"No pre-landfall shelter within about {PARATRANSIT_PROXY_KM:.0f} km is recorded as wheelchair "
                f"accessible; {unrec} have never been assessed.",
                f"Assess those {unrec} shelters for unaccompanied wheelchair entry before the season.", UNRECORDED,
                tract_ref))

        for c in cands[:3]:
            ver = verifications.get(c["shelter_id"])
            where.append({
                "for_tract": gid,
                "shelter_id": c["shelter_id"],
                "name": c.get("shelter_name"),
                "lat": c.get("latitude"),
                "lng": c.get("longitude"),
                "flags": list(ver["flags"]) if ver else [],
                "address": ", ".join(str(p) for p in (c.get("address_1"), c.get("city"), c.get("zip")) if p),
                "straight_line_km": c.get("straight_line_km"),
                "record": {
                    "label": RECORDED,
                    "capacity": c.get("evacuation_capacity") if c.get("capacity_status") == "RECORDED" else None,
                    "capacity_status": c.get("capacity_status"),
                    "capacity_as_filed": c.get("evacuation_capacity"),
                    "capacity_implausible": c.get("capacity_implausible"),
                    "usage": c.get("usage"),
                    "wheelchair": c.get("wheelchair_status"),
                    "generator": c.get("generator_status"),
                    "surge": c.get("surge_status"),
                    "is_medical": c.get("is_medical"),
                    "name_says_inactive": bool(c.get("name_says_inactive")),
                    "site_hazard": site_hazard_view((site_hazard or {}).get(c["shelter_id"])),
                },
                "live": ver["live"] if ver else None,
                "accessibility": ver["accessibility"] if ver else None,
                "verdict": ver["verdict"] if ver else "NOT_VERIFIED_THIS_RUN",
                "why": ver["why"] if ver else "Not checked against Google Maps in this brief.",
            })

    help_rows: list[dict[str, Any]] = []
    for rank, t in enumerate(help_tracts or [], start=1):
        gid = t["geo_id"]
        help_rows.append({
            "rank": rank,
            "geo_id": gid,
            "lat": t.get("lat"),
            "lng": t.get("lon"),
            "priority_score": t.get("priority_score"),
            "people": {
                "total_pop": t.get("total_pop"),
                "mobile_homes": t.get("mobile_homes"),
                "pop_65_plus": t.get("pop_65_plus"),
                "est_people_with_disability": t.get("est_people_with_disability"),
                "households_no_vehicle": t.get("households_no_vehicle"),
            },
            "reach": {
                "km_any_shelter": t.get("km_any_shelter"),
                "km_pre_landfall_shelter": t.get("km_pre_landfall_shelter"),
                "pre_landfall_within_run": t.get("n_pre_landfall_in_run"),
            },
            "hazard": {
                "hurricane_exposed_pop": t.get("hurricane_exposed_pop"),
                "coastal_flood_exposed_pop": t.get("coastal_flood_exposed_pop"),
            },
            "reasons": tract_reasons(t, focus="may_need_help"),
            "mobility": assess(t.get("km_pre_landfall_shelter"), t.get("households_no_vehicle"), t.get("households")),
        })
        if rank <= 3:
            fixes.append(_fix(
                "OUTREACH_FOR_MUST_LEAVE", f"Tract {gid}",
                f"{int(t.get('mobile_homes') or 0)} mobile homes, {int(t.get('pop_65_plus') or 0)} residents 65+, about "
                f"{int(t.get('est_people_with_disability') or 0)} with a disability.",
                "Plan outreach before the season: mobile homes must evacuate in any hurricane. Find out who needs a ride "
                "or medical transport, and which shelter they would use.",
                RECORDED, ref("tract", gid, t.get("lat"), t.get("lon"))))

    for ver in verifications.values():
        fixes.extend(ver.get("fixes", []))

    for s in county_shelter_rows:
        subject = f"{s.get('shelter_name')} ({s.get('shelter_id')})"
        shelter_ref = ref("shelter", s.get("shelter_id"), s.get("latitude"), s.get("longitude"))
        if s.get("is_pre_landfall") and s.get("name_says_inactive"):
            fixes.append(_fix(
                "NAME_SAYS_INACTIVE", subject,
                f"The FEMA record's own name reads '{s.get('shelter_name')}', yet it is still designated "
                f"{s.get('usage')} (pre-landfall).",
                "Confirm the building's status and remove the pre-landfall designation if it is out of use.",
                RECORDED, shelter_ref))
        if s.get("is_pre_landfall") and s.get("surge_status") != YES and flood_exposed(s, site_hazard):
            h = site_hazard[str(s.get("shelter_id"))]
            fixes.append(_fix(
                "PRE_LANDFALL_FLOOD_EXPOSED_SITE", subject,
                f"FEMA's surge flag is {s.get('surge_status')}, but {float(h['coastal_flood_exposed_share']):.0%} of the "
                f"nearest tract's residents are exposed to coastal flooding (annual frequency "
                f"{float(h.get('coastal_flood_annual_frequency') or 0):.2f}). Approximate: neighbourhood, not building.",
                "Confirm the site's surge zone and elevation with the county before relying on it pre-landfall.",
                APPROXIMATE, shelter_ref))
        if s.get("is_pre_landfall") and s.get("surge_status") == YES:
            fixes.append(_fix(
                "PRE_LANDFALL_IN_SURGE_ZONE", subject,
                "Designated for use before landfall but recorded inside the SLOSH storm-surge area.",
                "Reassign to post-impact use or confirm elevation and hardening.", RECORDED, shelter_ref))
        if s.get("capacity_implausible"):
            fixes.append(_fix(
                "CAPACITY_IMPLAUSIBLE", subject,
                f"Recorded capacity {s.get('evacuation_capacity')} is implausible; excluded from capacity totals.",
                "Correct the FEMA record. We leave the federal number as filed.", RECORDED, shelter_ref))

    if power and (power.get("power_dependent_dme") or 0) > 0 and summary["medical_needs_generator_yes"] == 0:
        censored = " (reported as 11, which means 1 to 10)" if power.get("power_dependent_is_censored") else ""
        fixes.append(_fix(
            "GENERATOR_GAP", f"{name} County",
            f"{power['power_dependent_dme']} Medicare beneficiaries rely on electricity-dependent equipment{censored}; "
            f"{summary['medical_needs']} medical-needs shelters, none recorded with a generator.",
            "Secure a generator-backed medical-needs site or a written transfer agreement.", COUNTY_LEVEL,
            ref("county", county.get("county_fips"), county.get("lat"), county.get("lng"))))

    for f in care:
        km = f.get("km_to_generator_recorded_shelter")
        if km is None or km > PARATRANSIT_PROXY_KM:
            where_txt = "none within 40 km" if km is None else f"{km} km away"
            fixes.append(_fix(
                "CARE_FACILITY_FAR_FROM_GENERATOR", f"{f.get('facility_name')} ({f.get('facility_id')})",
                f"{int(f.get('certified_beds') or 0)} certified beds; nearest pre-landfall shelter recorded with a "
                f"generator is {where_txt}.",
                "Confirm an on-site generator and a transfer agreement exist.", RECORDED,
                ref("facility", f.get("facility_id"), f.get("latitude"), f.get("longitude"))))

    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for f in fixes:
        key = (f["type"], f["subject"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    order = {k: i for i, k in enumerate(FIX_ORDER)}
    unique.sort(key=lambda f: order.get(f["type"], len(order)))

    wc = summary["pre_landfall_wheelchair"]
    gen = summary["pre_landfall_generator"]
    surge = summary["pre_landfall_surge"]
    lacktrpt_missing = bool(tracts) and all(t.get("pct_lacktrpt") is None for t in tracts)
    led = dict(ledger or {})
    could_not = [
        {"item": "Wheelchair access at pre-landfall shelters", "spoken_to": wc["yes"] + wc["no"],
         "unassessed": wc["unrecorded"],
         "note": f"{wc['yes'] + wc['no']} I can speak to; {wc['unrecorded']} nobody has assessed.", "label": UNRECORDED},
        {"item": "Generators at pre-landfall shelters", "spoken_to": gen["yes"] + gen["no"],
         "unassessed": gen["unrecorded"], "label": UNRECORDED},
        {"item": "Surge-zone status of pre-landfall shelters", "spoken_to": surge["yes"] + surge["no"],
         "unassessed": surge["unrecorded"], "label": UNRECORDED,
         "note": f"FEMA's surge flag is blank for {surge['unrecorded']} of {summary['pre_landfall']} pre-landfall "
                 "shelters. A shelter that is not flagged is not known to be outside the surge zone."},
        {"item": "Site flood exposure", "label": APPROXIMATE,
         "note": ("Unavailable in this run: the data lane's site-hazard view could not be read."
                  if site_hazard is None else
                  f"{summary['pre_landfall_flood_exposed_approx']} pre-landfall shelters sit in tracts where at least "
                  f"{SITE_FLOOD_SHARE_HIGH:.0%} of residents are exposed to coastal flooding. " + SITE_HAZARD_NOTE)},
        {"item": "Capacity of pre-landfall shelters", "spoken_to": summary["pre_landfall_capacity_recorded"],
         "unassessed": summary["pre_landfall_capacity_unrecorded"], "label": UNRECORDED,
         "note": "A blank or zero capacity on a pre-landfall shelter is unrecorded, not zero spaces. "
                 "Capacity totals count recorded values only, and exclude shelters whose own name says "
                 f"inactive or closed ({summary['pre_landfall_name_says_inactive_spaces']} spaces)."},
        {"item": "Shelter usage code (EVAC or POST)", "unassessed": summary["usage_unrecorded"],
         "note": "Shelters without a usage code are not counted as pre-landfall destinations.", "label": UNRECORDED},
        {"item": "Power-dependent residents", "label": COUNTY_LEVEL,
         "note": "HHS emPOWER is published per county. We can say how many, never which tract or block."},
        {"item": "Drive times", "label": RECORDED,
         "note": "Straight-line distance is a lower bound. Routed times come from Google Maps for checked "
                 "shelters only, and reflect typical traffic, not evacuation traffic."},
        {"item": "Assisted living facilities", "label": UNRECORDED,
         "note": "No national public roster exists; assisted living is state-licensed."},
        {"item": "Mobile home parks", "label": RECORDED,
         "note": "We have mobile-home counts per tract, not park locations."},
    ]
    if lacktrpt_missing:
        could_not.append({
            "item": "Lack of reliable transportation", "label": UNRECORDED,
            "note": "CDC PLACES does not publish this measure here. We quote vehicle ownership from the ACS, "
                    "and owning no car is not the same as having no ride."})
    if led.get("maps_calls"):
        could_not.append({
            "item": "Live checks Google Maps could not settle", "unassessed": led.get("unknown", 0),
            "note": f"{led.get('definite', 0)} of {led['maps_calls']} grounded checks gave a definite answer.",
            "label": VERIFIED_LIVE})

    return payload("readiness_brief", {
        "county": {"fips": county.get("county_fips"), "name": name, "lat": county.get("lat"), "lng": county.get("lng")},
        "generated_at": generated_at or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "assumptions": {
            **assumptions(),
            "ranking": RANKING_TEXT["who_cannot_leave"],
            "rankings": RANKING_TEXT,
            "ranking_inputs": "Physical and demographic only. No accessibility field, no hazard composite, and no "
                              "race or ethnicity is an input.",
            "ranking_change": RANKING_CHANGE,
        },
        "county_shelters": summary,
        "power_dependence": None if power is None else {**dict(power), "label": COUNTY_LEVEL},
        "who_cannot_leave": who,
        "must_leave_may_need_help": help_rows,
        "where_they_would_go": where,
        "fix_before_season": unique,
        "could_not_tell_you": could_not,
        "grounding_ledger": led,
        "maps_sources": collect_maps_sources(verifications.values()),
        "evidence_labels": LABELS,
    })


def _pick_to_verify(
    candidates_by_tract: Mapping[str, list[Mapping[str, Any]]],
    limit: int,
    priority: Optional[list[Mapping[str, Any]]] = None,
) -> dict[str, dict[str, Any]]:
    """What to check on Google Maps, in order: pre-landfall shelters whose own name says inactive
    (the record contradicts itself, so a live check settles it), then the nearest two pre-landfall
    candidates per tract that are not recorded in the surge zone. Deduplicated, capped at limit."""
    chosen: dict[str, dict[str, Any]] = {}
    for row in priority or []:
        if len(chosen) >= limit:
            return chosen
        if row.get("is_pre_landfall") and row.get("name_says_inactive"):
            chosen[row["shelter_id"]] = {"row": row, "origin": None}
    for gid, cands in candidates_by_tract.items():
        if len(chosen) >= limit:
            break
        picked = 0
        for c in cands:
            if c.get("surge_status") == YES or c["shelter_id"] in chosen:
                continue
            chosen[c["shelter_id"]] = {"row": c, "origin": {"lat": c.get("origin_lat"), "lng": c.get("origin_lng")}}
            picked += 1
            if picked == 2 or len(chosen) >= limit:
                break
        if len(chosen) >= limit:
            break
    return chosen


async def build_readiness_brief(
    county_fips: str,
    top_tracts: int = 5,
    verify_with_maps: bool = True,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Build the August Readiness Brief for one county, in four segments.

    1. Who cannot leave: the census tracts with the most households without a vehicle, ranked, with
       the reason for each and its evidence label. Alongside it, must_leave_may_need_help: the tracts
       with the most mobile homes, older residents and residents with a disability.
    2. Where they would go, and whether that holds up: the nearest pre-landfall shelters for each
       tract with the FEMA record, and (if verify_with_maps) a live Google Maps check of up to 8
       of them, including routed drive time from the tract.
    3. What to fix before the season: concrete gaps, ordered by severity.
    4. What we could not tell you: unrecorded fields counted, and the limits of the data.

    county_fips is the 5-digit county FIPS code (use find_county first if you only have a name).
    Takes 20 to 40 seconds with verification on. Returns structured JSON; summarize it for the
    manager and display Google Maps sources right after any sentence that relies on them.
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    top = max(1, min(int(top_tracts), 10))

    county, tracts, help_tracts, shelters, power, care = await asyncio.gather(
        asyncio.to_thread(data.county, cfg, fips),
        asyncio.to_thread(data.ranked_tracts, cfg, fips, top, "no_vehicle"),
        asyncio.to_thread(data.ranked_tracts, cfg, fips, top, "may_need_help"),
        asyncio.to_thread(data.county_shelters, cfg, fips),
        asyncio.to_thread(data.power_dependence, cfg, fips),
        asyncio.to_thread(data.care_facilities, cfg, fips),
    )
    if tool_context is not None:
        tool_context.state["county_fips"] = fips
        tool_context.state["county_centroid"] = {"lat": county["lat"], "lng": county["lng"]}

    cand_lists = await asyncio.gather(*(asyncio.to_thread(data.shelters_for_tract, cfg, t["geo_id"], 10) for t in tracts))
    candidates_by_tract = {t["geo_id"]: c for t, c in zip(tracts, cand_lists)}

    hazard_ids = [s["shelter_id"] for s in shelters] + [c["shelter_id"] for cl in cand_lists for c in cl]
    try:
        hazard: Optional[dict[str, dict[str, Any]]] = await asyncio.to_thread(data.site_hazard, cfg, hazard_ids)
    except Exception as exc:  # the view is optional; the brief says so instead of failing
        logger.warning("site hazard view unavailable: %s", exc)
        hazard = None

    verifications: dict[str, dict[str, Any]] = {}
    ledger: dict[str, Any] = {}
    if verify_with_maps:
        chosen = _pick_to_verify(candidates_by_tract, cfg.verify_max, priority=shelters)
        if chosen:
            results, ledger = await verify_rows(
                [v["row"] for v in chosen.values()],
                {k: v["origin"] for k, v in chosen.items() if v["origin"]}, cfg)
            verifications = {r["shelter_id"]: r for r in results}
            merge_ledger(tool_context.state if tool_context else None, ledger)

    return assemble_brief(county, tracts, shelters, candidates_by_tract, verifications, power, care, ledger,
                          site_hazard=hazard, help_tracts=help_tracts)
