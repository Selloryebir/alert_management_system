#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

overall_status=0
json_reader="$PYTHON_VENV/bin/python"
if [[ ! -x "$json_reader" ]]; then
  json_reader=$(command -v python3 || true)
fi

for component in algorithm backend; do
  pid_file="$PID_DIR/$component.pid"
  if [[ "$component" == "algorithm" ]]; then
    marker="algorithm_service"
    windows_image="wsl.exe"
  else
    marker="alert-management-backend-0.1.0.jar"
    windows_image="java.exe"
  fi
  if [[ -f "$pid_file" ]] && pid_running_for_project "$(cat "$pid_file")" "$marker"; then
    echo "$component: RUNNING (PID $(cat "$pid_file"))"
    continue
  fi
  windows_pid_file="$PID_DIR/$component.winpid"
  if [[ -f "$windows_pid_file" ]] \
      && windows_pid_running "$(cat "$windows_pid_file")" "$windows_image" "$marker"; then
    echo "$component: RUNNING (Windows PID $(cat "$windows_pid_file"))"
  else
    echo "$component: STOPPED"
    overall_status=1
  fi
done

if docker_run container inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1; then
  postgres_status=$(docker_run inspect --format '{{.State.Status}}' "$POSTGRES_CONTAINER")
  echo "postgres: $postgres_status"
  [[ "$postgres_status" == "running" ]] || overall_status=1
else
  echo "postgres: NOT_CREATED"
  overall_status=1
fi

for url in http://127.0.0.1:8001/health http://127.0.0.1:8080/api/v1/health; do
  health_body=$(curl --noproxy '*' --connect-timeout 1 --max-time 2 \
      --fail --silent "$url" 2>/dev/null || true)
  top_level_status=""
  if [[ -n "$json_reader" && -n "$health_body" ]]; then
    top_level_status=$(printf '%s' "$health_body" | "$json_reader" -c \
      'import json, sys; print(json.load(sys.stdin).get("status", ""))' 2>/dev/null || true)
  fi
  if [[ "$top_level_status" == "UP" ]]; then
    echo "$url: UP"
  else
    echo "$url: DOWN"
    overall_status=1
  fi
done

exit "$overall_status"
