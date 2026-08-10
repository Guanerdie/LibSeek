#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ ! -f "$project_root/.env" ]; then
  cp "$project_root/.env.example" "$project_root/.env"
  echo '已创建 .env。请先修改 PostgreSQL 密码；如需发现任务，再填写 NextFind 运行时凭据。' >&2
  exit 1
fi

docker compose --project-directory "$project_root" config --quiet
docker compose --project-directory "$project_root" up -d --build
docker compose --project-directory "$project_root" ps
echo '前端：http://127.0.0.1:8080  API：http://127.0.0.1:8000/api/docs'

