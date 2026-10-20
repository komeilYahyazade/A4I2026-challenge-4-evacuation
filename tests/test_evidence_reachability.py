from agent.evidence import LABELS, UNRECORDED, claim, three_state
from agent.reachability import MODEL_REPORTED, ROUTED, STRAIGHT_LINE, assess, carless_need


def test_three_state_never_turns_blank_into_no():
    assert three_state(True) == "YES"
    assert three_state(False) == "NO"
    for blank in (None, "", "   ", "UNK", "unknown", "UNRECORDED"):
        assert three_state(blank) == UNRECORDED
    assert three_state("YES") == "YES"
    assert three_state(" no ") == "NO"


def test_claim_rejects_unknown_labels():
    assert claim("x", "RECORDED")["label"] == "RECORDED"
    assert set(LABELS) == {"RECORDED", "UNRECORDED", "VERIFIED LIVE", "MODELED", "COUNTY-LEVEL", "APPROXIMATE"}
    try:
        claim("x", "PROBABLY")
    except ValueError:
        return
    raise AssertionError("unknown label accepted")


def test_drive_time_decides_paratransit_when_present():
    # 18 km straight-line fails the 15 km proxy, but a routed 25-minute drive is within the run.
    r = assess(18.0, 420, 1600, drive_minutes=25, drive_basis=ROUTED)
    assert r["status"] == "PICKUP_NEEDED"
    assert r["distance_basis"] == ROUTED
    slow = assess(9.0, 420, 1600, drive_minutes=41, drive_basis=MODEL_REPORTED)
    assert slow["status"] == "PICKUP_NEEDED_LONG_RUN"
    assert slow["distance_basis"] == MODEL_REPORTED


def test_straight_line_is_labelled_as_a_lower_bound():
    r = assess(12.0, 420, 1600)
    assert r["distance_basis"] == STRAIGHT_LINE
    assert r["status"] == "PICKUP_NEEDED"


def test_count_matters_not_only_share():
    need = carless_need(180, 3000)  # 6% share, but 180 households
    assert need["needs_pickup_plan"] is True
    r = assess(30.0, 180, 3000)
    assert r["status"] == "PICKUP_NEEDED_LONG_RUN"


def test_small_carless_population_still_gets_named():
    r = assess(30.0, 20, 2000)
    assert r["status"] == "CAR_REACHABLE"
    assert "20 households" in r["recommendation"]


def test_missing_distance_is_no_destination_not_walkable():
    r = assess(None, 420, 1600)
    assert r["status"] == "NO_DESTINATION"
    assert r["walkable"] is False


def test_walkable_still_mentions_people_who_cannot_walk():
    r = assess(1.0, 420, 1600)
    assert r["status"] == "WALKABLE"
    assert "cannot walk" in r["recommendation"]
