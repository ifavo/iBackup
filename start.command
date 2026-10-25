#!/bin/zsh
set -eu
cd "${0:A:h}"
export UV_LINK_MODE=copy
if command -v uv >/dev/null 2>&1; then
  exec uv run --frozen --python 3.12 python -m ibackup "$@"
fi
if [[ -x /opt/homebrew/bin/uv ]]; then
  exec /opt/homebrew/bin/uv run --frozen --python 3.12 python -m ibackup "$@"
fi
print "Zum Starten brauchst du uv: https://docs.astral.sh/uv/getting-started/installation/"
read '?Enter zum Schließen.'
