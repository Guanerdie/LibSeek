#!/usr/bin/env sh
set -eu

validate_only=false
case "${1-}" in
  '') ;;
  --validate-only)
    validate_only=true
    shift
    ;;
  *)
    echo '用法：scripts/start.sh [--validate-only]' >&2
    exit 2
    ;;
esac
if [ "$#" -ne 0 ]; then
  echo '用法：scripts/start.sh [--validate-only]' >&2
  exit 2
fi

project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
env_file="$project_root/.env"
example_file="$project_root/.env.example"
compose_file="$project_root/compose.yaml"

if [ ! -f "$env_file" ]; then
  cp "$example_file" "$env_file"
  echo '已创建 .env。请先修改 PostgreSQL 密码并填写本地认证配置。' >&2
  exit 1
fi

dotenv_value() {
  key=$1
  sed -n "s/^${key}=//p" "$env_file" | tail -n 1 | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

dotenv_interpolation_names=$(
  awk -F= '
    /^[A-Z][A-Z0-9_]*=/ {
      value = substr($0, index($0, "=") + 1)
      if (value ~ /\$[A-Za-z_]/ || value ~ /\$\{[A-Za-z_]/) {
        print $1
      }
    }
  ' "$env_file" | sort -u
)
if [ -n "$dotenv_interpolation_names" ]; then
  echo '拒绝 .env 中的变量插值；以下键必须直接填写字面值：' >&2
  printf '%s\n' "$dotenv_interpolation_names" | sed 's/^/  - /' >&2
  exit 1
fi

process_override_names=$(
  env | sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*$/\1/p' | while IFS= read -r name; do
    case "$name" in
      COMPOSE_*) printf '%s\n' "$name" ;;
      *)
        if grep -q "^${name}=" "$example_file" || grep -Eq "\\\$\\{${name}([}:])" "$compose_file"; then
          printf '%s\n' "$name"
        fi
        ;;
    esac
  done | sort -u
)
if [ -n "$process_override_names" ]; then
  echo '拒绝父进程覆盖启动配置。请清除以下环境变量，并只在 .env 中配置：' >&2
  printf '%s\n' "$process_override_names" | sed 's/^/  - /' >&2
  exit 1
fi

compose_file_override_names=$(
  sed -n 's/^\(COMPOSE_[A-Z0-9_]*\)=.*$/\1/p' "$env_file" | sort -u | while IFS= read -r name; do
    if [ -n "$(dotenv_value "$name")" ]; then
      printf '%s\n' "$name"
    fi
  done
)
if [ -n "$compose_file_override_names" ]; then
  echo '拒绝 .env 中的 Compose 控制变量：' >&2
  printf '%s\n' "$compose_file_override_names" | sed 's/^/  - /' >&2
  exit 1
fi

postgres_password=$(dotenv_value POSTGRES_PASSWORD)
database_url=$(dotenv_value DATABASE_URL)
if [ -z "$postgres_password" ] || [ -z "$database_url" ] || printf '%s' "$postgres_password" | grep -q 'change-me-before-production' || printf '%s' "$database_url" | grep -q 'change-me-before-production'; then
  echo '拒绝使用示例 PostgreSQL 密码启动；请同时修改 .env 中的 POSTGRES_PASSWORD 和 DATABASE_URL。' >&2
  exit 1
fi

auth_username=$(dotenv_value AUTH_LOCAL_USERNAME)
auth_password=$(dotenv_value AUTH_LOCAL_PASSWORD)
auth_signing_key=$(dotenv_value AUTH_SESSION_SIGNING_KEY)
if [ -z "$auth_username" ] || [ -z "$auth_password" ] || [ "${#auth_signing_key}" -lt 32 ]; then
  echo '本地启动需要 AUTH_LOCAL_USERNAME、AUTH_LOCAL_PASSWORD 和至少 32 个字符的 AUTH_SESSION_SIGNING_KEY。' >&2
  exit 1
fi

docker compose --project-directory "$project_root" --env-file "$env_file" -f "$compose_file" config --quiet
if [ "$validate_only" = true ]; then
  echo 'Docker Compose 配置有效；未启动任何容器。'
  exit 0
fi

docker compose --project-directory "$project_root" --env-file "$env_file" -f "$compose_file" up -d --build --wait --wait-timeout 180
docker compose --project-directory "$project_root" --env-file "$env_file" -f "$compose_file" ps
echo '前端：http://127.0.0.1:8080  API：http://127.0.0.1:8000/api/docs'
