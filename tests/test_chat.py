"""Chat helpers: refusal marker, attribution of prose built from verify_shelter, MCP filter."""

from api import chat


def test_refused_marker():
    assert chat.REFUSED.match("REFUSED: This is planning only.")
    assert chat.REFUSED.match("**REFUSED:** no")
    assert not chat.REFUSED.match("Not refused: here is the answer.")


def test_attribute_cites_maps_paragraphs_only():
    v = {"maps_name": "Caloosa Sound Convention Center", "record_name": "Harborside Events Center",
         "sources": [{"id": "src_1", "provider": "Google Maps", "title": "Caloosa", "uri": "u", "place_id": "P1"},
                     {"id": "src_2", "provider": "Google Maps", "title": "route", "uri": "u2", "place_id": None}]}
    text = ("Google Maps lists it as the Caloosa Sound Convention Center.\n\n"
            "FEMA never recorded wheelchair access for this building.")
    segs, sources = chat._attribute(text, [v], 1)
    assert [s["id"] for s in sources] == ["src_1", "src_2"]
    assert segs[0] == {"text": text.split("\n\n")[0], "source_ids": ["src_1"], "freshness": "live"}
    assert segs[1]["source_ids"] == [] and segs[1]["freshness"] == "historical"


def test_mcp_is_read_only_and_small():
    from readiness_agent.tools import mcp
    assert "execute_sql" not in mcp.READ_ONLY_TOOLS and "cancel_job" not in mcp.READ_ONLY_TOOLS
    assert "get_job" not in mcp.READ_ONLY_TOOLS  # its ~235k-char declaration exhausts the token quota
