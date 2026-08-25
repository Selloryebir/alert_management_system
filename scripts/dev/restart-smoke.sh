#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

"$REPOSITORY_ROOT/scripts/dev/stop.sh"
"$REPOSITORY_ROOT/scripts/dev/start.sh"
"$REPOSITORY_ROOT/scripts/dev/status.sh"
"$REPOSITORY_ROOT/scripts/dev/stop.sh"
"$REPOSITORY_ROOT/scripts/dev/start.sh"
"$REPOSITORY_ROOT/scripts/dev/status.sh"

if ! grep -Fq 'Schema "public" is up to date. No migration necessary.' \
    "$LOG_DIR/backend.log"; then
  echo "第二次启动未证明 Flyway 迁移幂等。" >&2
  exit 1
fi

echo "M1 两轮启停、聚合健康、静态页面和迁移幂等检查通过。"
