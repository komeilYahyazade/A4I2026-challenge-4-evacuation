# Evacuation Readiness Agent (A4I 2026, Challenge 4)

`BUILD_SPEC.md` is the single source of truth. Read it before changing anything; its section 3 rules
(three-state flags, freshness labels, no numbers from the model, Maps storage limits, planning only)
apply to every file.

## Layout
- `contract/schemas.py` + `contract/mocks/` – the v1 API contract (additive changes only).
- `sql/` – every query, with `{{dataset}}` and named `@params`. `00_shelter_evidence.sql` builds the
  derived tables (`shelter_evidence`, `tract_scores`, `care_evidence`, `shelter_places`); rebuild with
  `.venv/bin/python scripts/setup_bq.py` after any data reload.
- `agent/readiness_agent/` – ADK agents (`agent.py`), prompts, tools. Facts are one state key each
  (`tools/facts.py`) so parallel tool calls never race. `brief_plan.py` decides which ids and numbers a
  brief shows.
- `api/` – FastAPI: ADK routes + `/v1`. `briefs.py` assembles briefs (numbers from facts, words from the
  writer, guards against invented ids/numbers and over-generous verdicts). `chat.py` runs chat.
- `web/` – React console (Vite + TypeScript, Google Maps via `@vis.gl/react-google-maps`). `server.py`
  serves the build plus `/config.js` (Maps browser key from env) and forwards `/v1` to the private API
  with its service-account ID token. Deployed as Cloud Run `readiness-console`.
  Build: `cd web && PATH=$PWD/../.node/bin:$PATH npm run build` (Node is project-local in `.node/`).
- `frontend/` – the earlier Streamlit UI (`readiness-ui`), kept as a backup.

## Shared project: check before you deploy
The lab project is shared by the whole team. Before `gcloud run deploy`, run `gcloud run services list`
and never reuse a service name someone else created (`readiness-web`, `readiness-agent`, `*-rashif`
belong to teammates). Ours: `readiness-api`, `readiness-ui`, `readiness-console`.

## Commands
- Tests: `.venv/bin/python -m pytest -q`
- API: `MOCK_MODE=0 .venv/bin/python -m uvicorn api.main:app --port 8080`
- UI: `cd frontend && API_BASE_URL=http://localhost:8080 ../.venv/bin/streamlit run app.py`

## Gotchas found while building
- Gemini Maps grounding calls take 12-23 s each here (spec says 5-8 s); briefs take ~110 s.
- Never expose the MCP tools `get_job` / `get_table_info`: their declarations are ~300k chars and
  exhaust the Gemini per-minute token quota.
- ACS `median_income` uses -666666666 as a sentinel. Null it at the SQL boundary.
- Never commit real Maps responses as fixtures (rule 10). Test fixtures are synthetic.
