#!/usr/bin/env bash
# Disable the pdf-mcp-ocr plugin without uninstalling. Re-enable with ./enable.sh.

set -euo pipefail

PLUGIN_NAME="pdf-mcp-ocr"

if ! command -v claude >/dev/null 2>&1; then
  echo "error: 'claude' CLI not found in PATH" >&2
  exit 1
fi

claude plugin disable "$PLUGIN_NAME" --scope user

echo "Disabled '$PLUGIN_NAME' plugin. Run ./enable.sh to turn it back on."
