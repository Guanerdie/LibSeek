#!/usr/bin/env sh
set -eu

usage() {
  echo '用法：scripts/start-secrets.sh --docker-context <名称> --discovery [--avistaz] [--qb] [--profile automation-preflight|download-execution|download-monitor] [--validate-only]' >&2
}

discovery=false
avistaz=false
qb=false
docker_context=''
docker_context_set=false
automation_preflight=false
download_execution=false
download_monitor=false
validate_only=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --discovery)
      discovery=true
      ;;
    --docker-context)
      shift
      if [ "$#" -eq 0 ] || [ "$docker_context_set" = true ]; then
        usage
        exit 2
      fi
      docker_context=$1
      docker_context_set=true
      ;;
    --avistaz)
      avistaz=true
      ;;
    --qb)
      qb=true
      ;;
    --profile)
      shift
      if [ "$#" -eq 0 ]; then
        usage
        exit 2
      fi
      case "$1" in
        automation-preflight) automation_preflight=true ;;
        download-execution) download_execution=true ;;
        download-monitor) download_monitor=true ;;
        *)
          usage
          exit 2
          ;;
      esac
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
  echo 'Docker Secret 启动必须通过 --docker-context 显式选择安全格式的 context 名称。' >&2
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

if [ "$discovery" != true ]; then
  echo 'Docker Secret 启动必须显式选择 --discovery；只有对应阶段已获授权时才追加 --avistaz、--qb 和 --profile。' >&2
  exit 1
fi
if { [ "$automation_preflight" = true ] || [ "$download_monitor" = true ]; } && [ "$qb" != true ]; then
  echo 'automation-preflight 和 download-monitor profile 必须显式选择 --qb Secret 层。' >&2
  exit 1
fi
if [ "$download_execution" = true ] && { [ "$avistaz" != true ] || [ "$qb" != true ]; }; then
  echo 'download-execution profile 必须同时显式选择 --avistaz 和 --qb Secret 层。' >&2
  exit 1
fi

project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
env_file="$project_root/.env"
example_file="$project_root/.env.example"
compose_file="$project_root/compose.yaml"
discovery_compose_file="$project_root/deploy/compose.secrets.discovery.yaml.example"
avistaz_compose_file="$project_root/deploy/compose.secrets.avistaz.yaml.example"
qb_compose_file="$project_root/deploy/compose.secrets.qb.yaml.example"

for required_file in "$example_file" "$compose_file" "$discovery_compose_file"; do
  if [ ! -f "$required_file" ]; then
    echo "缺少仓库必需文件：$required_file" >&2
    exit 1
  fi
done
if [ "$avistaz" = true ] && [ ! -f "$avistaz_compose_file" ]; then
  echo "缺少仓库必需文件：$avistaz_compose_file" >&2
  exit 1
fi
if [ "$qb" = true ] && [ ! -f "$qb_compose_file" ]; then
  echo "缺少仓库必需文件：$qb_compose_file" >&2
  exit 1
fi

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
  echo '缺少 .env。请先复制 .env.example 并完成配置；Docker Secret 启动器不会自动创建它。' >&2
  exit 1
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

plaintext_secret_override_names=$(
  for name in \
    AUTH_LOCAL_USERNAME \
    AUTH_LOCAL_PASSWORD \
    AUTH_SESSION_SIGNING_KEY \
    NEXTFIND_USERNAME \
    NEXTFIND_PASSWORD \
    TMDB_ACCESS_TOKEN \
    AVISTAZ_USERNAME \
    AVISTAZ_PASSWORD \
    AVISTAZ_PID \
    QB_BASE_URL \
    QB_USERNAME \
    QB_PASSWORD; do
    matching_values=$(sed -n "s/^${name}=//p" "$env_file" | tr -d '\r')
    if printf '%s\n' "$matching_values" | grep -q '[^[:space:]]'; then
      printf '%s\n' "$name"
    fi
  done
)
if [ -n "$plaintext_secret_override_names" ]; then
  echo '使用 Docker Secret 时拒绝 .env 中的明文凭据；请清空以下键：' >&2
  printf '%s\n' "$plaintext_secret_override_names" | sed 's/^/  - /' >&2
  exit 1
fi

postgres_password=$(dotenv_value POSTGRES_PASSWORD)
database_url=$(dotenv_value DATABASE_URL)
if [ -z "$postgres_password" ] || [ -z "$database_url" ] || printf '%s' "$postgres_password" | grep -q 'change-me-before-production' || printf '%s' "$database_url" | grep -q 'change-me-before-production'; then
  echo '拒绝使用示例 PostgreSQL 密码启动；请同时修改 .env 中的 POSTGRES_PASSWORD 和 DATABASE_URL。' >&2
  exit 1
fi

missing_secret_names=''
require_secret_file() {
  secret_name=$1
  if [ ! -f "$project_root/secrets/$secret_name" ]; then
    if [ -n "$missing_secret_names" ]; then
      missing_secret_names="$missing_secret_names, $secret_name"
    else
      missing_secret_names=$secret_name
    fi
  fi
}

for secret_name in \
  auth_local_username.txt \
  auth_local_password.txt \
  auth_session_signing_key.txt \
  nextfind_username.txt \
  nextfind_password.txt \
  tmdb_access_token.txt; do
  require_secret_file "$secret_name"
done
if [ "$avistaz" = true ]; then
  for secret_name in avistaz_username.txt avistaz_password.txt avistaz_pid.txt; do
    require_secret_file "$secret_name"
  done
fi
if [ "$qb" = true ]; then
  for secret_name in qb_base_url.txt qb_username.txt qb_password.txt; do
    require_secret_file "$secret_name"
  done
fi
if [ -n "$missing_secret_names" ]; then
  echo "缺少所选层的 Docker Secret 文件：$missing_secret_names" >&2
  exit 1
fi

set -- \
  --context "$docker_context" \
  compose \
  --project-directory "$project_root" \
  --env-file "$env_file" \
  -f "$compose_file" \
  -f "$discovery_compose_file"
if [ "$avistaz" = true ]; then
  set -- "$@" -f "$avistaz_compose_file"
fi
if [ "$qb" = true ]; then
  set -- "$@" -f "$qb_compose_file"
fi
if [ "$automation_preflight" = true ]; then
  set -- "$@" --profile automation-preflight
fi
if [ "$download_execution" = true ]; then
  set -- "$@" --profile download-execution
fi
if [ "$download_monitor" = true ]; then
  set -- "$@" --profile download-monitor
fi

if ! docker "$@" config --quiet; then
  echo 'Docker Compose 配置验证失败。' >&2
  exit 1
fi
if [ "$validate_only" = true ]; then
  echo 'Docker Compose Secret 配置有效；未启动任何容器。'
  exit 0
fi

if ! docker "$@" up -d --build --wait --wait-timeout 180; then
  echo 'Docker Compose 启动失败。' >&2
  exit 1
fi
if ! docker "$@" ps; then
  echo 'Docker Compose 状态检查失败。' >&2
  exit 1
fi
echo '前端：http://127.0.0.1:8080  API：http://127.0.0.1:8000/api/docs'
