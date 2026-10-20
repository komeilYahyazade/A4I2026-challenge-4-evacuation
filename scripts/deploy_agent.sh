#!/usr/bin/env bash
# Moved to deploy/deploy_agent.sh (agent lane). Kept so existing docs keep working.
exec bash "$(dirname "$0")/../deploy/deploy_agent.sh" "$@"
