import json

from helpers import CFG, columns_of, make_row

from agent.reachability import MODEL_REPORTED, ROUTED
from agent.verify import decide, ledger_from

SHELTER_COLS = columns_of(lambda d: d.shelters_by_id(CFG, ["1"]))
SOURCES = [{"title": "Lehigh Acres Middle School", "uri": "https://maps.google.com/?cid=1", "place_id": "places/abc"}]


def shelter(**kw):
    base = dict(shelter_id="1", shelter_name="Lehigh Acres MS", address_1="104 Arthur Ave", city="Lehigh Acres",
                zip="33936", latitude=26.61, longitude=-81.63, usage="EVAC", is_pre_landfall=True,
                evacuation_capacity=900, capacity_status="RECORDED", capacity_implausible=False,
                wheelchair_status="UNRECORDED",
                generator_status="YES", surge_status="NO", is_medical=False)
    base.update(kw)
    return make_row(SHELTER_COLS, **base)


def answer(**kw):
    body = {"exists": True, "current_name": "Lehigh Acres Middle School", "place_type": "school",
            "closed": "no", "wheelchair_entrance": "unknown", "drive_minutes": None, "notes": ""}
    body.update(kw)
    return "```json\n" + json.dumps(body) + "\n```"


def raw(text, sources=SOURCES, route=None, error=None):
    return {"text": text, "sources": sources, "route": route, "error": error, "routing_requested": route is not None}


def test_permanently_closed_is_dropped_with_a_fix():
    r = decide(shelter(), raw(answer(closed="permanently")))
    assert r["verdict"] == "DROP"
    assert "CLOSED" in r["flags"]
    assert any(f["type"] == "CLOSED" for f in r["fixes"])


def test_not_found_is_dropped():
    r = decide(shelter(), raw(answer(exists=False)))
    assert r["verdict"] == "DROP" and "NOT_FOUND" in r["flags"]


def test_text_without_maps_sources_is_not_evidence():
    r = decide(shelter(), raw(answer(exists=True), sources=[]))
    assert r["verdict"] == "KEEP_UNVERIFIED"
    assert r["live"]["status"] == "NO_MAPS_EVIDENCE"
    assert r["live"]["exists"] == "unknown"
    assert r["stored_pointer"] == {}


def test_missing_existence_field_stays_unknown():
    text = "```json\n" + json.dumps({"current_name": "Lehigh Acres Middle School"}) + "\n```"
    r = decide(shelter(), raw(text))
    assert r["live"]["exists"] == "unknown"
    assert r["verdict"] == "KEEP_UNVERIFIED"


def test_abbreviation_is_not_a_rename_but_a_real_rename_is_flagged():
    assert "NAME_DIFFERS" not in decide(shelter(), raw(answer()))["flags"]
    renamed = decide(
        shelter(shelter_name="First Presbyterian Church of Maitland"),
        raw(answer(current_name="Maitland Presbyterian Church")))
    assert renamed["verdict"] == "KEEP"
    assert "NAME_DIFFERS" in renamed["flags"]


def test_record_and_maps_disagree_on_wheelchair_access():
    r = decide(shelter(wheelchair_status="YES"), raw(answer(wheelchair_entrance="no")))
    assert "ACCESSIBILITY_CONFLICT" in r["flags"]
    assert r["verdict"] == "KEEP"


def test_unrecorded_and_unknown_stays_unrecorded_and_asks_someone():
    r = decide(shelter(wheelchair_status="UNRECORDED"), raw(answer(wheelchair_entrance="unknown")))
    assert r["accessibility"]["label"] == "UNRECORDED"
    assert any(f["type"] == "UNASSESSED_ACCESSIBILITY" for f in r["fixes"])


def test_maps_entrance_answer_fills_a_blank_record_as_live_evidence_only():
    r = decide(shelter(wheelchair_status="UNRECORDED"), raw(answer(wheelchair_entrance="yes")))
    assert r["accessibility"]["label"] == "VERIFIED LIVE"
    assert "not an ADA certification" in r["accessibility"]["summary"]
    assert r["record"]["wheelchair"] == "UNRECORDED"


def test_route_chunk_beats_model_reported_minutes():
    routed = decide(shelter(), raw(answer(drive_minutes=99), route={"drive_minutes": 14.0, "road_km": 15.1}))
    assert routed["live"]["drive_minutes"] == 14.0 and routed["live"]["drive_basis"] == ROUTED
    reported = decide(shelter(), raw(answer(drive_minutes=22)))
    assert reported["live"]["drive_basis"] == MODEL_REPORTED


def test_only_place_id_is_kept_and_ledger_counts():
    results = [decide(shelter(), raw(answer())), decide(shelter(shelter_id="2"), raw(answer(closed="permanently"))),
               decide(shelter(shelter_id="3"), raw(answer(), sources=[]))]
    assert results[0]["stored_pointer"] == {"place_id": "places/abc"}
    led = ledger_from(results, [{}, {}, {}])
    assert led["maps_calls"] == 3 and led["definite"] == 2 and led["unknown"] == 1 and led["dropped"] == 1
    assert any(c.startswith("DROP") for c in led["changes"])


def test_surge_and_implausible_capacity_are_flagged():
    r = decide(shelter(surge_status="YES", capacity_implausible=True), raw(answer()))
    assert "PRE_LANDFALL_IN_SURGE_ZONE" in r["flags"] and "CAPACITY_IMPLAUSIBLE" in r["flags"]


