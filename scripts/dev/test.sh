#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

"$REPOSITORY_ROOT/scripts/dev/bootstrap.sh"
python3 "$REPOSITORY_ROOT/scripts/validate_repository.py"
python3 "$REPOSITORY_ROOT/scripts/validate_automation.py"

if command -v java >/dev/null 2>&1; then
  "$REPOSITORY_ROOT/mvnw" -f "$REPOSITORY_ROOT/src/backend/pom.xml" test
else
  windows_maven_log=$(mktemp)
  trap 'rm -f "$windows_maven_log"' EXIT
  for attempt in 1 2 3; do
    if cmd.exe /d /c "set DEBUG=false&& mvnw.cmd -f src\\backend\\pom.xml test" \
        </dev/null 2>&1 | tee "$windows_maven_log"; then
      break
    fi
    windows_maven_status=${PIPESTATUS[0]}
    if ! grep -Fq "UtilAcceptVsock" "$windows_maven_log" ||
        ! grep -Fq "failed 110" "$windows_maven_log"; then
      exit "$windows_maven_status"
    fi
    if ((attempt == 3)); then
      echo "WSL 到 Windows 的 Maven 启动连续 3 次通信超时。" >&2
      exit "$windows_maven_status"
    fi
    echo "WSL 到 Windows 的 Maven 启动失败，第 $attempt 次有限重试。" >&2
    sleep 2
  done
  rm -f "$windows_maven_log"
  trap - EXIT
fi

(
  cd "$REPOSITORY_ROOT/src/algorithm"
  "$PYTHON_VENV/bin/python" -m pytest
)
(
  cd "$REPOSITORY_ROOT"
  "$PYTHON_VENV/bin/python" -m pytest tests/data -q -s -p no:cacheprovider
)
(
  cd "$REPOSITORY_ROOT"
  "$PYTHON_VENV/bin/python" -m pytest tests/contract -q -s -p no:cacheprovider
)
if [[ "$REPOSITORY_ROOT" == /mnt/?/* ]]; then
  frontend_test_root=$(mktemp -d /tmp/alert-management-frontend.XXXXXX)
  tar --exclude=node_modules --exclude=dist -C "$REPOSITORY_ROOT/src/frontend" -cf - . |
    tar -C "$frontend_test_root" -xf -
  npm --prefix "$frontend_test_root" ci --ignore-scripts
  npm --prefix "$frontend_test_root" test -- --run
else
  npm --prefix "$REPOSITORY_ROOT/src/frontend" test -- --run
fi

echo "仓库、后端、算法、合成数据、跨组件契约和前端测试通过。"
