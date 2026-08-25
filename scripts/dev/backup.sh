#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

backup_dir="$RUNTIME_DIR/backups"
mkdir -p "$backup_dir"

container_label=$(docker_run inspect \
  --format '{{ index .Config.Labels "alert-management-demo" }}' "$POSTGRES_CONTAINER" 2>/dev/null || true)
if [[ "$container_label" != "m1" ]]; then
  echo "拒绝备份：未确认目标是本项目 PostgreSQL 容器 $POSTGRES_CONTAINER。" >&2
  exit 1
fi
if [[ $(docker_run inspect --format '{{.State.Running}}' "$POSTGRES_CONTAINER") != "true" ]]; then
  echo "拒绝备份：本项目 PostgreSQL 容器未运行。" >&2
  exit 1
fi

backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_name="alert-management-$backup_stamp.dump"
backup_path="$backup_dir/$backup_name"
container_path="/tmp/$backup_name"
if [[ -e "$backup_path" ]]; then
  echo "拒绝覆盖已存在的备份：$backup_path" >&2
  exit 1
fi

cleanup_container_backup() {
  docker_run exec "$POSTGRES_CONTAINER" rm -f "$container_path" >/dev/null 2>&1 || true
}
trap cleanup_container_backup EXIT

docker_run exec "$POSTGRES_CONTAINER" pg_dump \
  --username alert_management \
  --dbname alert_management \
  --format custom \
  --file "$container_path"
docker_run exec "$POSTGRES_CONTAINER" pg_restore --list "$container_path" >/dev/null
docker_bin=$(find_docker)
copy_target="$backup_path"
if [[ $(basename "$docker_bin") == "docker.exe" ]]; then
  copy_target=$(wslpath -w "$backup_path")
fi
"$docker_bin" cp "$POSTGRES_CONTAINER:$container_path" "$copy_target"

if [[ ! -s "$backup_path" ]] || [[ $(head -c 5 "$backup_path") != "PGDMP" ]]; then
  echo "备份文件不可读或格式错误：$backup_path" >&2
  exit 1
fi
echo "项目 PostgreSQL 备份可读：$backup_path ($(wc -c < "$backup_path") bytes)"
