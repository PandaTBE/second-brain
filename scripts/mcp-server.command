#!/bin/sh
set -eu
project_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
cd -- "$project_dir"
exec "$project_dir/.venv/bin/python" "$project_dir/brain_settings/mcp_server.py" "$@"
