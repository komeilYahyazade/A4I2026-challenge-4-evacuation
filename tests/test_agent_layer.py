"""Unit and live integration tests for the Agent, Grounding, and Assessment layers.

Covers:
1. Active emergency refusal (911 guardrail).
2. Strict UNRECORDED vs NO distinction across BigQuery tools and system instructions.
3. Live Vertex AI Google Maps Grounding on a real Florida shelter (catching the
   'First Presbyterian Church of Maitland' -> 'Maitland Presbyterian Church' rename)
   and live routed travel time / road distance.
4. Conversational Shelter Assessment Agent catching the 'padlocked side-door ramp /
   custodian escort / lights-only generator' trap and persisting to BigQuery.
"""

from __future__ import annotations

import os
import unittest

# These tests call Vertex AI Gemini, Grounding with Google Maps and BigQuery for real, and test_05
# INSERTs a synthetic assessment (FL-TRAP-TEST-001) into evacuation_readiness.shelter_assessments on
# every run. Opt in explicitly so an ordinary `pytest` never spends money or writes fake rows.
LIVE = os.environ.get("RUN_LIVE_TESTS") == "1"

from agent.agent import FUNCTION_TOOLS, build_root_agent
from agent.assessment_agent import (
    NEEDS_FOLLOWUP_QUESTION,
    VERIFIED_ACCESSIBLE,
    VERIFIED_INACCESSIBLE_OR_RESTRICTED,
    evaluate_operator_statement,
    get_shelter_assessments,
)
from agent.grounding import (
    build_single_shelter_grounding_prompt,
    compute_grounded_route_with_maps,
    extract_maps_grounding_sources,
    format_google_maps_attribution,
    sanitize_for_storage,
    verify_place_with_google_maps,
)
from agent.prompts import SYSTEM_INSTRUCTION
from agent.tools import (
    ask_evacuation_agent,
    audit_county_shelters_with_live_maps,
    build_live_county_readiness_brief,
    submit_shelter_operator_assessment,
    verify_shelter_on_google_maps,
)



