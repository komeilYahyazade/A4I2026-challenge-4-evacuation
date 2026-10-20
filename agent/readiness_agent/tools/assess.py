"""Tools for the assessment conversation (and one for chat).

get_shelter_contact: the FEMA record plus the building's PUBLIC phone and website, looked up live on
  Google Maps so the manager can call. Shown with attribution, never stored (Maps storage rule).
  FEMA's point-of-contact columns name individuals and are deliberately not loaded; we never use them.
save_field_assessment: append what the operator said.
get_field_assessments: what operators have said about named shelters, for chat.
"""

from __future__ import annotations

import asyncio
import logging
import re

from google.adk.tools import ToolContext

from readiness_agent import attribution
from readiness_agent.tools import assessments, verify

log = logging.getLogger(__name__)

CONTACT_KEYS = ("MAPS_NAME", "PHONE", "WEBSITE", "NOTE")


def contact_prompt(shelter: dict) -> str:
    return "\n".join([
        "I need to phone this building to ask its operator about wheelchair access, for a hurricane-season "
        "planning record. Use Google Maps.",
        f'Building on file: "{shelter["name"]}", {shelter.get("address_1") or ""}, '
        f'{shelter.get("city") or ""}, FL {shelter.get("zip") or ""}. '
        f'Coordinates on file: {shelter["lat"]}, {shelter["lon"]}.',
        "Give only the organisation's public listing, never a named person.",
        "Answer with exactly these lines and nothing else:",
        "MAPS_NAME: <name on Google Maps, or none>",
        "PHONE: <public phone number, or none>",
        "WEBSITE: <website, or none>",
        "NOTE: <one short sentence, e.g. the main office or front desk>",
    ])


def parse_contact(text: str | None) -> dict:
    out = {k: None for k in CONTACT_KEYS}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*\**\s*([A-Z_]+)\s*\**\s*:\s*(.*)$", line)
        if m and m.group(1) in out:
            value = m.group(2).strip().strip("*").strip()
            out[m.group(1)] = None if value.lower() in ("", "none", "unknown", "n/a") else value
    return {"maps_name": out["MAPS_NAME"], "phone": out["PHONE"], "website": out["WEBSITE"],
            "note": out["NOTE"]}


async def lookup_contact(shelter: dict) -> dict:
    """One grounded Google Maps call. Without Maps sources the answer is not evidence: no contact."""
    checked_at = verify.now_iso()
    try:
        async with verify._sem():
            resp = await verify._generate(contact_prompt(shelter), shelter["lat"], shelter["lon"], False)
        cand = (resp.candidates or [None])[0]
        sources, _ = attribution.sources_from_metadata(getattr(cand, "grounding_metadata", None))
        parsed = parse_contact(resp.text)
    except Exception as e:
        log.exception("contact lookup for %s failed", shelter.get("shelter_id"))
        return {"checked_at": checked_at, "maps_name": None, "phone": None, "website": None,
                "note": f"The Google Maps lookup failed ({type(e).__name__}).", "sources": []}
    if not sources:
        parsed = {"maps_name": None, "phone": None, "website": None,
                  "note": "Google Maps returned no listing for this building."}
    return {"checked_at": checked_at, **parsed, "sources": sources}


def _shelter_summary(row: dict) -> dict:
    keys = ("shelter_id", "name", "address_1", "city", "zip", "county_fips", "usage", "evacuation_capacity",
            "wheelchair_accessible", "generator_onsite", "is_medical")
    return {k: row.get(k) for k in keys}


async def get_shelter_contact(shelter_id: str, tool_context: ToolContext) -> dict:
    """Start an assessment: the shelter's FEMA record, its latest field assessment if any, and the
    building's public phone number and website from Google Maps so the manager can call the operator.
    Call this first, once per shelter.

    Args:
        shelter_id: the FEMA shelter id.

    Returns:
        shelter (FEMA record, historical), latest_assessment (or null), and contact (live from Google
        Maps: maps_name, phone, website, note, sources). The contact is for display only and is not stored.
    """
    row = await asyncio.to_thread(verify.load_shelter, shelter_id)
    if row is None:
        return {"status": "error", "message": f"No shelter {shelter_id} in the registry."}
    latest = (await asyncio.to_thread(assessments.latest, [shelter_id])).get(str(shelter_id))
    contact = await lookup_contact(row)
    return {"status": "ok", "shelter": _shelter_summary(row), "latest_assessment": latest, "contact": contact,
            "display_rule": "Show phone and website with their Google Maps sources. Do not store them."}


async def save_field_assessment(shelter_id: str, wheelchair_unaccompanied: str, notes: str,
                                tool_context: ToolContext, ramp_unlocked_24_7: str = "unknown",
                                generator_powers_medical_outlets: str = "unknown",
                                assessor_role: str = "shelter operator") -> dict:
    """Save what the operator said, after the manager confirms your summary. Append-only.

    Args:
        shelter_id: the FEMA shelter id being assessed.
        wheelchair_unaccompanied: can somebody in a wheelchair get in, unaccompanied, today: yes, no or
            unclear. Use unclear unless the operator's answer settles it.
        notes: what the operator said, in their words, with conditions such as hours, locks, lifts
            out of service. No personal names.
        ramp_unlocked_24_7: is the accessible entrance unlocked at all hours: yes, no or unknown.
        generator_powers_medical_outlets: does a generator power medical outlets: yes, no or unknown.
        assessor_role: the role of the person who answered, e.g. "school facilities manager". No names.

    Returns:
        the saved field assessment, or an error explaining what to fix.
    """
    row = await asyncio.to_thread(verify.load_shelter, shelter_id)
    if row is None:
        return {"status": "error", "message": f"No shelter {shelter_id} in the registry."}
    try:
        params = assessments.build_record(row, wheelchair_unaccompanied, ramp_unlocked_24_7,
                                          generator_powers_medical_outlets, notes, assessor_role,
                                          getattr(getattr(tool_context, "session", None), "id", None))
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    await asyncio.to_thread(assessments.insert, params)
    saved = (await asyncio.to_thread(assessments.latest, [shelter_id])).get(str(shelter_id))
    return {"status": "ok", "saved": saved}


async def get_field_assessments(shelter_ids: list[str], tool_context: ToolContext) -> dict:
    """What shelter operators have said about wheelchair access, from field assessments (phone calls
    recorded in this tool's sessions). Use for "can a wheelchair user get in" questions about named
    shelters. A field assessment answers that question directly and outranks a blank FEMA field.

    Args:
        shelter_ids: FEMA shelter ids.

    Returns:
        assessments keyed by shelter_id (freshness "field", with the date and the operator's words),
        and missing: the ids nobody has assessed.
    """
    found = await asyncio.to_thread(assessments.latest, [str(s) for s in shelter_ids])
    return {"status": "ok", "source": ["field_assessment"], "assessments": found,
            "missing": [str(s) for s in shelter_ids if str(s) not in found]}
