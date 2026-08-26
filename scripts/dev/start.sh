#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

cleanup_on_error() {
  local exit_code=$?
  if [[ $exit_code -ne 0 ]]; then
    echo "M1 启动失败，停止本次已启动的应用进程；日志位于 $LOG_DIR。" >&2
    stop_pid_file "$PID_DIR/backend.pid" "Java 后端" "alert-management-backend-0.1.0.jar" || true
    stop_pid_file "$PID_DIR/algorithm.pid" "Python 算法服务" "algorithm_service" || true
    stop_windows_pid_file "$PID_DIR/backend.winpid" "Java 后端" "java.exe" "alert-management-backend-0.1.0.jar" || true
    stop_windows_pid_file "$PID_DIR/algorithm.winpid" "Python 算法服务" "wsl.exe" "algorithm_service" || true
  fi
  exit "$exit_code"
}
trap cleanup_on_error EXIT

"$REPOSITORY_ROOT/scripts/dev/bootstrap.sh"
APP_SECRETS_DIR="$DEV_SECRET_ROOT" "$REPOSITORY_ROOT/scripts/security/prepare-local-secrets.sh" >/dev/null

if docker_run container inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1; then
  if [[ $(docker_run inspect --format '{{.State.Running}}' "$POSTGRES_CONTAINER") != "true" ]]; then
    docker_run start "$POSTGRES_CONTAINER" >/dev/null
  fi
else
  docker_run run --detach \
    --name "$POSTGRES_CONTAINER" \
    --label alert-management-demo=m1 \
    --label "alert-management-runtime-scope=$POSTGRES_RUNTIME_SCOPE" \
    --publish "127.0.0.1:${POSTGRES_PORT}:5432" \
    --env POSTGRES_DB=alert_management \
    --env POSTGRES_USER=alert_management \
    --env POSTGRES_PASSWORD=alert_management \
    --volume "$POSTGRES_VOLUME:/var/lib/postgresql/data" \
    "$POSTGRES_IMAGE" >/dev/null
fi

for _ in $(seq 1 40); do
  if docker_run exec "$POSTGRES_CONTAINER" pg_isready \
      --username alert_management --dbname alert_management >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker_run exec "$POSTGRES_CONTAINER" pg_isready \
  --username alert_management --dbname alert_management >/dev/null

stop_pid_file "$PID_DIR/backend.pid" "Java 后端" "alert-management-backend-0.1.0.jar"
stop_pid_file "$PID_DIR/algorithm.pid" "Python 算法服务" "algorithm_service"
stop_windows_pid_file "$PID_DIR/backend.winpid" "Java 后端" "java.exe" "alert-management-backend-0.1.0.jar"
stop_windows_pid_file "$PID_DIR/algorithm.winpid" "Python 算法服务" "wsl.exe" "algorithm_service"

npm --prefix "$REPOSITORY_ROOT/src/frontend" run build

java_bin=$(find_java)
windows_backend_started=false
if [[ "$java_bin" == *.exe ]] && command -v powershell.exe >/dev/null 2>&1; then
  rm -f "$PID_DIR/backend.winpid"
  powershell.exe -NoProfile -ExecutionPolicy Bypass \
    -File "$(wslpath -w "$REPOSITORY_ROOT/scripts/dev/start-backend.ps1")" \
    -RepositoryRoot "$(wslpath -w "$REPOSITORY_ROOT")" \
    -BootstrapAdminPasswordFile "$(wslpath -w "$DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE")" \
    -PidFile "$(wslpath -w "$PID_DIR/backend.winpid")" \
    -PostgresPort "$POSTGRES_PORT" -Build </dev/null
  for _ in $(seq 1 20); do
    [[ -s "$PID_DIR/backend.winpid" ]] && break
    sleep 0.25
  done
  if [[ ! -s "$PID_DIR/backend.winpid" ]]; then
    echo "Windows 后端启动器未写入 PID。" >&2
    exit 1
  fi
  windows_backend_started=true
else
  "$REPOSITORY_ROOT/mvnw" -f "$REPOSITORY_ROOT/src/backend/pom.xml" \
    package -DskipTests
fi

if [[ "$windows_backend_started" != true ]]; then
  jar_path="$REPOSITORY_ROOT/src/backend/target/alert-management-backend-0.1.0.jar"
  (
    cd "$REPOSITORY_ROOT"
    nohup env \
      SERVER_PORT=8080 \
      SERVER_ADDRESS=127.0.0.1 \
      DB_URL="jdbc:postgresql://127.0.0.1:${POSTGRES_PORT}/alert_management" \
      DB_USERNAME=alert_management \
      DB_PASSWORD=alert_management \
      APP_DEPLOYMENT_MODE=LOCAL_NATIVE \
      APP_BOOTSTRAP_ADMIN_USERNAME=admin \
      APP_BOOTSTRAP_ADMIN_PASSWORD_FILE="$DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE" \
      SESSION_COOKIE_SECURE=false \
      ALGORITHM_HEALTH_URL=http://127.0.0.1:8001/health \
      "$java_bin" -Xms128m -Xmx768m -jar "$jar_path" \
      </dev/null >"$LOG_DIR/backend.log" 2>&1 &
    echo $! > "$PID_DIR/backend.pid"
  )
fi

(
  cd "$REPOSITORY_ROOT/src/algorithm"
  nohup env ALGORITHM_HOST=127.0.0.1 ALGORITHM_PORT=8001 \
    "$PYTHON_VENV/bin/python" -m algorithm_service \
    </dev/null >"$LOG_DIR/algorithm.log" 2>"$LOG_DIR/algorithm-error.log" &
  echo $! > "$PID_DIR/algorithm.pid"
)

wait_for_url "http://127.0.0.1:8001/health" "Python 算法服务"
wait_for_url "http://127.0.0.1:8080/api/v1/health" "Java 后端"
"$REPOSITORY_ROOT/scripts/dev/smoke.sh"

trap - EXIT
echo "M1 四组件已启动：http://127.0.0.1:8080"
echo "开发管理员：admin；首次密码文件：$DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE"
echo "日志目录：$LOG_DIR"
