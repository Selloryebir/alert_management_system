#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

health_json=$(curl --noproxy '*' --fail --silent --show-error \
  http://127.0.0.1:8080/api/v1/health)
HEALTH_JSON="$health_json" "$PYTHON_VENV/bin/python" - <<'PY'
import json
import os

payload = json.loads(os.environ["HEALTH_JSON"])
assert payload["status"] == "UP", payload
assert payload["identity"] == "报警管理系统", payload
assert payload["components"] == {
    "system": {"status": "UP"},
    "database": {"status": "UP"},
    "algorithm": {"status": "UP"},
}, payload
print("聚合健康检查通过：system/database/algorithm 均为 UP。")
PY

page=$(curl --noproxy '*' --fail --silent --show-error http://127.0.0.1:8080/)
asset_path=$(sed -n 's/.*src="\([^"]*\/assets\/index-[^"]*\.js\)".*/\1/p' <<<"$page")
if [[ -z "$asset_path" ]]; then
  echo "根页面未引用预期的 Vue 构建资源。" >&2
  exit 1
fi
bundle=$(curl --noproxy '*' --fail --silent --show-error \
  "http://127.0.0.1:8080$asset_path")
grep -Fq "报警管理系统" <<<"$bundle"
grep -Fq "仅使用合成数据" <<<"$bundle"

migration_table=$(docker_run exec "$POSTGRES_CONTAINER" psql \
  --username alert_management --dbname alert_management --tuples-only --no-align \
  --command "SELECT to_regclass('public.app_metadata');" | tr -d '\r')
if [[ "$migration_table" != "app_metadata" ]]; then
  echo "Flyway 迁移验证表不存在：$migration_table" >&2
  exit 1
fi

echo "静态页面身份与 Flyway 迁移检查通过。"
