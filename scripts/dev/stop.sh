#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

stop_pid_file "$PID_DIR/backend.pid" "Java 后端" "alert-management-backend-0.1.0.jar"
stop_pid_file "$PID_DIR/algorithm.pid" "Python 算法服务" "algorithm_service"
stop_windows_pid_file "$PID_DIR/backend.winpid" "Java 后端" "java.exe" "alert-management-backend-0.1.0.jar"
stop_windows_pid_file "$PID_DIR/algorithm.winpid" "Python 算法服务" "wsl.exe" "algorithm_service"

if docker_run container inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1 \
    && [[ $(docker_run inspect --format '{{.State.Running}}' "$POSTGRES_CONTAINER") == "true" ]]; then
  docker_run stop --time 10 "$POSTGRES_CONTAINER" >/dev/null
fi

echo "M1 应用进程和开发数据库已停止；数据库卷保留用于重启迁移验证。"
