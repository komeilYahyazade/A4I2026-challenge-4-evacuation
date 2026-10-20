import re
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "toolbox" / "tools.yaml"


def _docs():
    return [d for d in yaml.safe_load_all(CONFIG.read_text()) if d]


def test_multi_document_v1_format():
    docs = _docs()
    kinds = {d["kind"] for d in docs}
    assert kinds == {"source", "tool", "toolset"}
    for d in docs:
        assert "name" in d


def test_tools_bind_declared_parameters_and_toolset_is_complete():
    docs = _docs()
    tools = {d["name"]: d for d in docs if d["kind"] == "tool"}
    sources = {d["name"] for d in docs if d["kind"] == "source"}
    toolset = next(d for d in docs if d["kind"] == "toolset")
    assert toolset["name"] == "evacuation_readiness"
    assert set(toolset["tools"]) == set(tools)
    for name, tool in tools.items():
        assert tool["type"] == "bigquery-sql" and tool["source"] in sources
        used = set(re.findall(r"@(\w+)", tool["statement"]))
        declared = {p["name"] for p in tool.get("parameters", [])}
        assert used == declared, name
        assert "org_poc" not in tool["statement"]


def test_source_is_read_only():
    source = next(d for d in _docs() if d["kind"] == "source")
    assert source["type"] == "bigquery" and source["writeMode"] == "blocked"
