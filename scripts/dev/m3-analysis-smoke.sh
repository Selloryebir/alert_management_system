#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

# M1/M2 共用的开发实例可能仍在占用固定端口；M3 必须先结束它，
# 再切换到本轮专属容器、卷和密钥目录。
"$REPOSITORY_ROOT/scripts/dev/stop.sh"

M3_RUNTIME="$RUNTIME_DIR/m3"
mkdir -p "$M3_RUNTIME"
m3_run_id="${GITHUB_RUN_ID:-local}-$$"
export APP_SECRETS_DIR="$M3_RUNTIME/secrets-$m3_run_id"
DEV_SECRET_ROOT="$APP_SECRETS_DIR"
DEV_BOOTSTRAP_ADMIN_PASSWORD_FILE="$DEV_SECRET_ROOT/bootstrap-admin-password.txt"
export POSTGRES_CONTAINER="alert-management-m3-postgres-$m3_run_id"
export POSTGRES_VOLUME="alert_management_m3_pgdata_${m3_run_id//-/_}"
export POSTGRES_RUNTIME_SCOPE="m3-$m3_run_id"

cleanup_m3() {
  "$REPOSITORY_ROOT/scripts/dev/stop.sh" >/dev/null 2>&1 || true
  if docker_run container inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1; then
    container_scope=$(docker_run inspect --format \
      '{{ index .Config.Labels "alert-management-runtime-scope" }}' "$POSTGRES_CONTAINER")
    if [[ "$container_scope" == "$POSTGRES_RUNTIME_SCOPE" ]]; then
      docker_run rm --force "$POSTGRES_CONTAINER" >/dev/null
    else
      echo "拒绝清理范围不匹配的 M3 容器：$POSTGRES_CONTAINER" >&2
    fi
  fi
  if docker_run volume inspect "$POSTGRES_VOLUME" >/dev/null 2>&1; then
    volume_scope=$(docker_run volume inspect --format \
      '{{ index .Labels "alert-management-runtime-scope" }}' "$POSTGRES_VOLUME")
    if [[ "$volume_scope" == "$POSTGRES_RUNTIME_SCOPE" ]]; then
      docker_run volume rm "$POSTGRES_VOLUME" >/dev/null
    else
      echo "拒绝清理范围不匹配的 M3 数据卷：$POSTGRES_VOLUME" >&2
    fi
  fi
}
trap cleanup_m3 EXIT

docker_run volume create \
  --label alert-management-demo=m3 \
  --label "alert-management-runtime-scope=$POSTGRES_RUNTIME_SCOPE" \
  "$POSTGRES_VOLUME" >/dev/null

"$REPOSITORY_ROOT/scripts/dev/start.sh"

DEFAULT_PROJECT_ID="00000000-0000-0000-0000-000000000001"
dev_admin_login "$M3_RUNTIME"

monotonic_ms() {
  "$PYTHON_VENV/bin/python" -c 'import time; print(time.monotonic_ns() // 1_000_000)'
}

json_value() {
  local path=$1
  local key=$2
  "$PYTHON_VENV/bin/python" -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])' \
    "$path" "$key"
}

import_file() {
  local file_path=$1
  local label=$2
  curl "${DEV_AUTH_CURL_ARGS[@]}" "${DEV_AUTH_CSRF_ARGS[@]}" \
    --output "$M3_RUNTIME/$label-preview.json" \
    --form "project_id=$DEFAULT_PROJECT_ID" \
    --form "file=@$file_path" \
    http://127.0.0.1:8080/api/v1/imports/preview
  local batch_id
  batch_id=$(json_value "$M3_RUNTIME/$label-preview.json" batch_id)
  curl "${DEV_AUTH_CURL_ARGS[@]}" "${DEV_AUTH_CSRF_ARGS[@]}" \
    --output "$M3_RUNTIME/$label-confirm.json" \
    --request POST "http://127.0.0.1:8080/api/v1/imports/$batch_id/confirm"
  [[ $(json_value "$M3_RUNTIME/$label-confirm.json" status) == "IMPORTED" ]]
  echo "$batch_id"
}

analyze_batch() {
  local batch_id=$1
  local output=$2
  curl "${DEV_AUTH_CURL_ARGS[@]}" "${DEV_AUTH_CSRF_ARGS[@]}" \
    --output "$output" --request POST \
    "http://127.0.0.1:8080/api/v1/imports/$batch_id/analyses"
}

