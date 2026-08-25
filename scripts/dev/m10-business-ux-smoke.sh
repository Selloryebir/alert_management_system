#!/usr/bin/env bash

source "$(dirname "$0")/browser-test-common.sh"

M10_RUNTIME="$RUNTIME_DIR/m10-business-ux"
M10_RUN_ID="${M10_RUN_ID:-$(date -u '+%Y%m%d-%H%M%S')-$$}"
M10_OUTPUT="$M10_RUNTIME/results/$M10_RUN_ID"
M10_BASE_URL="${M10_BASE_URL:-http://127.0.0.1:8080}"
M10_DATASET="${M10_DATASET:-$REPOSITORY_ROOT/samples/smoke/synthetic_smoke_utf8.csv}"

cleanup_m10() {
  local exit_code=$?
  if ! "$REPOSITORY_ROOT/scripts/dev/stop.sh"; then
    echo "M10 验收栈未能完整停止；请检查 $LOG_DIR。" >&2
    exit_code=1
  fi
  if ((exit_code != 0)); then
    echo "M10 项目化业务与全中文 UX 验收失败；证据保留在 $M10_OUTPUT。" >&2
    for log_file in "$LOG_DIR"/backend.log "$LOG_DIR"/algorithm.log "$LOG_DIR"/frontend.log; do
      if [[ -f "$log_file" ]]; then
        echo "最近日志：$log_file" >&2
        tail -n 60 "$log_file" >&2 || true
      fi
    done
  fi
  exit "$exit_code"
}
trap cleanup_m10 EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ ! -f "$M10_DATASET" ]]; then
  echo "M10 样例文件不存在：$M10_DATASET" >&2
  exit 1
fi

mkdir -p "$M10_OUTPUT"
prepare_e2e_browser_runtime
"$REPOSITORY_ROOT/scripts/dev/start.sh"

started_at=$(monotonic_ms)
"$PYTHON_VENV/bin/python" \
  "$REPOSITORY_ROOT/tests/m10/api_smoke.py" \
  --base-url "$M10_BASE_URL" \
  --dataset "$M10_DATASET" \
  --correction-dataset "$REPOSITORY_ROOT/tests/m10/data/correctable-invalid.csv" \
  --output "$M10_OUTPUT/api-result.json"

mapfile -t project_ids < <("$PYTHON_VENV/bin/python" - "$M10_OUTPUT/api-result.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    result = json.load(stream)
print(result["project_a"]["project_id"])
print(result["project_b"]["project_id"])
PY
)
if ((${#project_ids[@]} != 2)); then
  echo "M10 API 证据未返回两个项目 ID。" >&2
  exit 1
fi

: >"$M10_OUTPUT/database-counts.tsv"
for project_id in "${project_ids[@]}"; do
  docker_run exec "$POSTGRES_CONTAINER" psql \
    --username alert_management \
    --dbname alert_management \
    --tuples-only \
    --no-align \
    --field-separator $'\t' \
    --set ON_ERROR_STOP=1 \
    --command "
      SELECT p.project_id, COUNT(DISTINCT b.batch_id), COUNT(a.record_id),
             COUNT(a.record_id) FILTER (WHERE a.invalidated_at IS NULL),
             COUNT(a.record_id) FILTER (WHERE a.invalidated_at IS NOT NULL),
             COUNT(r.record_id) FILTER (WHERE COALESCE(d.status, 'OPEN') <> 'CLOSED')
        FROM business_project p
        LEFT JOIN import_batch b ON b.project_id=p.project_id
        LEFT JOIN alarm_record a ON a.batch_id=b.batch_id
        LEFT JOIN analysis_result r ON r.record_id=a.record_id
        LEFT JOIN alarm_disposition d ON d.run_id=r.run_id AND d.record_id=r.record_id
       WHERE p.project_id='$project_id'::uuid
       GROUP BY p.project_id;
    " >>"$M10_OUTPUT/database-counts.tsv"
done

"$PYTHON_VENV/bin/python" - "$M10_OUTPUT/api-result.json" "$M10_OUTPUT/database-counts.tsv" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    result = json.load(stream)
rows = {}
with open(sys.argv[2], encoding="utf-8") as stream:
    for line in stream:
        values = line.rstrip("\r\n").split("\t")
        if len(values) == 6:
            rows[values[0]] = tuple(map(int, values[1:]))
for key in ("a", "b"):
    project_id = result[f"project_{key}"]["project_id"]
    overview = result[f"overview_{key}"]
    expected = (
        overview["batch_count"], overview["alarm_count"],
        overview["valid_alarm_count"], overview["invalid_alarm_count"],
        overview["pending_disposition_count"],
    )
    if rows.get(project_id) != expected:
        raise SystemExit(
            f"项目 {project_id} overview 与 PostgreSQL 不一致：API={expected}，DB={rows.get(project_id)}"
        )
print("两个项目的 overview 与 PostgreSQL 事实计数逐字段一致。")
PY

env \
  E2E_BASE_URL="$M10_BASE_URL" \
  E2E_DATASET="$M10_DATASET" \
  M10_CORRECTION_DATASET="$REPOSITORY_ROOT/tests/m10/data/correctable-invalid.csv" \
  M10_OUTPUT_DIR="$M10_OUTPUT" \
  node "$REPOSITORY_ROOT/tests/m10/browser-smoke.mjs"
finished_at=$(monotonic_ms)

echo "M10 两项目 API 隔离与浏览器六步业务闭环通过：$((finished_at - started_at))ms。"
echo "M10 验收证据：$M10_OUTPUT"
