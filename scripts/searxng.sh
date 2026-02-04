#!/usr/bin/env bash
# Install and run a private SearXNG instance for Mai, without Docker.
#
#   scripts/searxng.sh setup    clone SearXNG, create its virtualenv, write settings
#   scripts/searxng.sh run      start it on http://127.0.0.1:8888 (foreground)
#   scripts/searxng.sh update   pull the latest SearXNG and reinstall dependencies
#
# Everything lives under data/searxng/ (git-ignored). The instance listens on
# localhost only, has the JSON API enabled, and has the rate limiter off, which
# is only appropriate because it is not reachable from the network.
#
# Override the location or port with MAI_SEARXNG_DIR and MAI_SEARXNG_PORT.
# See docs/guide/search.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${MAI_SEARXNG_DIR:-$ROOT/data/searxng}"
PORT="${MAI_SEARXNG_PORT:-8888}"
REPO="https://github.com/searxng/searxng"
SETTINGS="$DIR/settings.yml"

install_deps() {
  uv venv --quiet --allow-existing --python 3.12 "$DIR/venv"
  uv pip install --quiet --python "$DIR/venv/bin/python" -r "$DIR/src/requirements.txt"
}

setup() {
  mkdir -p "$DIR"
  if [ ! -d "$DIR/src/.git" ]; then
    git clone --quiet --depth 1 "$REPO" "$DIR/src"
  fi
  install_deps
  if [ ! -f "$SETTINGS" ]; then
    cat > "$SETTINGS" <<EOF
# SearXNG settings for Mai. Reference: https://docs.searxng.org/admin/settings/
use_default_settings: true

general:
  instance_name: "Mai local search"

server:
  port: $PORT
  bind_address: "127.0.0.1"
  secret_key: "$(openssl rand -hex 32)"
  limiter: false
  public_instance: false

search:
  safe_search: 0
  formats:
    - html
    - json
EOF
  fi
  echo "SearXNG is set up in $DIR. Start it with: scripts/searxng.sh run"
}

run() {
  [ -x "$DIR/venv/bin/python" ] || { echo "Run 'scripts/searxng.sh setup' first." >&2; exit 1; }
  cd "$DIR/src"
  SEARXNG_SETTINGS_PATH="$SETTINGS" exec "$DIR/venv/bin/python" -m searx.webapp
}

update() {
  git -C "$DIR/src" pull --quiet --depth 1
  install_deps
  echo "SearXNG updated. Restart it to use the new version."
}

case "${1:-}" in
  setup) setup ;;
  run) run ;;
  update) update ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
