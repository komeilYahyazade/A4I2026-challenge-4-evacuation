"""Which ids the brief contains and every number it shows, derived from facts alone.

Both the writer's digest (what to judge) and the API's assemble_brief (what to show) call plan(),
so the writer is asked about exactly the ids the brief will carry. No number comes from the model.
"""

from __future__ import annotations

from readiness_agent import config
from readiness_agent.tools.facts import source_map

MAX_TRACTS = 5
MAX_CARE = 3
DROPPED = ("closed", "not_found")

# Fallback text (no numbers) for when the writer omits an unknown topic.
UNKNOWN_DEFAULTS = {
    "Wheelchair access": ("FEMA's field is blank: nobody recorded it, which is not the same as no.",
                          "Call or visit each site, or run the assessment agent."),
    "Wheelchair access after Google Maps": (
        "Neither the FEMA record nor Google Maps says whether a wheelchair user can get in.",
        "Someone has to visit or call the site and ask."),
    "Generator on site": ("FEMA's generator field is blank for these shelters.",
                          "Confirm with each site's facility manager before the season."),
    "Where power-dependent residents live": ("emPOWER publishes county totals only.",
                                             "No public source. Ask the county health department."),
    "Lack of reliable transport": (
        "CDC PLACES does not publish lack of reliable transport for this state; car ownership is "
        "not the same as having a ride.",
        "Use para-transit registries or a county special-needs registry."),
    "Assisted living facilities": ("There is no national public list; they are licensed by the state.",
                                   "Request the state assisted-living license roster."),
}


def _first(d: dict | None) -> dict | None:
    return next(iter(d.values())) if d else None


def tract_label(geo_id: str) -> str:
    base, suffix = int(geo_id[5:9]), geo_id[9:11]
    return f"Tract {base}" + (f".{suffix}" if suffix != "00" else "")


def plan(f: dict) -> dict:
    county = _first(f.get("county")) or {}
    tracts = ((_first(f.get("tracts")) or {}).get("tracts") or [])[:MAX_TRACTS]
    care = (_first(f.get("care")) or {}).get("facilities", [])
    power = _first(f.get("power"))
    verifs = f.get("verifications", {})
    candidates = {k: v for k, v in f.get("candidates", {}).items() if not k.startswith("county:")}

    far = [c for c in care if c["nearest_generator_yes_km"] is None
           or c["nearest_generator_yes_km"] > config.GENERATOR_GAP_KM]
    care_pick = sorted(far or care, key=lambda c: -(c["certified_beds"] or 0))[:MAX_CARE]

    who = [{"kind": "tract", "id": t["geo_id"], "row": t} for t in tracts]
    who += [{"kind": "care_facility", "id": c["ccn"], "row": c} for c in care_pick]

    top_ids = [t["geo_id"] for t in tracts]
    destinations = []
    for sid, v in verifs.items():
        rows = [(geo, s) for geo, c in candidates.items() for s in c["shelters"] if s["shelter_id"] == sid]
        serves = sorted({geo for geo, s in rows if s["within_reach"] and geo in top_ids})
        if not serves and (v.get("drive") or {}).get("from_geo_id"):
            serves = [v["drive"]["from_geo_id"]]
        kms = [s["straight_line_km"] for geo, s in rows if geo in serves and s["straight_line_km"] is not None]
        if not kms:
            kms = [s["straight_line_km"] for _g, s in rows if s["straight_line_km"] is not None]
        destinations.append({"shelter_id": sid, "serves": serves,
                             "straight_line_km": min(kms) if kms else None,
                             "row": rows[0][1] if rows else None, "verification": v})
    destinations.sort(key=lambda d: (d["verification"]["status"] in DROPPED,
                                     d["straight_line_km"] if d["straight_line_km"] is not None else 1e9))

    unknowns = []
    if county.get("shelters_total"):
        unknowns.append({"topic": "Wheelchair access", "count": county["wheelchair_unrecorded"],
                         "of": county["shelters_total"], "unit": "shelters"})
    if verifs:
        both = sum(1 for v in verifs.values() if v["wheelchair"]["agreement"] == "both_blank")
        unknowns.append({"topic": "Wheelchair access after Google Maps", "count": both,
                         "of": len(verifs), "unit": "shelters checked"})
    if county.get("shelters_total"):
        unknowns.append({"topic": "Generator on site", "count": county["generator_unrecorded"],
                         "of": county["shelters_total"], "unit": "shelters"})
    if power and power.get("power_dependent_dme") is not None:
        unknowns.append({"topic": "Where power-dependent residents live", "count": power["power_dependent_dme"],
                         "of": None, "unit": "people"})
    if county.get("lacktrpt_published_tracts") == 0:
        unknowns.append({"topic": "Lack of reliable transport", "count": None, "of": None, "unit": "tracts"})
    unknowns.append({"topic": "Assisted living facilities", "count": None, "of": None, "unit": "facilities"})

    smap = source_map(verifs)
    allowed_sources = sorted(set(smap.values()), key=lambda s: int(s[4:]))
    return {"county": county, "who": who, "destinations": destinations, "unknowns": unknowns,
            "power": power, "source_map": smap, "allowed_source_ids": allowed_sources}


