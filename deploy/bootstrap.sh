#!/usr/bin/env bash
# One-command setup on the always-on PC (Ubuntu or Ubuntu on WSL): get the code, copy the private
# engine home, and install the scheduler. Nothing is deployed or published.
#
#   bash deploy/bootstrap.sh <home-source>
#   curl -fsSL https://raw.githubusercontent.com/tomnotthomas/growth-engine/main/deploy/bootstrap.sh | bash -s -- <home-source>
#
# <home-source> is where the private home comes from: a local folder, or an rsync/ssh source such as
# user@laptop:/path/to/home. It is copied to ~/growth-home without its state/ and dist/ folders.
set -euo pipefail

SOURCE="${1:?usage: bootstrap.sh <home-source: local path or user@host:/path>}"
REPO_URL="${GROWTH_REPO_URL:-https://github.com/tomnotthomas/growth-engine.git}"
REPO="$HOME/growth-engine"
HOME_DIR="${GROWTH_HOME:-$HOME/growth-home}"

command -v git >/dev/null || { echo "install git first: sudo apt install git" >&2; exit 1; }
command -v rsync >/dev/null || { echo "install rsync first: sudo apt install rsync" >&2; exit 1; }

if [ -d "$REPO/.git" ]; then
  git -C "$REPO" pull --ff-only
else
  git clone "$REPO_URL" "$REPO"
fi

mkdir -p "$HOME_DIR"
rsync -a --exclude state/ --exclude dist/ "${SOURCE%/}/" "$HOME_DIR/"
chmod 700 "$HOME_DIR"

"$REPO/deploy/install-wsl.sh" "$HOME_DIR"
cat <<MSG

Done. On Windows, run once in an elevated PowerShell so WSL starts with the PC:
  powershell -ExecutionPolicy Bypass -File \\\\wsl\$\\Ubuntu\\home\\$USER\\growth-engine\\deploy\\windows\\keep-wsl-running.ps1 -User $USER
MSG
