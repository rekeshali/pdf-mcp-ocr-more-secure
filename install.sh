#!/usr/bin/env bash
# Install pdf-mcp-ocr as a Claude Code plugin under user scope.
# Idempotent: re-running replaces any prior install.

set -euo pipefail

PLUGIN_NAME="pdf-mcp-ocr"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS_DIR="$HOME/.claude/plugin-settings"
SETTINGS_FILE="$SETTINGS_DIR/$PLUGIN_NAME.json"

# ── Prereq checks ──────────────────────────────────────────────────────────

if ! command -v uv >/dev/null 2>&1; then
  echo "error: 'uv' not found in PATH. Install from https://docs.astral.sh/uv/ or via 'pip install uv'." >&2
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "error: 'claude' CLI not found in PATH" >&2
  exit 1
fi

if ! command -v tesseract >/dev/null 2>&1; then
  echo "warning: 'tesseract' binary not found. The OCR fallback will fail gracefully" >&2
  echo "         at runtime, but scanned PDFs won't get OCR'd until you install it:" >&2
  echo "           macOS:  brew install tesseract" >&2
  echo "           Debian: sudo apt install tesseract-ocr" >&2
fi

# ── Sync Python deps from the frozen lockfile ──────────────────────────────

echo "Syncing Python dependencies (frozen lockfile, OCR extra)..."
(cd "$REPO_DIR" && uv sync --frozen --extra ocr) >/dev/null

# ── Drop a template user config on first install only ──────────────────────

mkdir -p "$SETTINGS_DIR"
if [ ! -f "$SETTINGS_FILE" ]; then
  cat > "$SETTINGS_FILE" <<'JSON'
{
  "_comment": "Allow/deny lists for pdf-mcp-ocr. Empty arrays = permissive. Deny always wins.",
  "_docs": "Path patterns are shell globs (fnmatch). URL patterns match the hostname only; '*' matches any chars including dots.",
  "_ssrf_floor": "The built-in SSRF block list (loopback, link-local, RFC 1918, IPv6 ULA) is ALWAYS enforced on URLs regardless of this file.",
  "_https_floor": "Only https:// URL sources are accepted. http://, file://, data: are rejected at validation time.",

  "path": {
    "allow": [],
    "deny": []
  },
  "url": {
    "allow": [],
    "deny": []
  }
}
JSON
  echo "Created default config: $SETTINGS_FILE"
fi

# ── Register plugin (idempotent) ───────────────────────────────────────────

claude plugin uninstall "$PLUGIN_NAME" --scope user >/dev/null 2>&1 || true
claude plugin install "$REPO_DIR" --scope user

echo
echo "Installed '$PLUGIN_NAME' plugin (user scope) from $REPO_DIR"
echo "Config:  $SETTINGS_FILE"
echo "Verify:  claude plugin list"
echo "In a Claude session, run:  /mcp"