# Numbers that are vocabulary, not data: "age 65+", "100-year floodplain", "call 911".
VOCABULARY_NUMBERS = {65.0, 100.0, 911.0}


def numbers_in_facts(f: dict, p: dict) -> set[float]:
    """Every number the writer may legitimately quote, with its rounded forms."""
    out: set[float] = set(VOCABULARY_NUMBERS)

    def add(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            out.update({float(v), float(round(v)), round(float(v), 1), round(float(v), 2)})
        elif isinstance(v, str):
            for tok in v.replace(",", "").split():
                try:
                    add(float(tok.strip(".():;%")))
                except ValueError:
                    pass
        elif isinstance(v, dict):
            for x in v.values():
                add(x)
        elif isinstance(v, (list, tuple, set)):
            for x in v:
                add(x)

    add(f)
    add({k: v for k, v in p.items() if k != "source_map"})
    # counts the writer may derive by counting lists the plan hands it
    add([len(p["who"]), len(p["destinations"]), sum(1 for w in p["who"] if w["kind"] == "tract"),
         sum(1 for d in p["destinations"] if d["verification"]["status"] in DROPPED),
         sum(1 for d in p["destinations"] if d["verification"]["status"] == "renamed"),
         config.REACHABLE_KM_NO_VEHICLE, config.GENERATOR_GAP_KM])
    for w in p["who"]:
        if w["kind"] == "tract":
            add(tract_label(w["id"]).split(" ", 1)[1])
    for d in p["destinations"]:
        wc = d["verification"]["wheelchair"]
        add(sum(1 for x in p["destinations"] if x["verification"]["wheelchair"]["maps"] == wc["maps"]))
    return out


def writer_tasks(f: dict) -> dict:
    """What the writer must judge, in the digest: exactly the ids the brief will carry."""
    p = plan(f)
    return {
        "reason_ids": [{"id": w["id"], "kind": w["kind"]} for w in p["who"]],
        "destination_ids": [d["shelter_id"] for d in p["destinations"]],
        "dropped_by_maps": [d["shelter_id"] for d in p["destinations"] if d["verification"]["status"] in DROPPED],
        "unknown_topics": [{"topic": u["topic"], "count": u["count"], "of": u["of"], "unit": u["unit"]}
                           for u in p["unknowns"]],
        "allowed_source_ids": p["allowed_source_ids"],
        "reachable_km_no_vehicle": reachable_km(f),
    }


def reachable_km(f: dict) -> float:
    return float((_first(f.get("gaps")) or {}).get("reachable_km") or config.REACHABLE_KM_NO_VEHICLE)