verify_smoke_analysis() {
  local actual_path=$1
  "$PYTHON_VENV/bin/python" - "$actual_path" \
      "$REPOSITORY_ROOT/samples/expected/analysis-smoke-expected.json" <<'PY'
import json
import sys
from datetime import datetime

actual = json.load(open(sys.argv[1], encoding="utf-8"))
expected = json.load(open(sys.argv[2], encoding="utf-8"))
assert actual["status"] == "COMPLETED", actual
assert actual.get("failure") is None, actual
assert actual["contract_version"] == "v2", actual
assert actual["algorithm_version"] == "0.2.0", actual
assert actual["rule_version"] == expected["rule_version"], actual
assert actual["parameters"] == expected["parameters"], actual

actual_records = [
    {
        "source_row": item["source_row"],
        "noise_type": item["noise_type"],
        "alarm_class": item["alarm_class"],
        "cause_category": item["cause_category"],
    }
    for item in actual["results"]
]
assert actual_records == expected["records"], (actual_records, expected["records"])
assert all(item["evidence"] for item in actual["results"]), actual["results"]
assert all(
    any("SUPERVISED_CAUSE_V2" in evidence for evidence in item["evidence"])
    for item in actual["results"]
), "监督模型未参与全部记录的可解释分析"

def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

actual_chains = sorted(
    (
        [member["source_row"] for member in chain["members"]],
        instant(chain["start_time"]),
        instant(chain["end_time"]),
        chain["association_rule"],
    )
    for chain in actual["event_chains"]
)
expected_chains = sorted(
    (
        chain["member_source_rows"],
        instant(chain["start_time"]),
        instant(chain["end_time"]),
        chain["association_rule_category"],
    )
    for chain in expected["event_chains"]
)
assert actual_chains == expected_chains, (actual_chains, expected_chains)
assert all(
    "不代表已确认根因" in chain["explanation"]
    for chain in actual["event_chains"]
), actual["event_chains"]

summary = actual["summary"]
expected_summary = expected["summary"]
assert summary["input_count"] == summary["success_count"] == 300, summary
assert summary["failure_count"] == 0, summary
assert summary["noise_type_counts"] == expected_summary["noise_type_counts"], summary
assert summary["cause_category_counts"] == expected_summary["cause_category_counts"], summary
assert summary["event_chain_count"] == expected_summary["event_chain_counts"]["total"], summary
serialized = json.dumps(actual, ensure_ascii=False)
assert "已证明根因" not in serialized and "真实准确率" not in serialized, serialized
PY
}

