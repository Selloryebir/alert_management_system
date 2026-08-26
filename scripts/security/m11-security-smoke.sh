#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
source "$REPOSITORY_ROOT/scripts/dev/common.sh"

run_suffix="$(date -u '+%Y%m%d-%H%M%S')-$$"
evidence_root="$RUNTIME_DIR/m11/results/$run_suffix"
local_secret_root="$evidence_root/local-secrets"
network_secret_root="$evidence_root/network-secrets"
export POSTGRES_CONTAINER="alert-management-m11-${run_suffix,,}"
export POSTGRES_VOLUME="alert_management_m11_${run_suffix//-/_}"
export POSTGRES_RUNTIME_SCOPE="m11-$run_suffix"
export APP_SECRETS_DIR="$local_secret_root"
network_project="alert-management-m11-network-${run_suffix,,}"
network_started=false

docker_bin=""
compose_repository_root=""
compose_file=""
compose_network_file=""
compose_network_secret_root=""

resolve_compose() {
  docker_bin=$(find_docker)
  compose_repository_root="$REPOSITORY_ROOT"
  compose_file="$REPOSITORY_ROOT/compose.yaml"
  compose_network_file="$REPOSITORY_ROOT/compose.network.yaml"
  compose_network_secret_root="$network_secret_root"
  if [[ "$docker_bin" == *.exe ]]; then
    compose_repository_root=$(wslpath -w "$REPOSITORY_ROOT")
    compose_file=$(wslpath -w "$REPOSITORY_ROOT/compose.yaml")
    compose_network_file=$(wslpath -w "$REPOSITORY_ROOT/compose.network.yaml")
    compose_network_secret_root=$(wslpath -w "$network_secret_root")
  fi
}

network_compose() {
  APP_SECRETS_DIR="$compose_network_secret_root" "$docker_bin" compose \
    --file "$compose_file" \
    --file "$compose_network_file" \
    --project-name "$network_project" \
    --project-directory "$compose_repository_root" \
    "$@"
}

