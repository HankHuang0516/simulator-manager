#!/bin/sh
set -eu

REPOSITORY="HankHuang0516/simulator-manager"
ARCHIVE_URL="https://github.com/${REPOSITORY}/archive/refs/heads/main.zip"
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/simulator-manager.XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT HUP INT TERM

echo "Downloading Simulator Manager Tool…"
curl -fsSL "$ARCHIVE_URL" -o "$TMP_ROOT/source.zip"
ditto -x -k "$TMP_ROOT/source.zip" "$TMP_ROOT/source"
SOURCE_DIR="$TMP_ROOT/source/simulator-manager-main"

set -- --skill-dir "$HOME/.agents/skills"
if [ -f "$HOME/.local/share/simulator-manager/.simulator-manager-install" ]; then
  set -- "$@" --upgrade
fi
python3 "$SOURCE_DIR/install.py" "$@"

if command -v codex >/dev/null 2>&1; then
  if ! codex plugin marketplace list | awk 'NR > 1 {print $1}' | grep -qx 'hank-tools'; then
    codex plugin marketplace add "$REPOSITORY" --ref main
  else
    codex plugin marketplace upgrade hank-tools
  fi
  codex plugin add simulator-manager@hank-tools
  echo "Simulator Manager Codex Tool installed."
else
  echo "CLI and Skill installed. Install the Codex app/CLI, then run:"
  echo "  codex plugin marketplace add $REPOSITORY --ref main"
  echo "  codex plugin add simulator-manager@hank-tools"
fi

echo "Creating the Simulator Manager application and opening Quick Start…"
if "$HOME/.local/bin/sim-manager" ui --install-app --onboarding --json; then
  echo "Dashboard entry: $HOME/Applications/Simulator Manager.app"
  echo "The dashboard also remains available from the macOS menu bar."
else
  echo "The CLI and Codex Tool are installed, but the native dashboard could not be built." >&2
  echo "Install Xcode Command Line Tools, then run: sim-manager ui --install-app --onboarding" >&2
fi

echo "Next: start a new Codex task and say: Use simulator-manager for this project."
echo "Codex will register the task, run host checks first, and use managed runtime/UI requests only when needed."
