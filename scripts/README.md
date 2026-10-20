# scripts/

Headless alternatives to the notebook, for when a Colab Enterprise runtime is slow or
unavailable.

`load.sh` reaches the same end state as `notebooks/c4_01_load_explore.ipynb`—the same tables, in
the same dataset, in your project. Use the notebook if you can: it explains what it's doing and
why, and the explanations matter. Use this if you can't.

## Running it

From the **root of the repo**, in Cloud Shell:

```bash
bash scripts/load.sh              # loads Florida, the default
bash scripts/load.sh --list       # show every state we've published
bash scripts/load.sh --help       # usage
```

Invoke it with `bash` rather than `./scripts/load.sh`. That way it works whether or not the
file's executable bit survived however your copy of this repo was created.

## It is safe to run more than once

Every table is fully replaced, never appended to. So if you cancel it halfway through, or you
aren't sure whether it finished, just run it again—the end state is the same either way.

## Deploying (agent lane)

```bash
GOOGLE_CLOUD_PROJECT=<project> bash scripts/deploy_toolbox.sh            # prints MCP_TOOLBOX_URL
GOOGLE_CLOUD_PROJECT=<project> MCP_TOOLBOX_URL=<url> bash scripts/deploy_agent.sh
```

Both services are private and keep one warm instance, so the first request on stage does not pay
a cold start. `docs/agent.md` covers running locally, the tools, and the design decisions.
