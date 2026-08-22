#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "${1:-}" = "--build" ]; then
  docker compose -f "$project_root/compose.yaml" up -d --build
else
  docker compose -f "$project_root/compose.yaml" up -d
fi
