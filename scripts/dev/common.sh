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
DEV_SECRET_ROOT=${APP_SECRETS_DIR:-"$RUNTIME_DIR/compose-secrets"}
DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE="$DEV_SECRET_ROOT/bootstrap-admin-password.txt"

mkdir -p "$RUNTIME_DIR" "$LOG_DIR" "$PID_DIR"

find_docker() {
  if command -v docker >/dev/null 2>&1 && docker version >/dev/null 2>&1; then
    command -v docker
    return
  fi
  if command -v docker.exe >/dev/null 2>&1 && docker.exe version >/dev/null 2>&1; then
    command -v docker.exe
    return
  fi
  echo "Docker Desktop 未运行或当前终端无法访问 Docker；M1 仅用它承载开发期 PostgreSQL。" >&2
  return 1
}

docker_run() {
  local docker_bin
  docker_bin=$(find_docker)
  "$docker_bin" "$@"
}

find_java() {
  if command -v java >/dev/null 2>&1; then
    command -v java
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
