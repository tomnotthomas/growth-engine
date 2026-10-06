#!/usr/bin/env bash
# Deploy one project's site and waitlist to Cloudflare (Workers static assets + D1, free tier).
# THIS MAKES THE SITE PUBLIC. It asks for the project id as confirmation unless CONFIRM=<project>.
# Usage: GROWTH_HOME=/path/to/home deploy/cloudflare.sh <project>
set -euo pipefail

PROJECT="${1:?usage: deploy/cloudflare.sh <project>}"
HOME_DIR="${GROWTH_HOME:?set GROWTH_HOME to the private engine home}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WRANGLER="npx --yes wrangler@4"

if [ "${CONFIRM:-}" != "$PROJECT" ]; then
  read -r -p "Deploying '$PROJECT' makes it public on Cloudflare. Type the project id to continue: " answer
  [ "$answer" = "$PROJECT" ] || { echo "aborted" >&2; exit 1; }
fi

cd "$REPO"
python3 -m growth --home "$HOME_DIR" build "$PROJECT"
OUT="$(python3 - "$HOME_DIR" "$PROJECT" <<'PY'
import sys
from pathlib import Path
from growth.config import load_engine
engine = load_engine(Path(sys.argv[1]))
project = engine.projects[sys.argv[2]]
if "waitlist" in project.raw and project.raw["waitlist"].get("email_provider", "log") == "log":
    sys.exit("[waitlist] email_provider is 'log' (local development only); set it to 'brevo' or 'resend' before deploying")
print(engine.dist_dir / project.site.get("out", project.id))
PY
)"
cd "$OUT"
$WRANGLER whoami >/dev/null || { echo "log in first: $WRANGLER login" >&2; exit 1; }

DB_NAME="$(sed -n 's/^database_name = "\(.*\)"/\1/p' wrangler.toml)"
if grep -q 'database_id = "SET-BY-deploy/cloudflare.sh"' wrangler.toml; then
  ID="$($WRANGLER d1 list --json | python3 -c "import json,sys; print(next((d['uuid'] for d in json.load(sys.stdin) if d['name']=='$DB_NAME'), ''))")"
  if [ -z "$ID" ]; then
    $WRANGLER d1 create "$DB_NAME"
    ID="$($WRANGLER d1 list --json | python3 -c "import json,sys; print(next(d['uuid'] for d in json.load(sys.stdin) if d['name']=='$DB_NAME'))")"
  fi
  python3 - "$HOME_DIR/projects/$PROJECT/project.toml" "$ID" <<'PY'
import re, sys
path, db_id = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
line = f'd1_database_id = "{db_id}"'
if re.search(r'^d1_database_id\s*=', text, flags=re.M):
    text = re.sub(r'^d1_database_id\s*=.*$', line, text, count=1, flags=re.M)
elif re.search(r'^\[waitlist\]\s*$', text, flags=re.M):
    text = re.sub(r'^\[waitlist\]\s*$', "[waitlist]\n" + line, text, count=1, flags=re.M)
else:
    sys.exit(f"{path} has no [waitlist] table; add one before deploying")
open(path, "w", encoding="utf-8").write(text)
PY
  echo "Recorded D1 database $ID in projects/$PROJECT/project.toml; rebuilding."
  cd "$REPO" && python3 -m growth --home "$HOME_DIR" build "$PROJECT" && cd "$OUT"
  if grep -q 'SET-BY-deploy/cloudflare.sh' wrangler.toml; then
    echo "wrangler.toml still has no D1 database id after the rebuild; check [waitlist] d1_database_id" >&2
    exit 1
  fi
fi

$WRANGLER d1 execute "$DB_NAME" --remote --file schema.sql
SECRETS="$($WRANGLER secret list 2>/dev/null || echo '[]')"
for name in EMAIL_API_KEY STATS_TOKEN HASH_SALT; do
  if ! grep -q "\"$name\"" <<<"$SECRETS"; then
    echo "missing Worker secret $name; set it with: (cd $OUT && $WRANGLER secret put $name)" >&2
    exit 1
  fi
done
$WRANGLER deploy
echo "Deployed $PROJECT. Point the domain at the Worker in the Cloudflare dashboard (Workers > Domains)."
