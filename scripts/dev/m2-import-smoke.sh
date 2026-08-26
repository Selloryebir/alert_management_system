#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

"$REPOSITORY_ROOT/scripts/dev/start.sh"

M2_RUNTIME="$RUNTIME_DIR/m2"
mkdir -p "$M2_RUNTIME"
DEFAULT_PROJECT_ID="00000000-0000-0000-0000-000000000001"

monotonic_ms() {
  "$PYTHON_VENV/bin/python" -c 'import time; print(time.monotonic_ns() // 1_000_000)'
}

preview_file() {
  local file_path=$1
  local expected_status=$2
  local expected_rows=$3
  local invalid_name=${4:-}
  local response
  response=$(curl --noproxy '*' --fail --silent --show-error \
    --form "project_id=$DEFAULT_PROJECT_ID" \
    --form "file=@$file_path" http://127.0.0.1:8080/api/v1/imports/preview)
  PREVIEW_JSON="$response" EXPECTED_STATUS="$expected_status" \
      EXPECTED_ROWS="$expected_rows" INVALID_NAME="$invalid_name" \
      INVALID_MANIFEST="$REPOSITORY_ROOT/samples/expected/invalid-summary.json" \
      "$PYTHON_VENV/bin/python" - <<'PY'
import json
import os

payload = json.loads(os.environ["PREVIEW_JSON"])
assert payload["status"] == os.environ["EXPECTED_STATUS"], payload
assert payload["total_rows"] == int(os.environ["EXPECTED_ROWS"]), payload
invalid_name = os.environ["INVALID_NAME"]
if invalid_name:
    manifest = json.load(open(os.environ["INVALID_MANIFEST"], encoding="utf-8"))
    declaration = manifest["files"][invalid_name]
    expected = declaration["file_errors"] + declaration["row_errors"]
    actual = [
        {key: error[key] for key in ("source_row", "field", "code")}
        for error in payload["errors"]
    ]
    assert payload["valid_rows"] == declaration["valid_rows"], payload
    assert payload["error_count"] == len(expected), payload
    assert actual == expected, (actual, expected)
    assert payload["preview_rows"] == [], payload
else:
    assert payload["valid_rows"] == int(os.environ["EXPECTED_ROWS"]), payload
    assert payload["error_count"] == 0 and payload["errors"] == [], payload
print(payload["batch_id"])
PY
}

confirm_batch() {
  local batch_id=$1
  local expected_rows=$2
  local response
  response=$(curl --noproxy '*' --fail --silent --show-error \
    --request POST "http://127.0.0.1:8080/api/v1/imports/$batch_id/confirm")
  CONFIRM_JSON="$response" EXPECTED_ROWS="$expected_rows" \
      "$PYTHON_VENV/bin/python" - <<'PY'
import json
import os

payload = json.loads(os.environ["CONFIRM_JSON"])
assert payload["status"] == "IMPORTED", payload
assert payload["valid_rows"] == int(os.environ["EXPECTED_ROWS"]), payload
PY
  local stored
  stored=$(docker_run exec "$POSTGRES_CONTAINER" psql \
    --username alert_management --dbname alert_management --tuples-only --no-align \
    --command "SELECT COUNT(*) FROM alarm_record WHERE batch_id = '$batch_id';" | tr -d '\r')
  [[ "$stored" == "$expected_rows" ]]
}

normalized_digest() {
  local batch_id=$1
  docker_run exec "$POSTGRES_CONTAINER" psql \
    --username alert_management --dbname alert_management --tuples-only --no-align \
    --command "
      SELECT md5(string_agg(
        concat_ws('|', source_row::text, event_time::text,
          coalesce(return_time::text, '<NULL>'), coalesce(ack_time::text, '<NULL>'),
          site, area, coalesce(unit_name, '<NULL>'), tag, description, priority,
          alarm_state, coalesce(alarm_value::text, '<NULL>'),
          coalesce(threshold::text, '<NULL>'), coalesce(engineering_unit, '<NULL>'),
          source_system, coalesce(operator_name, '<NULL>')), E'\\n' ORDER BY source_row))
        FROM alarm_record WHERE batch_id = '$batch_id';" | tr -d '\r'
}

declare -a smoke_batches=()
for sample in \
    "$REPOSITORY_ROOT/samples/smoke/synthetic_smoke_utf8.csv" \
    "$REPOSITORY_ROOT/samples/smoke/synthetic_smoke_utf8.txt" \
    "$REPOSITORY_ROOT/samples/smoke/synthetic_smoke.xlsx"; do
  batch_id=$(preview_file "$sample" READY 300)
  confirm_batch "$batch_id" 300
  smoke_batches+=("$batch_id")
done

first_digest=$(normalized_digest "${smoke_batches[0]}")
for batch_id in "${smoke_batches[@]:1}"; do
  [[ $(normalized_digest "$batch_id") == "$first_digest" ]]
done
echo "CSV/TXT/XLSX 各 300 行规范化摘要一致：$first_digest"

gb_batch=$(preview_file \
  "$REPOSITORY_ROOT/samples/smoke/synthetic_smoke_gb18030.csv" READY 12)
confirm_batch "$gb_batch" 12
echo "GB18030 中文样例 12 行导入通过。"

for file_name in \
    missing_header.csv \
    required_value_missing.csv \
    invalid_time.csv \
    invalid_enum.csv \
    invalid_number.csv \
    time_order_invalid.csv; do
  data_rows=$(($(wc -l < "$REPOSITORY_ROOT/samples/invalid/$file_name") - 1))
  batch_id=$(preview_file "$REPOSITORY_ROOT/samples/invalid/$file_name" \
    REJECTED "$data_rows" "$file_name")
  stored=$(docker_run exec "$POSTGRES_CONTAINER" psql \
    --username alert_management --dbname alert_management --tuples-only --no-align \
    --command "SELECT (SELECT COUNT(*) FROM import_staging WHERE batch_id = '$batch_id')
                     + (SELECT COUNT(*) FROM alarm_record WHERE batch_id = '$batch_id');" | tr -d '\r')
  [[ "$stored" == "0" ]]
done
echo "非法样例精确命中 1 个文件级错误和 36 个逐行错误；六批均无业务与暂存记录。"

curl --noproxy '*' --fail --silent --show-error \
  --output "$M2_RUNTIME/import-list.json" \
  "http://127.0.0.1:8080/api/v1/imports?project_id=$DEFAULT_PROJECT_ID&limit=20"
curl --noproxy '*' --fail --silent --show-error \
  --output "$M2_RUNTIME/records-first.json" \
  "http://127.0.0.1:8080/api/v1/imports/${smoke_batches[0]}/records?page=0&size=200"
curl --noproxy '*' --fail --silent --show-error \
  --output "$M2_RUNTIME/records-last.json" \
  "http://127.0.0.1:8080/api/v1/imports/${smoke_batches[0]}/records?page=1&size=200"
EXPECTED_BATCH="${smoke_batches[0]}" "$PYTHON_VENV/bin/python" - \
    "$M2_RUNTIME/import-list.json" \
    "$M2_RUNTIME/records-first.json" \
    "$M2_RUNTIME/records-last.json" <<'PY'
import json
import os
import sys

batches = json.load(open(sys.argv[1], encoding="utf-8"))
assert 1 <= len(batches) <= 20, batches
assert os.environ["EXPECTED_BATCH"] in {item["batch_id"] for item in batches}, batches
assert all(item["preview_rows"] == [] for item in batches), batches
first = json.load(open(sys.argv[2], encoding="utf-8"))
last = json.load(open(sys.argv[3], encoding="utf-8"))
assert (first["total"], first["page"], first["size"], len(first["items"])) == (300, 0, 200, 200), first
assert (last["total"], last["page"], last["size"], len(last["items"])) == (300, 1, 200, 100), last
assert first["items"][0]["source_row"] == 2, first["items"][0]
assert last["items"][-1]["source_row"] == 301, last["items"][-1]
assert first["items"][0]["raw_payload"]["source_row"] == "2", first["items"][0]
PY
echo "批次列表与 300 行分页原始记录追溯通过。"

duplicate_status=$(curl --noproxy '*' --silent --output "$M2_RUNTIME/repeat-confirm.json" \
  --write-out '%{http_code}' --request POST \
  "http://127.0.0.1:8080/api/v1/imports/${smoke_batches[0]}/confirm")
[[ "$duplicate_status" == "409" ]]
REPEAT_JSON=$(cat "$M2_RUNTIME/repeat-confirm.json") \
  "$PYTHON_VENV/bin/python" - <<'PY'
import json
import os

payload = json.loads(os.environ["REPEAT_JSON"])
assert payload["code"] == "IMPORT_STATUS_CONFLICT", payload
assert payload["message"] == "批次已经确认导入，不能重复确认", payload
assert payload["trace_id"], payload
PY
echo "重复确认返回 HTTP 409，原批次仍为 300 行。"

demo_file="$M2_RUNTIME/synthetic_demo_20000.csv"
python3 "$REPOSITORY_ROOT/samples/generate_samples.py" \
  --dataset demo --output "$demo_file"
demo_started=$(monotonic_ms)
demo_batch=$(preview_file "$demo_file" READY 20000)
demo_preview_done=$(monotonic_ms)
confirm_batch "$demo_batch" 20000
demo_confirm_done=$(monotonic_ms)
echo "PostgreSQL 17.6 Demo 导入通过：batch_id=$demo_batch，文件=$(wc -c < "$demo_file") bytes，" \
  "预览校验=$((demo_preview_done - demo_started))ms，确认落库=$((demo_confirm_done - demo_preview_done))ms，" \
  "总计=$((demo_confirm_done - demo_started))ms。"
