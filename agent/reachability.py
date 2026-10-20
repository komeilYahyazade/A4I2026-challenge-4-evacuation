"""What "reachable" means for a household without a car. These are decisions; the reasons are here.

WALK_KM = 1.5
    About 20 to 30 minutes on foot for an older adult (3 to 4.5 km/h). Beyond that, walking to
    a shelter with luggage, medication and possibly a pet stops being a plan. Even inside this
    radius, walking is not feasible for everyone; the recommendation says so.

PARATRANSIT_MAX_MINUTES = 30
    A single para-transit or county bus run that can be repeated several times in the hours
    before landfall. Measured as drive time from Google Maps routing when available.

PARATRANSIT_PROXY_KM = 15
    Straight-line stand-in for the 30-minute run when no route is available: roughly 20 km of
    road at a 1.3 detour factor, about 30 minutes at 40 km/h. Straight-line distance is always a
    lower bound on road distance, and the output labels it as such.

CAR_KM = 40
    Search radius for any destination. Households with a car routinely drive farther; this is
    the bound for "a destination exists in the region at all".

NO_VEHICLE_HOUSEHOLDS_MIN = 50 and NO_VEHICLE_SHARE_MIN = 0.10
    A tract needs a pickup plan if either holds. The count matters as much as the share: 6% of
    a 3,000-household tract is 180 households with no car, which is a bus, not a rounding error.
    Note: these measure vehicle *ownership* (ACS). Owning no car is not the same as having no ride.

CAPACITY_PLAUSIBLE_MAX = 100_000
    FEMA records include capacities above 200,000 for single schools. We flag them and leave the
    federal number untouched; we never silently overwrite a federal record.

SITE_FLOOD_SHARE_HIGH = 0.5
    A pre-landfall shelter is flagged as flood-exposed (APPROXIMATE) when at least half the population
    of its nearest census tract is exposed to coastal flooding (FEMA NRI CFLD_EXPP / POPULATION). FEMA's
    own surge flag is blank for most shelters, so this is the only signal for most of them. "Any exposure"
    is too coarse: it fires for 71 of Lee County's 84 pre-landfall shelters. The same threshold drives the
    Hurricane Ian rehearsal, so the brief and the rehearsal agree.

PRE_LANDFALL_USAGE = ("EVAC", "BOTH")
    Only shelters designated for use before landfall count as destinations in an evacuation
    plan. POST shelters are a different building with a different job.
"""

from typing import Any, Optional

WALK_KM = 1.5
PARATRANSIT_MAX_MINUTES = 30
PARATRANSIT_PROXY_KM = 15.0
CAR_KM = 40.0
NO_VEHICLE_HOUSEHOLDS_MIN = 50
NO_VEHICLE_SHARE_MIN = 0.10
CAPACITY_PLAUSIBLE_MAX = 100_000
PRE_LANDFALL_USAGE: tuple[str, ...] = ("EVAC", "BOTH")
SITE_FLOOD_SHARE_HIGH = 0.5

ROUTED = "ROUTED (Google Maps routing, typical traffic, not evacuation traffic)"
MODEL_REPORTED = "MODEL-REPORTED drive time (no Google Maps route source returned)"
STRAIGHT_LINE = "STRAIGHT-LINE (lower bound on road distance)"


def assumptions() -> dict[str, Any]:
    return {
        "walk_km": WALK_KM,
        "paratransit_max_minutes": PARATRANSIT_MAX_MINUTES,
        "paratransit_straight_line_proxy_km": PARATRANSIT_PROXY_KM,
        "car_search_km": CAR_KM,
        "no_vehicle_households_min": NO_VEHICLE_HOUSEHOLDS_MIN,
        "no_vehicle_share_min": NO_VEHICLE_SHARE_MIN,
        "capacity_plausible_max": CAPACITY_PLAUSIBLE_MAX,
        "pre_landfall_usage": list(PRE_LANDFALL_USAGE),
        "site_flood_share_high": SITE_FLOOD_SHARE_HIGH,
    }


def carless_need(households_no_vehicle: Optional[float], households: Optional[float]) -> dict[str, Any]:
    n = float(households_no_vehicle or 0)
    total = float(households or 0)
    share = n / total if total > 0 else 0.0
    return {
        "households_no_vehicle": int(round(n)),
        "share_no_vehicle": round(share, 4),
        "needs_pickup_plan": n >= NO_VEHICLE_HOUSEHOLDS_MIN or share >= NO_VEHICLE_SHARE_MIN,
    }


def assess(
    straight_line_km: Optional[float],
    households_no_vehicle: Optional[float],
    households: Optional[float],
    drive_minutes: Optional[float] = None,
    drive_basis: Optional[str] = None,
) -> dict[str, Any]:
    """Classify one tract-to-destination pair for the people in the tract who have no car.

    Drive time, when present, decides the para-transit question. Straight-line distance is used
    only when no drive time exists, and the result says which basis it used.
    """
    need = carless_need(households_no_vehicle, households)
    n = need["households_no_vehicle"]
    out: dict[str, Any] = {
        **need,
        "straight_line_km": None if straight_line_km is None else round(float(straight_line_km), 2),
        "drive_minutes": None if drive_minutes is None else round(float(drive_minutes), 1),
    }

    if straight_line_km is None:
        out.update(
            distance_basis=STRAIGHT_LINE,
            walkable=False,
            paratransit_reachable=False,
            car_reachable=False,
            status="NO_DESTINATION",
            recommendation=(
                f"No pre-landfall shelter within {CAR_KM:.0f} km. Designate one or arrange "
                f"transport out of the county for the {n} households with no vehicle."
            ),
        )
        return out

    km = float(straight_line_km)
    walkable = km <= WALK_KM
    if drive_minutes is not None:
        basis = drive_basis or MODEL_REPORTED
        paratransit = float(drive_minutes) <= PARATRANSIT_MAX_MINUTES
    else:
        basis = STRAIGHT_LINE
        paratransit = km <= PARATRANSIT_PROXY_KM
    car = km <= CAR_KM

    if walkable:
        status = "WALKABLE"
        rec = (
            f"Within {WALK_KM} km on foot. Confirm the walking route, and plan a ride for residents "
            f"who cannot walk that far."
        )
    elif need["needs_pickup_plan"] and paratransit:
        status = "PICKUP_NEEDED"
        rec = (
            f"{n} households have no vehicle and no shelter within walking distance. Stage a "
            f"pickup point and a para-transit run (within {PARATRANSIT_MAX_MINUTES} minutes)."
        )
    elif need["needs_pickup_plan"]:
        status = "PICKUP_NEEDED_LONG_RUN"
        rec = (
            f"{n} households have no vehicle and the nearest pre-landfall shelter is beyond a "
            f"{PARATRANSIT_MAX_MINUTES}-minute run. Pre-arrange buses or designate a closer site."
        )
    else:
        status = "CAR_REACHABLE"
        rec = "Reachable by car." + (
            f" The {n} households without a vehicle still need a ride arranged." if n > 0 else ""
        )

    out.update(
        distance_basis=basis,
        walkable=walkable,
        paratransit_reachable=paratransit,
        car_reachable=car,
        status=status,
        recommendation=rec,
    )
    return out
