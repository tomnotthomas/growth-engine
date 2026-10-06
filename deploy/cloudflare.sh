#!/usr/bin/env bash
# Deploy one project now: build and check it locally; upload and promote it to production only
# when the project says [deploy] launched = true (the owner's launch go).
# The scheduled deploy job does the same on its own; this is the by-hand path.
# Usage: GROWTH_HOME=/path/to/home deploy/cloudflare.sh <project>
set -euo pipefail

PROJECT="${1:?usage: deploy/cloudflare.sh <project>}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
exec python3 -m growth deploy "$PROJECT"
