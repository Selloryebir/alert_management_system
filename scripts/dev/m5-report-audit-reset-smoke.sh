#!/usr/bin/env bash

source "$(dirname "$0")/browser-test-common.sh"

M5_RUNTIME="$RUNTIME_DIR/m5"
M5_OUTPUT="$M5_RUNTIME/results"
SENTINEL_TABLE="m5_reset_scope_sentinel"
SENTINEL_VALUE="M5_RESET_MUST_PRESERVE_THIS_ROW"
mkdir -p "$M5_OUTPUT"

cleanup_sentinel() {
  docker_run exec "$POSTGRES_CONTAINER" psql \
    --username alert_management \
    --dbname alert_management \
    --command "DROP TABLE IF EXISTS $SENTINEL_TABLE" >/dev/null 2>&1 || true
}

cleanup_m5() {
  cleanup_sentinel
  "$REPOSITORY_ROOT/scripts/dev/stop.sh" || true
}
trap cleanup_m5 EXIT

verify_sentinel() {
  local actual
  actual=$(docker_run exec "$POSTGRES_CONTAINER" psql \
    --username alert_management \
    --dbname alert_management \
    --tuples-only \
    --no-align \
    --command "SELECT marker FROM $SENTINEL_TABLE" | tr -d '\r')
  if [[ "$actual" != "$SENTINEL_VALUE" ]]; then
    echo "演示复位越界：项目 PostgreSQL 哨兵记录未保留。" >&2
    return 1
  fi
}

prepare_e2e_browser_runtime
"$REPOSITORY_ROOT/scripts/dev/start.sh"
dev_admin_login "$M5_RUNTIME"
"$REPOSITORY_ROOT/scripts/dev/backup.sh"

docker_run exec "$POSTGRES_CONTAINER" psql \
  --username alert_management \
  --dbname alert_management \
  --set ON_ERROR_STOP=1 \
  --command "CREATE TABLE IF NOT EXISTS $SENTINEL_TABLE (marker text PRIMARY KEY)" \
  --command "TRUNCATE TABLE $SENTINEL_TABLE" \
  --command "INSERT INTO $SENTINEL_TABLE(marker) VALUES ('$SENTINEL_VALUE')" >/dev/null

curl "${DEV_AUTH_CURL_ARGS[@]}" "${DEV_AUTH_CSRF_ARGS[@]}" \
  --header 'Content-Type: application/json' \
  --data '{"operator":"demo-reviewer","confirmation":"RESET_DEMO"}' \
  http://127.0.0.1:8080/api/v1/demo/reset > "$M5_RUNTIME/initial-reset.json"
"$PYTHON_VENV/bin/python" - "$M5_RUNTIME/initial-reset.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    response = json.load(stream)
if response.get("business_state") != "EMPTY":
    raise SystemExit(f"初始演示复位未返回 EMPTY：{response}")
PY
verify_sentinel

smoke_started=$(monotonic_ms)
env \
  E2E_BASE_URL=http://127.0.0.1:8080 \
  E2E_MODE=smoke \
  E2E_ADMIN_PASSWORD_FILE="$DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE" \
  E2E_DATASET="$REPOSITORY_ROOT/samples/smoke/synthetic_smoke_utf8.csv" \
  E2E_EXPECTED_TOTAL=300 \
  M5_OUTPUT_DIR="$M5_OUTPUT" \
  npm --prefix "$E2E_DIR" run test:m5
smoke_finished=$(monotonic_ms)
verify_sentinel
echo "M5 两轮 300 行报告、审计、复位闭环完成：$((smoke_finished - smoke_started))ms。"

demo_file="$M5_RUNTIME/synthetic_demo_20000.csv"
python3 "$REPOSITORY_ROOT/samples/generate_samples.py" --dataset demo --output "$demo_file"
demo_started=$(monotonic_ms)
env \
  E2E_BASE_URL=http://127.0.0.1:8080 \
  E2E_MODE=demo \
  E2E_ADMIN_PASSWORD_FILE="$DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE" \
  E2E_DATASET="$demo_file" \
  E2E_EXPECTED_TOTAL=20000 \
  M5_OUTPUT_DIR="$M5_OUTPUT" \
  npm --prefix "$E2E_DIR" run test:m5
demo_finished=$(monotonic_ms)
verify_sentinel

"$PYTHON_VENV/bin/python" - "$M5_OUTPUT/demo-20000-metrics.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    metrics = json.load(stream)
if metrics.get("total") != 20_000:
    raise SystemExit(f"20k 指标总数错误：{metrics}")
reports = metrics.get("reports", [])
if [report.get("format") for report in reports] != ["pdf", "xlsx"]:
    raise SystemExit(f"20k 报告格式不完整：{reports}")
for report in reports:
    if report.get("duration_ms", 0) <= 0 or report.get("bytes", 0) <= 100:
        raise SystemExit(f"20k 报告指标无效：{report}")
    print(
        f"20k {report['format'].upper()}："
        f"{report['duration_ms']}ms，{report['bytes']} bytes，{report['file']}"
    )
PY

echo "M5 20000 行两类报告与复位完成：$((demo_finished - demo_started))ms。"
echo "M5 验收产物：$M5_OUTPUT"
