# frontend: Lee County evacuation readiness

A dark, map-first console for a county emergency manager. A four-step stepper in the header drives the whole UI, and each step
sets both the map layers and the panel:

1. **Who can't leave.** The KPIs come first: people without a car, recorded shelter spaces, and people with no seat within 15 km. A
   one-line takeaway follows, then the top 5 neighbourhoods ("Census tract 208.01"), then "Show all". The map shows need circles
   and numbered ranks.
2. **Where they'd go.** On first entry, two shelters are checked live on Google Maps (the one whose FEMA name says INACTIVE goes
   first). Each card says what happened in plain words, with the Google Maps sources directly under the claim. The map shows
   shelters plus thin pickup-run lines, which brighten when you hover a neighbourhood.
3. **Hurricane Ian rehearsal.** The NHC cone, the shelters taken offline and the re-routed runs. **No Google Maps calls**, and the panel
   says so.
4. **What to fix.** The one site that seats the most people, then the fix list and plan approval (approval is turned off on the public
   site), the field-assessment log, and "what we could not tell you".

The season strip under the header is context only: it shows where each step sits between October and November. Chat is an "Ask
the agent" drawer over the panel. Evidence details (the FEMA record, distance basis, flags, raw IDs) sit in a collapsible
"Evidence" section on each card. Each claim carries one evidence chip, and every chip opens the same labels explainer.

Design tokens live in `src/theme.ts` and the `:root` block of `src/styles.css`. Map states use different shapes and colours: a
hollow dashed dot is unrecorded, ✓ circle confirmed, ↻ renamed, ◆ unconfirmed, ✕ square dropped, ▲ offline. The legend lists
only the symbols currently on the map.

## How it talks to the agent

The UI only calls same-origin paths: `/api/*`, `/apps/*` (ADK sessions) and `/run_sse` (ADK chat). A proxy in front of it adds
the Google ID token that the **private** `readiness-agent` requires.

| Where | Proxy | Token |
|---|---|---|
| Dev (`npm run dev`) | Vite `server.proxy` sends the three paths to `VITE_AGENT_PROXY_TARGET` | `gcloud auth print-identity-token`, refreshed every 45 min |
| Cloud Run (`readiness-web`) | `server.mjs` sends the three paths to `AGENT_UPSTREAM` | Metadata server, with audience = `AGENT_UPSTREAM`. The service account `readiness-web@` has `run.invoker` on the agent |

The contract is in [`../contract/README.md`](../contract/README.md). The UI picks a renderer for each payload by `payload_type`, never by tool name.

## Run it

```bash
cd frontend
npm ci
# frontend/.env.local (gitignored):
#   VITE_MAPS_API_KEY=<browser key: Maps JavaScript API only, referrer-restricted>
#   VITE_AGENT_PROXY_TARGET=https://readiness-agent-tw6u3teu3q-uc.a.run.app   # or http://localhost:8000
npm run dev            # http://127.0.0.1:5173
```

To run without an agent, use `VITE_MODE=mock npm run dev`. It serves the agent lane's synthetic fixtures (`npm run sync-fixtures`
copies them from `contract/fixtures/`), and a MOCK DATA badge is always on screen. If a live call fails, the UI also falls back to
the fixtures and shows the badge, so nobody can mistake mock data for live data.

## Deploy

```bash
bash frontend/deploy.sh
```

This deploys `readiness-web` **private** (`--no-allow-unauthenticated`), grants `run.invoker` on the agent, and adds both Cloud Run
URL forms to the Maps key's allowed referrers. Making the web service public is a separate decision, and the command for it is
in the script header.

## Rules the UI enforces

- **Attribution.** Google Maps sources render immediately after the claim they support: each shelter's live check, each fix
  that was checked live, and each chat answer (from `groundingMetadata`, or from the tool results in the same turn). The words
  "Google Maps" are always `translate="no"`, never restyled or wrapped, and that includes inside agent text.
- **Unrecorded is never NO.** It gets its own grey "?" pin and striped bars, and the chips say so. A blank surge flag is shown as
  UNRECORDED, never as "outside the zone".
- **Two separate axes per shelter.** The pin fill shows wheelchair evidence (record or live) and the ring shows whether the building was
  confirmed live. A DROP is struck through, and a rename gets a dashed ring.
- **Every number carries its label:** RECORDED, UNRECORDED, VERIFIED LIVE, MODELED, COUNTY-LEVEL or APPROXIMATE. Distances
  say whether they are ROUTED or STRAIGHT-LINE.
- **Activate** is greyed out on purpose: Maps grounding is not permitted for emergency response.
