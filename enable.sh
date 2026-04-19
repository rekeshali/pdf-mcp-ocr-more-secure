#!/usr/bin/env bash
# Re-enable the pdf-mcp-ocr plugin (must already be installed).

set -euo pipefail

PLUGIN_NAME="pdf-mcp-ocr"

if ! command -v claude >/dev/null 2>&1; then
  echo "error: 'claude' CLI not found in PATH" >&2
  exit 1
fi

claude plugin enable "$PLUGIN_NAME" --scope user

echo "Enabled '$PLUGIN_NAME' plugin."
