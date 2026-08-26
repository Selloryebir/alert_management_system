#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
RUNTIME_DIR="$REPOSITORY_ROOT/.runtime"
LOG_DIR="$RUNTIME_DIR/logs"
PID_DIR="$RUNTIME_DIR/pids"
PYTHON_RUNTIME="$RUNTIME_DIR/python/bin/python3"
PYTHON_VENV="$RUNTIME_DIR/venv"
POSTGRES_CONTAINER=${POSTGRES_CONTAINER:-"alert-management-m1-postgres"}
POSTGRES_VOLUME=${POSTGRES_VOLUME:-"alert_management_m1_pgdata"}
POSTGRES_IMAGE=${POSTGRES_IMAGE:-"postgres:17.6-bookworm@sha256:f3bd19c606e442c3d7bdfa8002e03fe260a1023351e0ea4598032022b68dd6e3"}
POSTGRES_PORT=${POSTGRES_PORT:-"55432"}
POSTGRES_RUNTIME_SCOPE=${POSTGRES_RUNTIME_SCOPE:-"m1"}
DEV_SECRET_ROOT=${APP_SECRETS_DIR:-"$RUNTIME_DIR/compose-secrets"}
DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE="$DEV_SECRET_ROOT/bootstrap-admin-password.txt"

mkdir -p "$RUNTIME_DIR" "$LOG_DIR" "$PID_DIR"

dev_admin_login() {
  local session_dir=$1
  local login_response must_change new_password
  mkdir -p "$session_dir"
  DEV_AUTH_COOKIE_JAR="$session_dir/admin-cookie.txt"
  : > "$DEV_AUTH_COOKIE_JAR"
  chmod 600 "$DEV_AUTH_COOKIE_JAR"

  dev_admin_refresh_csrf
  login_response=$(dev_admin_login_request)
  must_change=$(LOGIN_JSON="$login_response" "$PYTHON_VENV/bin/python" -c \
    'import json,os; value=json.loads(os.environ["LOGIN_JSON"]); assert value["username"] == "admin" and value["global_role"] == "SYSTEM_ADMIN"; print(str(value["must_change_password"]).lower())')
  if [[ "$must_change" != "true" ]]; then
    return
  fi

  new_password=$(
    "$PYTHON_VENV/bin/python" -c \
      'import secrets; print("Dev-M11-" + secrets.token_urlsafe(20))'
  )
  PASSWORD_FILE="$DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE" NEW_PASSWORD="$new_password" \
    "$PYTHON_VENV/bin/python" - <<'PY' |
import json
import os

with open(os.environ["PASSWORD_FILE"], encoding="utf-8") as source:
    current = source.read().strip()
print(json.dumps({"current_password": current, "new_password": os.environ["NEW_PASSWORD"]}))
PY
    curl "${DEV_AUTH_CURL_ARGS[@]}" "${DEV_AUTH_CSRF_ARGS[@]}" \
      --header 'Content-Type: application/json' --data-binary @- \
      http://127.0.0.1:8080/api/v1/auth/password >/dev/null
  PASSWORD_FILE="$DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE" NEW_PASSWORD="$new_password" \
    "$PYTHON_VENV/bin/python" - <<'PY'
import os
from pathlib import Path

target = Path(os.environ["PASSWORD_FILE"])
temporary = target.with_suffix(".tmp")
temporary.write_text(os.environ["NEW_PASSWORD"], encoding="utf-8")
temporary.chmod(0o600)
temporary.replace(target)
PY
  unset new_password

  : > "$DEV_AUTH_COOKIE_JAR"
  dev_admin_refresh_csrf
  login_response=$(dev_admin_login_request)
  LOGIN_JSON="$login_response" "$PYTHON_VENV/bin/python" -c \
    'import json,os; value=json.loads(os.environ["LOGIN_JSON"]); assert value["username"] == "admin" and not value["must_change_password"]'
}

dev_admin_login_request() {
  PASSWORD_FILE="$DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE" "$PYTHON_VENV/bin/python" - <<'PY' |
import json
import os

with open(os.environ["PASSWORD_FILE"], encoding="utf-8") as source:
    print(json.dumps({"username": "admin", "password": source.read().strip()}))
PY
    curl "${DEV_AUTH_CURL_ARGS[@]}" "${DEV_AUTH_CSRF_ARGS[@]}" \
      --header 'Content-Type: application/json' --data-binary @- \
      http://127.0.0.1:8080/api/v1/auth/login
}