def test_rows_share_the_destination_shape_and_fixes_point_at_the_shelter():
    r = decide(shelter(), raw(answer(closed="permanently")))
    assert r["name"] == "Lehigh Acres MS" and r["lat"] == 26.61 and r["lng"] == -81.63
    fix = r["fixes"][0]
    assert fix["ref"] == {"kind": "shelter", "id": "1", "lat": 26.61, "lng": -81.63}
    assert fix["severity"] == "HIGH"


def test_dropped_shelter_with_unrecorded_capacity_says_so():
    r = decide(shelter(evacuation_capacity=None, capacity_status="UNRECORDED"), raw(answer(exists=False)))
    assert "no recorded capacity" in r["fixes"][0]["evidence"]
    assert r["record"]["capacity_status"] == "UNRECORDED"


def test_inactive_name_is_flagged_on_verification():
    r = decide(shelter(shelter_name="INACTIVE Grace Church", name_says_inactive=True), raw(answer()))
    assert "NAME_SAYS_INACTIVE" in r["flags"] and r["record"]["name_says_inactive"] is True


def test_verify_queue_checks_self_contradicting_records_first():
    import asyncio
    from unittest import mock

    from agent import verify

    seen = []

    async def fake_ground(row, origin, cfg):
        seen.append(row["shelter_id"])
        return raw(answer())

    rows = [shelter(shelter_id=str(i)) for i in range(3)] + [
        shelter(shelter_id="345200", shelter_name="INACTIVE Grace Church", name_says_inactive=True)]
    cfg = CFG.__class__(project_id="demo-project", verify_max=2, verify_concurrency=1)
    with mock.patch.object(verify, "ground_one", fake_ground):
        results, _ = asyncio.run(verify.verify_rows(rows, {}, cfg))
    assert seen[0] == "345200" and len(results) == 2


def test_inactive_record_with_a_successor_at_the_address_is_dropped_not_kept():
    r = decide(shelter(shelter_id="345200", shelter_name="INACTIVE Grace Church", name_says_inactive=True),
               raw(answer(current_name="Lehigh Acres Spanish Seventh-day Adventist Church", wheelchair_entrance="yes")))
    assert r["verdict"] == "DROP"
    assert {"NAME_DIFFERS", "SUCCESSOR_AT_ADDRESS", "NAME_SAYS_INACTIVE"} <= set(r["flags"])
    types = [f["type"] for f in r["fixes"]]
    assert "SUCCESSOR_AT_ADDRESS" in types and "NAME_DIFFERS" not in types  # never "rename the record"
    successor = next(f for f in r["fixes"] if f["type"] == "SUCCESSOR_AT_ADDRESS")
    assert successor["severity"] == "HIGH"
    assert successor["action"].startswith("Confirm with Lehigh Acres Spanish Seventh-day Adventist Church")


def test_both_live_outcomes_for_grace_church_lead_to_the_same_plan_decision():
    row = shelter(shelter_id="345200", shelter_name="INACTIVE Grace Church", name_says_inactive=True)
    not_found = decide(row, raw(answer(exists=False)))
    successor = decide(row, raw(answer(current_name="Lehigh Acres Spanish Seventh-day Adventist Church")))
    assert not_found["verdict"] == successor["verdict"] == "DROP"


def test_inactive_record_with_the_same_organisation_is_never_a_confident_keep():
    r = decide(shelter(shelter_name="INACTIVE Grace Church", name_says_inactive=True),
               raw(answer(current_name="Grace Church")))
    assert r["verdict"] == "KEEP_UNVERIFIED"
    assert "NAME_DIFFERS" not in r["flags"]  # status words are ignored when comparing names
    assert any(f["type"] == "NAME_SAYS_INACTIVE" and f["severity"] == "HIGH" for f in r["fixes"])


def test_an_ordinary_rename_is_still_kept():
    r = decide(shelter(shelter_name="First Presbyterian Church of Maitland"),
               raw(answer(current_name="Maitland Presbyterian Church")))
    assert r["verdict"] == "KEEP" and "SUCCESSOR_AT_ADDRESS" not in r["flags"]


def test_hourly_maps_budget_caps_live_calls_and_labels_the_rest():
    import asyncio
    from unittest import mock

    from agent import verify

    calls = []

    async def fake_ground(row, origin, cfg):
        calls.append(row["shelter_id"])
        return raw(answer())

    verify._recent_calls.clear()
    rows = [shelter(shelter_id=str(i)) for i in range(5)]
    cfg = CFG.__class__(project_id="demo-project", verify_max=8, maps_budget_per_hour=3)
    with mock.patch.object(verify, "ground_one", fake_ground):
        results, led = asyncio.run(verify.verify_rows(rows, {}, cfg))
        again, led2 = asyncio.run(verify.verify_rows(rows[:1], {}, cfg))
    verify._recent_calls.clear()
    assert len(calls) == 3 and led["maps_calls"] == 3 and led["skipped_budget"] == 2
    skipped = [r for r in results if r["live"]["status"] == "SKIPPED_BUDGET"]
    assert len(skipped) == 2 and all(r["verdict"] == "KEEP_UNVERIFIED" and r["live"]["label"] == "UNRECORDED" for r in skipped)
    assert led2["maps_calls"] == 0 and led2["skipped_budget"] == 1


def test_budget_window_rolls_over():
    from agent.verify import _recent_calls, take_budget

    _recent_calls.clear()
    assert take_budget(5, 2, now=1000.0) == 2
    assert take_budget(1, 2, now=2000.0) == 0
    assert take_budget(1, 2, now=4700.0) == 1  # first two are over an hour old
    assert take_budget(9, 0) == 9  # 0 means unlimited
    _recent_calls.clear()
