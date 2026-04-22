#!/usr/bin/env bash
set -euo pipefail

APP_NAME="codex-history"
TARGET_ROOT="${HOME}/.local/share/${APP_NAME}"
TARGET_BIN_DIR="${HOME}/.local/bin"
TARGET_CLI="${TARGET_ROOT}/codex_history.py"
TARGET_WRAPPER="${TARGET_BIN_DIR}/${APP_NAME}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd 2>/dev/null || true)"
LOCAL_SOURCE="${SCRIPT_DIR}/src/codex_history/cli.py"

mkdir -p "${TARGET_ROOT}" "${TARGET_BIN_DIR}"

if command -v python3 >/dev/null 2>&1; then
  :
else
  echo "python3 is required but was not found." >&2
  exit 1
fi

if [[ -f "${LOCAL_SOURCE}" ]]; then
  cp "${LOCAL_SOURCE}" "${TARGET_CLI}"
elif [[ -n "${CODEX_HISTORY_CLI_URL:-}" ]]; then
  curl -fsSL "${CODEX_HISTORY_CLI_URL}" -o "${TARGET_CLI}"
else
  cat >&2 <<'EOF'
No local source checkout detected.

To use install.sh in curl|bash mode, set CODEX_HISTORY_CLI_URL to a raw URL for cli.py.

Example:
  curl -fsSL https://YOUR-DOMAIN/install.sh | \
    CODEX_HISTORY_CLI_URL=https://YOUR-DOMAIN/codex_history/cli.py bash
EOF
  exit 1
fi

cat > "${TARGET_WRAPPER}" <<'EOF'
#!/usr/bin/env bash
exec python3 "$HOME/.local/share/codex-history/codex_history.py" --serve "$@"
EOF

chmod +x "${TARGET_WRAPPER}"

echo "Installed ${APP_NAME}"
echo "CLI script: ${TARGET_CLI}"
echo "Wrapper:    ${TARGET_WRAPPER}"
echo
echo "If '${APP_NAME}' is not found immediately, add ~/.local/bin to PATH or restart your shell."