cleanup() {
  local exit_code=$?
  if [[ "$network_started" == true ]]; then
    network_compose down --volumes --remove-orphans >/dev/null 2>&1 || exit_code=1
  fi
  "$REPOSITORY_ROOT/scripts/dev/stop.sh" >/dev/null 2>&1 || exit_code=1
  if [[ -n "$docker_bin" ]] && "$docker_bin" container inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1; then
    local scope
    scope=$($docker_bin inspect --format '{{ index .Config.Labels "alert-management-runtime-scope" }}' \
      "$POSTGRES_CONTAINER" 2>/dev/null || true)
    if [[ "$scope" == "$POSTGRES_RUNTIME_SCOPE" ]]; then
      "$docker_bin" container rm --force "$POSTGRES_CONTAINER" >/dev/null || exit_code=1
    else
      echo "拒绝清理身份不一致的 PostgreSQL 容器：$POSTGRES_CONTAINER" >&2
      exit_code=1
    fi
  fi
  if [[ -n "$docker_bin" && "$POSTGRES_VOLUME" == alert_management_m11_* ]] \
      && "$docker_bin" volume inspect "$POSTGRES_VOLUME" >/dev/null 2>&1; then
    "$docker_bin" volume rm "$POSTGRES_VOLUME" >/dev/null || exit_code=1
  fi
  if ((exit_code != 0)); then
    echo "M11 安全验收失败；证据保留在：$evidence_root" >&2
  fi
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$evidence_root"
resolve_compose

# 避免此前开发会话占用固定端口；只按已有 PID 记录和项目容器身份停止。
env -u POSTGRES_CONTAINER -u POSTGRES_VOLUME -u POSTGRES_RUNTIME_SCOPE -u APP_SECRETS_DIR \
  "$REPOSITORY_ROOT/scripts/dev/stop.sh" >/dev/null 2>&1 || true

"$REPOSITORY_ROOT/scripts/dev/start.sh"
python3 "$REPOSITORY_ROOT/tests/m11/security_smoke.py" \
  --base-url http://127.0.0.1:8080 \
  --bootstrap-username admin \
  --bootstrap-password-file "$local_secret_root/bootstrap-admin-password.txt" \
  --output-dir "$evidence_root/api"

docker_host_binding=$($docker_bin inspect --format \
  '{{ (index (index .NetworkSettings.Ports "5432/tcp") 0).HostIp }}' "$POSTGRES_CONTAINER")
if [[ "$docker_host_binding" != "127.0.0.1" ]]; then
  echo "开发 PostgreSQL 没有只发布到回环地址：$docker_host_binding" >&2
  exit 1
fi
powershell.exe -NoProfile -Command \
  "\$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction Stop); if (\$listeners.Count -ne 1 -or \$listeners[0].LocalAddress -notin @('127.0.0.1','::1')) { throw '主系统未只绑定回环地址' }" \
  >/dev/null
if ! ss -ltnH 'sport = :8001' | awk '{print $4}' | grep -Eq '^(127\.0\.0\.1|\[::1\]):8001$'; then
  echo "算法服务没有只绑定回环地址。" >&2
  exit 1
fi
printf '{"postgres":"%s","backend":"loopback","algorithm":"loopback"}\n' "$docker_host_binding" \
  > "$evidence_root/local-bindings.json"

"$REPOSITORY_ROOT/scripts/dev/stop.sh" >/dev/null
if "$docker_bin" container inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1; then
  "$docker_bin" container rm "$POSTGRES_CONTAINER" >/dev/null
fi
if "$docker_bin" volume inspect "$POSTGRES_VOLUME" >/dev/null 2>&1; then
  "$docker_bin" volume rm "$POSTGRES_VOLUME" >/dev/null
fi

APP_SECRETS_DIR="$network_secret_root" "$REPOSITORY_ROOT/scripts/security/prepare-local-secrets.sh" >/dev/null
network_started=true
network_compose build backend algorithm

set +e
network_compose up --detach --wait --wait-timeout 90 >"$evidence_root/network-missing-tls.log" 2>&1
missing_tls_status=$?
set -e
network_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
if ((missing_tls_status == 0)); then
  echo "缺少 TLS 文件时网络部署错误启动成功。" >&2
  exit 1
fi
if ! grep -Eqi 'tls-keystore|tls_keystore|secret|秘密|密钥|file' "$evidence_root/network-missing-tls.log"; then
  echo "缺少 TLS 的失败日志没有指向密钥文件。" >&2
  exit 1
fi

openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 1 \
  -subj /CN=localhost \
  -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  -keyout "$network_secret_root/tls-test-key.pem" \
  -out "$network_secret_root/tls-test-cert.pem" >/dev/null 2>&1
openssl rand -base64 -out "$network_secret_root/tls-keystore-password.txt" 32
openssl pkcs12 -export \
  -out "$network_secret_root/tls-keystore.p12" \
  -inkey "$network_secret_root/tls-test-key.pem" \
  -in "$network_secret_root/tls-test-cert.pem" \
  -passout "file:$network_secret_root/tls-keystore-password.txt"

network_compose up --detach --wait --wait-timeout 240
curl --noproxy '*' --fail --silent --show-error \
  --cacert "$network_secret_root/tls-test-cert.pem" \
  https://localhost:8443/api/v1/health > "$evidence_root/network-health.json"
python3 - "$evidence_root/network-health.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
assert payload["status"] == "UP", payload
assert all(payload["components"][name]["status"] == "UP" for name in ("system", "database", "algorithm")), payload
PY
if curl --noproxy '*' --silent --show-error --max-time 3 http://127.0.0.1:8080/api/v1/health >/dev/null 2>&1; then
  echo "NETWORK 模式仍暴露明文 HTTP 8080。" >&2
  exit 1
fi
for service in postgres algorithm; do
  container_id=$(network_compose ps --quiet "$service")
  published=$($docker_bin inspect --format '{{ json .NetworkSettings.Ports }}' "$container_id")
  if [[ "$published" != *'null'* || "$published" == *'HostPort'* ]]; then
    echo "NETWORK 模式错误发布了 $service 端口：$published" >&2
    exit 1
  fi
done
printf '{"https":8443,"http_8080":"closed","postgres":"internal","algorithm":"internal"}\n' \
  > "$evidence_root/network-boundary.json"

network_compose down --volumes --remove-orphans >/dev/null
network_started=false
trap - EXIT
echo "M11 身份、授权、输入边界、本机回环与网络 HTTPS 验收通过。"
echo "M11 验收证据：$evidence_root"
