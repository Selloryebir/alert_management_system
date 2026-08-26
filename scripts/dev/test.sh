#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

"$REPOSITORY_ROOT/scripts/dev/bootstrap.sh"
python3 "$REPOSITORY_ROOT/scripts/validate_repository.py"
python3 "$REPOSITORY_ROOT/scripts/validate_automation.py"

if command -v java >/dev/null 2>&1; then
  "$REPOSITORY_ROOT/mvnw" -f "$REPOSITORY_ROOT/src/backend/pom.xml" test
else
  for attempt in 1 2 3; do
    if cmd.exe /d /c "set DEBUG=false&& mvnw.cmd -f src\\backend\\pom.xml test" </dev/null; then
      break
    fi
    if ((attempt == 3)); then
      echo "Windows Maven 测试连续 3 次启动失败。" >&2
      exit 1
    fi
    echo "WSL 到 Windows 的 Maven 启动失败，第 $attempt 次有限重试。" >&2
    sleep 2
  done
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
npm --prefix "$REPOSITORY_ROOT/src/frontend" test -- --run

echo "仓库、后端、算法、合成数据、跨组件契约和前端测试通过。"
