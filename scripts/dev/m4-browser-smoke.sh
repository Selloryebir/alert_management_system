#!/usr/bin/env bash

source "$(dirname "$0")/browser-test-common.sh"

M4_RUNTIME="$RUNTIME_DIR/m4"
mkdir -p "$M4_RUNTIME"

cleanup_m4() {
  "$REPOSITORY_ROOT/scripts/dev/stop.sh" || true
}
trap cleanup_m4 EXIT

prepare_e2e_browser_runtime

"$REPOSITORY_ROOT/scripts/dev/start.sh"

smoke_started=$(monotonic_ms)
env \
  E2E_BASE_URL=http://127.0.0.1:8080 \
  E2E_MODE=smoke \
  E2E_DATASET="$REPOSITORY_ROOT/samples/smoke/synthetic_smoke_utf8.csv" \
  E2E_EXPECTED_TOTAL=300 \
  npm --prefix "$E2E_DIR" run test:smoke
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
  npm --prefix "$E2E_DIR" run test:smoke
demo_finished=$(monotonic_ms)
echo "20000 行页面上传、分析和看板首屏完成：$((demo_finished - demo_started))ms。"
