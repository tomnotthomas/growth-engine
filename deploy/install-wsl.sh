#!/usr/bin/env bash
# Install growth-engine as a user-level systemd timer on Ubuntu (also on WSL with systemd enabled).
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
  echo "PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
} > "$ENV_FILE.new"
chmod 600 "$ENV_FILE.new"
mv "$ENV_FILE.new" "$ENV_FILE"
echo "Put secrets (GROWTH_DIGEST_WEBHOOK, <PROJECT>_STATS_TOKEN, PostHog keys) into ~/.config/growth-engine/env, never into the repo."

cp "$REPO/deploy/systemd/growth-engine.service" "$REPO/deploy/systemd/growth-engine.timer" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now growth-engine.timer
loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER"

(cd "$REPO" && GROWTH_HOME="$HOME_DIR" python3 -m growth check)
systemctl --user list-timers growth-engine.timer --no-pager
echo "Installed. Logs: journalctl --user -u growth-engine.service"