assert_analysis_storage() {
  local run_id=$1
  local expected_results=$2
  local expected_chains=$3
  local stored
  stored=$(docker_run exec "$POSTGRES_CONTAINER" psql \
    --username alert_management --dbname alert_management --tuples-only --no-align \
    --command "SELECT (SELECT COUNT(*) FROM analysis_result WHERE run_id = '$run_id')
                     || '|' || (SELECT COUNT(*) FROM event_chain WHERE run_id = '$run_id')
                     || '|' || (SELECT COUNT(*) FROM event_chain_member WHERE run_id = '$run_id');" \
    | tr -d '\r')
  [[ "$stored" == "$expected_results|$expected_chains|$((expected_chains * 5))" ]]
}

compare_smoke_semantics() {
  local first=$1
  local second=$2
  "$PYTHON_VENV/bin/python" - "$first" "$second" <<'PY'
import json
import sys

first = json.load(open(sys.argv[1], encoding="utf-8"))
second = json.load(open(sys.argv[2], encoding="utf-8"))
fields = ("source_row", "noise_type", "alarm_class", "cause_category", "score", "evidence")
first_records = [{key: item[key] for key in fields} for item in first["results"]]
second_records = [{key: item[key] for key in fields} for item in second["results"]]
assert first_records == second_records, (first_records, second_records)
chain_fields = ("start_time", "end_time", "association_rule", "explanation")
first_chains = sorted(
    [
        ({key: chain[key] for key in chain_fields}, [item["source_row"] for item in chain["members"]])
        for chain in first["event_chains"]
    ],
    key=lambda item: item[1],
)
second_chains = sorted(
    [
        ({key: chain[key] for key in chain_fields}, [item["source_row"] for item in chain["members"]])
        for chain in second["event_chains"]
    ],
    key=lambda item: item[1],
)
assert first_chains == second_chains, (first_chains, second_chains)
assert first["summary"] == second["summary"], (first["summary"], second["summary"])
PY
}

smoke_file="$REPOSITORY_ROOT/samples/smoke/synthetic_smoke_utf8.csv"
smoke_batch=$(import_file "$smoke_file" smoke-first)
analyze_batch "$smoke_batch" "$M3_RUNTIME/smoke-first-analysis.json"
verify_smoke_analysis "$M3_RUNTIME/smoke-first-analysis.json"
smoke_run=$(json_value "$M3_RUNTIME/smoke-first-analysis.json" run_id)
assert_analysis_storage "$smoke_run" 300 12

second_batch=$(import_file "$smoke_file" smoke-second)
analyze_batch "$second_batch" "$M3_RUNTIME/smoke-second-analysis.json"
verify_smoke_analysis "$M3_RUNTIME/smoke-second-analysis.json"
compare_smoke_semantics \
  "$M3_RUNTIME/smoke-first-analysis.json" "$M3_RUNTIME/smoke-second-analysis.json"
echo "固定 Smoke 两次分析逐行结果与 12 条关联链一致。"

failure_batch=$(import_file "$smoke_file" retry)
stop_pid_file "$PID_DIR/algorithm.pid" "Python 算法服务" "algorithm_service"
if command -v powershell.exe >/dev/null 2>&1; then
  stop_windows_pid_file "$PID_DIR/algorithm.winpid" "Python 算法服务" "wsl.exe" "algorithm_service"
fi
analyze_batch "$failure_batch" "$M3_RUNTIME/failed-analysis.json"
"$PYTHON_VENV/bin/python" - "$M3_RUNTIME/failed-analysis.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] == "FAILED", payload
assert "可重试" in payload["failure"], payload
assert payload["results"] == [] and payload["event_chains"] == [], payload
assert payload.get("summary") is None, payload
PY
failed_run=$(json_value "$M3_RUNTIME/failed-analysis.json" run_id)
assert_analysis_storage "$failed_run" 0 0

(
  cd "$REPOSITORY_ROOT/src/algorithm"
  nohup env ALGORITHM_HOST=127.0.0.1 ALGORITHM_PORT=8001 \
    ALGORITHM_MODEL_FILE="$DEV_SECRET_ROOT/algorithm-model.enc" \
    ALGORITHM_MODEL_KEY_FILE="$DEV_SECRET_ROOT/algorithm-model-key.txt" \
    "$PYTHON_VENV/bin/python" -m algorithm_service \
    </dev/null >"$LOG_DIR/algorithm.log" 2>&1 &
  echo $! > "$PID_DIR/algorithm.pid"
)
wait_for_url "http://127.0.0.1:8001/health" "Python 算法服务"
analyze_batch "$failure_batch" "$M3_RUNTIME/retry-analysis.json"
verify_smoke_analysis "$M3_RUNTIME/retry-analysis.json"
[[ $(json_value "$M3_RUNTIME/retry-analysis.json" attempt) == "2" ]]
echo "算法不可用时失败零结果，恢复后第二次尝试成功。"

demo_file="$M3_RUNTIME/synthetic_demo_20000.csv"
python3 "$REPOSITORY_ROOT/samples/generate_samples.py" --dataset demo --output "$demo_file"
demo_batch=$(import_file "$demo_file" demo)
demo_started=$(monotonic_ms)
analyze_batch "$demo_batch" "$M3_RUNTIME/demo-analysis.json"
demo_finished=$(monotonic_ms)
"$PYTHON_VENV/bin/python" - "$M3_RUNTIME/demo-analysis.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] == "COMPLETED", payload
assert payload["summary"]["input_count"] == 20_000, payload["summary"]
assert payload["summary"]["success_count"] == 20_000, payload["summary"]
assert payload["summary"]["failure_count"] == 0, payload["summary"]
assert len(payload["results"]) == 20_000, len(payload["results"])
assert all(item["evidence"] for item in payload["results"]), payload["results"][:3]
PY
echo "PostgreSQL 17.6 + 实际 Python 的 20000 行分析完成：$((demo_finished - demo_started))ms。"
