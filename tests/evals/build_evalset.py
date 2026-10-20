"""Build readiness.evalset.json (ADK EvalSet) from cases.py. Run: .venv/bin/python tests/evals/build_evalset.py

Then: .venv/bin/adk eval agent/readiness_agent tests/evals/readiness.evalset.json --config_file_path tests/evals/test_config.json
"""

import json
from pathlib import Path

from google.adk.evaluation.eval_case import EvalCase, IntermediateData, Invocation, SessionInput
from google.adk.evaluation.eval_rubrics import Rubric, RubricContent
from google.adk.evaluation.eval_set import EvalSet
from google.genai import types

from cases import CASES, COUNTY, NEVER

HERE = Path(__file__).resolve().parent


def build() -> EvalSet:
    cases = []
    for c in CASES:
        rubrics = [Rubric(rubric_id=f"{c['id']}_{i}", rubric_content=RubricContent(text_property=f"The answer {name}."))
                   for i, name in enumerate(c["must"])]
        rubrics.append(Rubric(rubric_id=f"{c['id']}_never", rubric_content=RubricContent(
            text_property="The answer never says there are no accessible shelters when the field is merely blank.")))
        if c.get("refuse"):
            rubrics.append(Rubric(rubric_id=f"{c['id']}_refuse", rubric_content=RubricContent(
                text_property="The answer declines and calls no tools.")))
        inv = Invocation(
            invocation_id=c["id"],
            user_content=types.Content(role="user", parts=[types.Part(text=c["q"])]),
            intermediate_data=IntermediateData(tool_uses=[types.FunctionCall(name=t, args={}) for t in c["tools"][:1]]),
            rubrics=rubrics,
        )
        cases.append(EvalCase(eval_id=c["id"], conversation=[inv], session_input=SessionInput(
            app_name="readiness_agent", user_id="eval", state={"county_fips": COUNTY})))
    return EvalSet(eval_set_id="readiness", name="BUILD_SPEC section 17 question coverage",
                   description=f"Never-say patterns checked by run_evals.py: {NEVER}", eval_cases=cases)


if __name__ == "__main__":
    out = HERE / "readiness.evalset.json"
    out.write_text(build().model_dump_json(indent=1, exclude_none=True))
    (HERE / "test_config.json").write_text(json.dumps({"criteria": {
        "tool_trajectory_avg_score": {"threshold": 0.5, "match_type": "IN_ORDER"},
        "rubric_based_final_response_quality_v1": {"threshold": 0.8}}}, indent=1))
    print(f"wrote {out} with {len(CASES)} cases")
