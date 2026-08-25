#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

PYTHON_VERSION="3.12.14+20260814"
PYTHON_ARCHIVE="cpython-3.12.14+20260814-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260814/cpython-3.12.14%2B20260814-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYTHON_SHA256="3297691ae34f75fed81ac424e040145fccb0bafe8e581cd5cadbddfa1c0766c0"

python_source=$PYTHON_RUNTIME
if [[ ! -x "$PYTHON_RUNTIME" ]] && command -v python3.12 >/dev/null 2>&1; then
  python_source=$(command -v python3.12)
elif [[ ! -x "$PYTHON_RUNTIME" ]]; then
  archive_path="$RUNTIME_DIR/$PYTHON_ARCHIVE"
  echo "下载项目级 Python $PYTHON_VERSION..."
  curl --fail --location --retry 3 --output "$archive_path" "$PYTHON_URL"
  printf '%s  %s\n' "$PYTHON_SHA256" "$archive_path" | sha256sum --check --status
  mkdir -p "$RUNTIME_DIR/python"
  tar -xzf "$archive_path" --strip-components=1 -C "$RUNTIME_DIR/python"
fi

if [[ ! -x "$PYTHON_VENV/bin/python" ]]; then
  "$python_source" -m venv "$PYTHON_VENV"
fi
python_lock_hash=$(sha256sum "$REPOSITORY_ROOT/src/algorithm/requirements.lock" | cut -d ' ' -f 1)
python_stamp="$RUNTIME_DIR/python-requirements.sha256"
if [[ ! -f "$python_stamp" ]] || [[ $(cat "$python_stamp") != "$python_lock_hash" ]]; then
  "$PYTHON_VENV/bin/python" -m pip install --disable-pip-version-check \
    --requirement "$REPOSITORY_ROOT/src/algorithm/requirements.lock"
  printf '%s\n' "$python_lock_hash" > "$python_stamp"
else
  "$PYTHON_VENV/bin/python" -m pip check >/dev/null
fi

node_version=$(node --version | sed 's/^v//')
if ! printf '%s\n%s\n' "22.12.0" "$node_version" | sort -V -C \
    || [[ "$node_version" != 22.* ]]; then
  echo "需要 Node.js 22.12.0 或更高的 22.x，当前为 $(node --version)。" >&2
  exit 1
fi
frontend_lock_hash=$(sha256sum "$REPOSITORY_ROOT/src/frontend/package-lock.json" | cut -d ' ' -f 1)
frontend_stamp="$RUNTIME_DIR/frontend-package-lock.sha256"
if [[ ! -f "$frontend_stamp" ]] || [[ $(cat "$frontend_stamp") != "$frontend_lock_hash" ]] \
    || [[ ! -d "$REPOSITORY_ROOT/src/frontend/node_modules" ]]; then
  npm --prefix "$REPOSITORY_ROOT/src/frontend" ci
  printf '%s\n' "$frontend_lock_hash" > "$frontend_stamp"
fi

java_bin=$(find_java)
java_version=$("$java_bin" -version 2>&1 | head -n 1)
if [[ "$java_version" != *'21.'* ]]; then
  echo "需要 Java 21，当前为：$java_version" >&2
  exit 1
fi

actual_python=$($PYTHON_VENV/bin/python --version)
echo "M1 开发依赖准备完成：$actual_python、Node $(node --version)、$java_version"