dev_admin_refresh_csrf() {
  local csrf_response
  csrf_response=$(curl --noproxy '*' --fail --silent --show-error \
    --cookie "$DEV_AUTH_COOKIE_JAR" --cookie-jar "$DEV_AUTH_COOKIE_JAR" \
    http://127.0.0.1:8080/api/v1/auth/csrf)
  DEV_AUTH_CSRF_HEADER=$(CSRF_JSON="$csrf_response" "$PYTHON_VENV/bin/python" -c \
    'import json,os; print(json.loads(os.environ["CSRF_JSON"])["header_name"])')
  DEV_AUTH_CSRF_TOKEN=$(CSRF_JSON="$csrf_response" "$PYTHON_VENV/bin/python" -c \
    'import json,os; print(json.loads(os.environ["CSRF_JSON"])["token"])')
  DEV_AUTH_CURL_ARGS=(--noproxy '*' --fail --silent --show-error
    --cookie "$DEV_AUTH_COOKIE_JAR" --cookie-jar "$DEV_AUTH_COOKIE_JAR")
  DEV_AUTH_CSRF_ARGS=(--header "$DEV_AUTH_CSRF_HEADER: $DEV_AUTH_CSRF_TOKEN")
}

find_docker() {
  local attempt
  for attempt in 1 2 3; do
    if command -v docker >/dev/null 2>&1 && docker version >/dev/null 2>&1; then
      command -v docker
      return
    fi
    if command -v docker.exe >/dev/null 2>&1 && docker.exe version >/dev/null 2>&1; then
      command -v docker.exe
      return
    fi
    if ((attempt < 3)); then
      sleep 1
    fi
  done
  echo "Docker Desktop 未运行或当前终端无法访问 Docker；M1 仅用它承载开发期 PostgreSQL。" >&2
  return 1
}

docker_run() {
  local docker_bin
  docker_bin=$(find_docker) || return 1
  "$docker_bin" "$@"
}

find_java() {
  if command -v java >/dev/null 2>&1; then
    command -v java
    return
  fi
  if command -v java.exe >/dev/null 2>&1; then
    command -v java.exe
    return
  fi
  if command -v cmd.exe >/dev/null 2>&1; then
    local windows_java
    windows_java=$(cmd.exe /d /c "where java" 2>/dev/null | tr -d '\r' | head -n 1)
    if [[ -n "$windows_java" ]]; then
      wslpath -u "$windows_java"
      return
    fi
  fi
  echo "未找到 Java 21；请安装 JDK 21 后重试。" >&2
  return 1
}

wait_for_url() {
  local url=$1
  local label=$2
  local attempts=${3:-40}
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl --noproxy '*' --connect-timeout 1 --max-time 2 \
        --fail --silent --show-error "$url" >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  echo "$label 未在 ${attempts} 秒内就绪：$url" >&2
  return 1
}

pid_running_for_project() {
  local pid=$1
  local expected_marker=$2
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null \
    && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -Fq "$expected_marker"
}

stop_pid_file() {
  local pid_file=$1
  local label=$2
  local expected_marker=$3
  [[ -f "$pid_file" ]] || return 0
  local pid
  pid=$(tr -d '[:space:]' < "$pid_file")
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    if ! pid_running_for_project "$pid" "$expected_marker"; then
      echo "$label PID 文件已失效，未停止非项目进程：PID=$pid" >&2
      rm -f "$pid_file"
      return 0
    fi
    kill "$pid"
    for _ in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "$label 未能正常停止，PID=$pid" >&2
      return 1
    fi
  fi
  rm -f "$pid_file"
}

windows_pid_running() {
  local pid=$1
  local expected_image=$2
  local expected_marker=$3
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  local process_line
  process_line=$(powershell.exe -NoProfile -Command \
    "\$p = Get-CimInstance Win32_Process -Filter 'ProcessId = $pid'; if (\$null -ne \$p) { Write-Output (\$p.Name + '|' + \$p.CommandLine) }" \
    2>/dev/null | tr -d '\r')
  [[ "$process_line" == "$expected_image|"* && "$process_line" == *"$expected_marker"* ]]
}

stop_windows_pid_file() {
  local pid_file=$1
  local label=$2
  local expected_image=$3
  local expected_marker=$4
  [[ -f "$pid_file" ]] || return 0
  local pid
  pid=$(tr -d '[:space:]' < "$pid_file")
  if [[ "$pid" =~ ^[0-9]+$ ]] && windows_pid_running "$pid" "$expected_image" "$expected_marker"; then
    taskkill.exe /PID "$pid" >/dev/null
  elif [[ "$pid" =~ ^[0-9]+$ ]]; then
    echo "$label PID 文件已失效，未停止非项目进程：Windows PID=$pid" >&2
  fi
  rm -f "$pid_file"
}
