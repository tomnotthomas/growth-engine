#!/usr/bin/env bash
# Install growth-engine as user-level systemd units on Ubuntu (also on WSL with systemd enabled):
# the scheduler timer, the self-update timer and the control API for the Mac app (127.0.0.1 only).
# Usage: deploy/install-wsl.sh /path/to/private/home
# The repository must be cloned at ~/growth-engine. Nothing is deployed or published by this script.
set -euo pipefail

HOME_DIR="${1:-${GROWTH_HOME:-$HOME/growth-home}}"
REPO="$HOME/growth-engine"

[ -d "$REPO/growth" ] || { echo "clone the repository to $REPO first" >&2; exit 1; }
[ -f "$HOME_DIR/engine.toml" ] || { echo "no engine.toml in $HOME_DIR (copy examples/home there and adjust)" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "needs Python 3.11 or newer" >&2; exit 1; }
command -v claude >/dev/null || echo "warning: claude is not on PATH; AI jobs (the digest) will fall back to plain text" >&2
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "note: ANTHROPIC_API_KEY is set in this shell; the engine strips it, so Claude always uses the subscription" >&2
fi
if [ "$(ps -p 1 -o comm=)" != "systemd" ]; then
  echo "systemd is not running. On WSL add to /etc/wsl.conf:  [boot]  systemd=true  then run 'wsl --shutdown' in Windows." >&2
  echo "Or use cron instead:  */5 * * * * cd $REPO && GROWTH_HOME=$HOME_DIR python3 -m growth tick >> $HOME_DIR/tick.log 2>&1" >&2
  exit 1
fi

mkdir -p "$HOME/.config/growth-engine" "$HOME/.config/systemd/user"
umask 077
# Rewrite only the two managed lines; secrets the operator added stay in place on every rerun.
ENV_FILE="$HOME/.config/growth-engine/env"
touch "$ENV_FILE"
{
  grep -v -e '^GROWTH_HOME=' -e '^PATH=' "$ENV_FILE" || true
  echo "GROWTH_HOME=$HOME_DIR"
  echo "PATH=$REPO/.venv/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
} > "$ENV_FILE.new"
chmod 600 "$ENV_FILE.new"
mv "$ENV_FILE.new" "$ENV_FILE"
if grep -qE '^[A-Z0-9_]*(TOKEN|KEY|SECRET|WEBHOOK)[A-Z0-9_]*=' "$ENV_FILE"; then
  echo "warning: $ENV_FILE holds secrets in plain text; move each into the encrypted store with" >&2
  echo "  python3 -m growth secret set <NAME>   and delete the line (see SECURITY.md)" >&2
fi
echo "Secrets (CLOUDFLARE_API_TOKEN, <PROJECT>_STATS_TOKEN, PostHog keys, the digest webhook) go into the"
echo "encrypted store: cd $REPO && python3 -m growth secret set <NAME>. Never into the repo or the env file."

for unit in growth-engine.service growth-engine.timer growth-update.service growth-update.timer growth-app.service; do
  cp "$REPO/deploy/systemd/$unit" "$HOME/.config/systemd/user/"
done
systemctl --user daemon-reload
systemctl --user enable --now growth-engine.timer
if [ "${GROWTH_AUTO_UPDATE:-1}" = "1" ]; then
  systemctl --user enable --now growth-update.timer
else
  echo "Auto-update left off (GROWTH_AUTO_UPDATE=0); run 'python3 -m growth self-update' by hand."
fi
systemctl --user enable --now growth-app.service
loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER"

(cd "$REPO" && GROWTH_HOME="$HOME_DIR" python3 -m growth check)
(cd "$REPO" && GROWTH_HOME="$HOME_DIR" python3 -m growth app-token >/dev/null)
systemctl --user list-timers 'growth-*' --no-pager
echo "Installed. Logs: journalctl --user -u growth-engine.service (scheduler), -u growth-update.service, -u growth-app.service"
echo "The Mac app connects with: ssh geekom-wsl (the control API listens on 127.0.0.1:8765 only)."
