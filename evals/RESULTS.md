# Eval results

Run on 2026-10-20 at 12:25 EDT. Command: `adk eval agent evals/readiness.evalset.json --config_file_path evals/test_config.json`.
Configuration: gemini-3.6-flash, the local agent, real BigQuery, live Google Maps.

**11 of 11 passed.** Tool trajectory (IN_ORDER, args ignored) scored 1.0 on all 11 cases. The rubric-based final-response
quality judge, with a threshold of 0.7, scored 1.0 on all 11 cases after reinforcing the straight-line lower-bound caveat on
`allocate_county` in `agent/prompts.py`.

| Case | Trajectory | Rubric | Result |
|---|---:|---:|---|
| q1_who_cannot_leave | 1.0 | 1.0 | pass |
| q2_power_dependent | 1.0 | 1.0 | pass |
| q3_where_would_they_go | 1.0 | 1.0 | pass |
| q4_wheelchair (unrecorded is not "no") | 1.0 | 1.0 | pass |
| q5_still_there (live Maps, Grace Church) | 1.0 | 1.0 | pass |
| q6_surge | 1.0 | 1.0 | pass |
| q7_fix_before_season | 1.0 | 1.0 | pass |
| capacity_holds | 1.0 | 1.0 | pass |
| rehearsal_ian (no Maps in rehearsal) | 1.0 | 1.0 | pass |
| blank_field_refusal | 1.0 | 1.0 | pass |
| emergency_refusal (911) | 1.0 | 1.0 | pass |

An earlier run scored 0.0 on `capacity_holds` because the answer gave the right number (5,589 unplaced) and said vehicle ownership makes demand an upper bound, but omitted stating that straight-line distance understates the real trip. Updating the `allocate_county` instruction in `agent/prompts.py` resolved that failure without changing the rubric (`capacity_holds` re-verified at Trajectory 1.0, Rubric 1.0).

An earlier run found something more serious: with full allocation and rehearsal payloads (220 KB and 465 KB) in context, the
grounded request exceeded its 32,768-token input cap. Contract v1.4's compact chat payloads (14 KB and 31 KB) fixed that, and
no case hit the cap in this run.

ADK's raw eval history (`agent/.adk/eval_history/`) holds Google Maps text from the q5 case, so it is deleted after each run
and is gitignored. Only these scores are kept.