@unittest.skipUnless(LIVE, "live cloud calls and a BigQuery write; set RUN_LIVE_TESTS=1 to run")
class TestAgentAndGroundingLayer(unittest.TestCase):
    """Comprehensive tests for grounding.py, assessment_agent.py, tools.py, and agent.py."""

    def test_01_active_emergency_refusal_calls_911(self) -> None:
        """Active storm / rescue dispatch queries must be refused immediately and point to 911."""
        res = ask_evacuation_agent(
            "A hurricane is making landfall right now and water is rising, dispatch a rescue boat to 123 Main St",
            county_name="Miami-Dade",
        )
        self.assertTrue(res["refused_emergency"])
        self.assertEqual(res["mode"], "EMERGENCY_REFUSAL")
        self.assertIn("911", res["answer"])
        self.assertIn("Emergency Operations Center", res["answer"])

    def test_02_unrecorded_vs_no_distinction_in_prompts_and_tools(self) -> None:
        """UNRECORDED (blank) must never be conflated with NO in prompts or live BigQuery audits."""
        self.assertIn("UNRECORDED", SYSTEM_INSTRUCTION)
        self.assertIn("[RECORDED]", SYSTEM_INSTRUCTION)
        self.assertIn("[VERIFIED LIVE]", SYSTEM_INSTRUCTION)
        self.assertIn("[MODELED]", SYSTEM_INSTRUCTION)
        self.assertIn("[COUNTY-LEVEL]", SYSTEM_INSTRUCTION)

        audit = audit_county_shelters_with_live_maps(county_name="Lee", max_shelters=1)
        wc = audit["wheelchair_breakdown"]
        self.assertIn("yes", wc)
        self.assertIn("no", wc)
        self.assertIn("unrecorded", wc)
        self.assertGreater(wc["unrecorded"], 0)
        self.assertIn("NOT 'no'", wc["evidence_note"])
        self.assertIn('<span translate="no">Google Maps</span>', audit["attribution_html"])

    def test_03_single_shelter_prompt_and_storage_sanitization(self) -> None:
        """Grounding prompt targets one named building at one address; storage keeps only place_id/review_id."""
        prompt = build_single_shelter_grounding_prompt(
            shelter_name="First Presbyterian Church of Maitland",
            address="341 N Orlando Ave",
            city="Maitland",
            state="FL",
            origin_lat=28.625,
            origin_lon=-81.365,
        )
        self.assertIn("First Presbyterian Church of Maitland", prompt)
        self.assertIn("341 N Orlando Ave", prompt)
        self.assertIn("driving time", prompt)

        sanitized = sanitize_for_storage(
            {
                "place_id": "ChIJ7-mYxkpw54gRbahHchWi7tg",
                "title": "Maitland Presbyterian Church",
                "uri": "https://maps.google.com/maps?cid=15631609569644882029",
            }
        )
        self.assertEqual(sanitized, {"place_id": "ChIJ7-mYxkpw54gRbahHchWi7tg"})

        attr = format_google_maps_attribution(
            [{"title": "Maitland Presbyterian Church", "uri": "https://maps.google.com/maps?cid=1"}]
        )
        self.assertIn('<span translate="no">Google Maps</span>', attr)

    def test_04_live_google_maps_grounding_maitland_rename_and_routing(self) -> None:
        """Live Google Maps Grounding verifies a real Florida shelter, catches renames, and routes."""
        res = verify_place_with_google_maps(
            shelter_name="First Presbyterian Church of Maitland",
            address="341 N Orlando Ave",
            city="Maitland",
            state="FL",
            origin_lat=28.625,
            origin_lon=-81.365,
        )
        self.assertIsNone(res.get("error"))
        self.assertIs(res["exists"], True)
        self.assertTrue(res["confirmed_open"])
        self.assertTrue(res["name_differs"])
        self.assertIn("Maitland Presbyterian Church", str(res["current_name"]))
        self.assertTrue(len(res["sources"]) > 0)
        self.assertTrue(bool(res["sources"][0].get("place_id")))
        self.assertTrue(res["sources"][0].get("uri", "").startswith("https://"))
        self.assertIn('<span translate="no">Google Maps</span>', res["attribution_html"])
        for sup in res["grounding_supports"]:
            self.assertIsInstance(sup["start_index"], int)
            self.assertGreaterEqual(sup["start_index"], 0)

        tool_res = verify_shelter_on_google_maps(
            shelter_name="First Presbyterian Church of Maitland",
            address="341 N Orlando Ave",
            city="Maitland",
            state="FL",
        )
        self.assertEqual(tool_res["payload_type"], "shelter_maps_verification.v1")
        self.assertTrue(tool_res["name_differs"])

        route_res = compute_grounded_route_with_maps(
            origin_address_or_coords="28.625, -81.365",
            destination_name="First Presbyterian Church of Maitland",
            destination_address="341 N Orlando Ave",
            city="Maitland",
            state="FL",
        )
        self.assertIsNotNone(route_res["drive_minutes"])
        self.assertGreater(route_res["drive_minutes"], 0)
        self.assertIn('<span translate="no">Google Maps</span>', route_res["attribution_html"])

    def test_05_assessment_agent_catches_padlocked_ramp_trap_and_persists_to_bq(self) -> None:
        """Assessment Agent must reject padlocked/escort-only ramps and persist verified records to BigQuery."""
        trap_statement = (
            "We have a ramp at the side entrance, but the gate is padlocked after 5pm unless the custodian "
            "is called, and our backup generator only runs hallway lights, not HVAC or medical outlets."
        )
        trap_eval = submit_shelter_operator_assessment(
            shelter_id="FL-TRAP-TEST-001",
            shelter_name="Side Gate Community Shelter",
            county="Miami-Dade",
            operator_statement=trap_statement,
        )
        self.assertIs(trap_eval["wheelchair_unaccompanied_entries"], False)
        self.assertIs(trap_eval["ramp_unlocked_24_7"], False)
        self.assertIs(trap_eval["generator_powers_medical_outlets"], False)
        self.assertEqual(trap_eval["verified_status"], VERIFIED_INACCESSIBLE_OR_RESTRICTED)
        self.assertTrue(trap_eval["saved_to_bigquery"])

        accessible_statement = (
            "Our main entrance has a permanent level ramp with automatic doors unlocked 24/7 so wheelchair "
            "users can enter unaccompanied at any hour without assistance, and our diesel backup generator "
            "powers all HVAC and medical outlets."
        )
        ok_eval = evaluate_operator_statement(
            shelter_id="FL-OK-TEST-002",
            shelter_name="Accessible Regional High School",
            county="Miami-Dade",
            operator_statement=accessible_statement,
            use_llm=False,
        )
        self.assertIs(ok_eval["wheelchair_unaccompanied_entries"], True)
        self.assertIs(ok_eval["ramp_unlocked_24_7"], True)
        self.assertIs(ok_eval["generator_powers_medical_outlets"], True)
        self.assertEqual(ok_eval["verified_status"], VERIFIED_ACCESSIBLE)

        vague_eval = evaluate_operator_statement(
            shelter_id="FL-VAGUE-TEST-003",
            shelter_name="Vague Elementary School",
            county="Miami-Dade",
            operator_statement="We have a wheelchair ramp on the west side of the gym.",
            use_llm=False,
        )
        self.assertEqual(vague_eval["verified_status"], NEEDS_FOLLOWUP_QUESTION)
        self.assertIn("unaccompanied", vague_eval["followup_question"].lower())

        saved_rows = get_shelter_assessments(county="Miami-Dade", shelter_id="FL-TRAP-TEST-001", limit=5)
        self.assertTrue(len(saved_rows) >= 1)
        self.assertEqual(saved_rows[0]["verified_status"], VERIFIED_INACCESSIBLE_OR_RESTRICTED)

    def test_06_live_county_readiness_brief_and_agent_wiring(self) -> None:
        """build_live_county_readiness_brief returns all 4 brief sections and root_agent registers all tools."""
        brief = build_live_county_readiness_brief(county_name="Miami-Dade", verify_top_n_on_maps=1)
        self.assertEqual(brief["payload_type"], "live_county_readiness_brief.v1")
        self.assertIn("who_cannot_leave", brief)
        self.assertIn("where_they_would_go", brief)
        self.assertIn("what_to_fix_before_season", brief)
        self.assertIn("what_we_could_not_tell_you", brief)
        self.assertGreater(len(brief["who_cannot_leave"]["top_no_vehicle_tracts"]), 0)
        self.assertIn('<span translate="no">Google Maps</span>', brief["attribution_html"])

        root = build_root_agent()
        tool_names = [getattr(t, "name", None) or getattr(t, "__name__", "") for t in FUNCTION_TOOLS]
        for expected in (
            "verify_shelter_on_google_maps",
            "audit_county_shelters_with_live_maps",
            "build_live_county_readiness_brief",
            "submit_shelter_operator_assessment",
            "ask_evacuation_agent",
        ):
            self.assertIn(expected, tool_names)
        self.assertIsNotNone(root)


if __name__ == "__main__":
    unittest.main()
