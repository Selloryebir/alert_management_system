#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

E2E_DIR="$REPOSITORY_ROOT/tests/e2e"
M4_RUNTIME="$RUNTIME_DIR/m4"
mkdir -p "$M4_RUNTIME"

monotonic_ms() {
  "$PYTHON_VENV/bin/python" -c 'import time; print(time.monotonic_ns() // 1_000_000)'
}

prepare_browser_runtime() {
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

if [[ ${E2E_DEPS_READY:-0} != "1" ]]; then
  npm ci --prefix "$E2E_DIR"
fi
if [[ ${E2E_BROWSER_READY:-0} != "1" ]]; then
  npm --prefix "$E2E_DIR" run install:chromium
fi
prepare_browser_runtime

"$REPOSITORY_ROOT/scripts/dev/start.sh"

smoke_started=$(monotonic_ms)
env \
  E2E_BASE_URL=http://127.0.0.1:8080 \
  E2E_MODE=smoke \
  E2E_DATASET="$REPOSITORY_ROOT/samples/smoke/synthetic_smoke_utf8.csv" \
  E2E_EXPECTED_TOTAL=300 \
  npm --prefix "$E2E_DIR" test
smoke_finished=$(monotonic_ms)
echo "300 行浏览器业务闭环完成：$((smoke_finished - smoke_started))ms。"

demo_file="$M4_RUNTIME/synthetic_demo_20000.csv"
python3 "$REPOSITORY_ROOT/samples/generate_samples.py" --dataset demo --output "$demo_file"
demo_started=$(monotonic_ms)
env \
  E2E_BASE_URL=http://127.0.0.1:8080 \
  E2E_MODE=demo \
  E2E_DATASET="$demo_file" \
  E2E_EXPECTED_TOTAL=20000 \
  npm --prefix "$E2E_DIR" test
demo_finished=$(monotonic_ms)
echo "20000 行页面上传、分析和看板首屏完成：$((demo_finished - demo_started))ms。"
