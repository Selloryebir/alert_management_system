#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

E2E_DIR="$REPOSITORY_ROOT/tests/e2e"

monotonic_ms() {
  "$PYTHON_VENV/bin/python" -c 'import time; print(time.monotonic_ns() // 1_000_000)'
}

prepare_e2e_browser_runtime() {
  if [[ ${E2E_DEPS_READY:-0} != "1" ]]; then
    npm ci --prefix "$E2E_DIR"
  fi
  if [[ ${E2E_BROWSER_READY:-0} != "1" ]]; then
    npm --prefix "$E2E_DIR" run install:chromium
  fi

  local browser_path
  browser_path=$(node -e \
    'const { chromium } = require(process.argv[1]); process.stdout.write(chromium.executablePath())' \
    "$E2E_DIR/node_modules/playwright")
  local dependency_root="$RUNTIME_DIR/playwright-deps/root"
  if [[ -d "$dependency_root/usr/lib/x86_64-linux-gnu" ]]; then
    export LD_LIBRARY_PATH="$dependency_root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi
  if ! ldd "$browser_path" 2>/dev/null | grep -q 'not found'; then
    return
  fi
  if ! command -v apt-get >/dev/null 2>&1 || ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "Chromium 缺少系统运行库；请执行 playwright install --with-deps chromium。" >&2
    return 1
  fi
  local package_dir="$RUNTIME_DIR/playwright-deps/packages"
  mkdir -p "$package_dir" "$dependency_root"
  (
    cd "$package_dir"
    apt-get download libnspr4 libnss3 libasound2t64
    for package_file in ./*.deb; do
      dpkg-deb -x "$package_file" "$dependency_root"
    done
  )
  export LD_LIBRARY_PATH="$dependency_root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  if ldd "$browser_path" 2>/dev/null | grep -q 'not found'; then
    echo "Chromium 运行库仍不完整；请执行 playwright install --with-deps chromium。" >&2
    return 1
  fi
}
