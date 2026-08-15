#!/usr/bin/env sh
set -eu

usage() {
  echo '用法：scripts/start.sh --docker-context <名称> [--validate-only]' >&2
}

docker_context=''
docker_context_set=false
validate_only=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --docker-context)
      shift
      if [ "$#" -eq 0 ] || [ "$docker_context_set" = true ]; then
        usage
        exit 2
      fi
      docker_context=$1
      docker_context_set=true
      ;;
    --validate-only)
      validate_only=true
      ;;
    *)
      usage
      exit 2
      ;;
  esac
  shift
done
if [ "$docker_context_set" != true ] || [ -z "$docker_context" ] || [ "${#docker_context}" -gt 128 ]; then
  echo 'Docker 启动必须通过 --docker-context 显式选择安全格式的 context 名称。' >&2
  exit 2
fi
case "$docker_context" in
  [A-Za-z0-9]*) ;;
  *)
    echo 'Docker context 名称格式无效。' >&2
    exit 2
    ;;
esac
case "$docker_context" in
  *[!A-Za-z0-9_.-]*)
    echo 'Docker context 名称格式无效。' >&2
    exit 2
    ;;
esac

project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
env_file="$project_root/.env"
example_file="$project_root/.env.example"
compose_file="$project_root/compose.yaml"

process_override_names=$(
  env | sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*$/\1/p' | while IFS= read -r name; do
    case "$name" in
      COMPOSE_*) printf '%s\n' "$name" ;;
      DOCKER_HOST|DOCKER_CONTEXT|DOCKER_TLS_VERIFY|DOCKER_CERT_PATH|DOCKER_CONFIG)
        printf '%s\n' "$name"
        ;;
      *)
        if grep -q "^${name}=" "$example_file" || grep -Eq "\\\$\\{${name}([}:])" "$compose_file"; then
          printf '%s\n' "$name"
        fi
        ;;
    esac
  done | LC_ALL=C sort -u
)
if [ -n "$process_override_names" ]; then
  echo '拒绝父进程覆盖启动配置。请清除以下环境变量，并只在 .env 中配置：' >&2
  printf '%s\n' "$process_override_names" | sed 's/^/  - /' >&2
  exit 1
fi

if [ ! -f "$env_file" ]; then
  postgres_password=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
  temporary_env=$(mktemp "$project_root/.env.tmp.XXXXXX")
  trap 'rm -f "$temporary_env"' EXIT HUP INT TERM
  sed "s/change-me-before-production/$postgres_password/g" "$example_file" > "$temporary_env"
  chmod 600 "$temporary_env"
  mv "$temporary_env" "$env_file"
  trap - EXIT HUP INT TERM
  echo '已创建本机 .env，并自动生成 PostgreSQL 随机密码。'
fi

unsupported_dotenv_line_numbers=$(
  LC_ALL=C awk '
    /^[[:space:]]*$/ || /^[[:space:]]*#/ { next }
    /^[A-Z][A-Z0-9_]*=/ { next }
    { print NR }
  ' "$env_file"
)
if [ -n "$unsupported_dotenv_line_numbers" ]; then
  echo '拒绝 .env 中不受支持的语法；只允许空行、注释和严格的大写 KEY=VALUE，违规行号：' >&2
  printf '%s\n' "$unsupported_dotenv_line_numbers" | sed 's/^/  - /' >&2
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

set -- --context "$docker_context" compose --project-directory "$project_root" --env-file "$env_file" -f "$compose_file"
if [ "$(dotenv_value ENABLE_DOWNLOAD_EXECUTOR)" = true ]; then
  set -- "$@" --profile download-execution
fi
if [ "$(dotenv_value ENABLE_DOWNLOAD_MONITOR)" = true ]; then
  set -- "$@" --profile download-monitor
fi

docker "$@" config --quiet
if [ "$validate_only" = true ]; then
  echo 'Docker Compose 配置有效；未启动任何容器。'
  exit 0
fi

docker "$@" up -d --build --wait --wait-timeout 180
docker "$@" ps
echo '请打开 http://127.0.0.1:9527 创建本地管理员并配置连接。'
