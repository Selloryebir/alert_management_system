#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

"$REPOSITORY_ROOT/scripts/dev/bootstrap.sh"

if command -v java >/dev/null 2>&1; then
  "$REPOSITORY_ROOT/mvnw" -f "$REPOSITORY_ROOT/src/backend/pom.xml" test
else
  cmd.exe /d /c "set DEBUG=false&& mvnw.cmd -f src\\backend\\pom.xml test" </dev/null
fi

"$PYTHON_VENV/bin/python" -m ruff check \
  --config "$REPOSITORY_ROOT/src/algorithm/pyproject.toml" \
  "$REPOSITORY_ROOT/src/algorithm" "$REPOSITORY_ROOT/tools/model-training"
npm --prefix "$REPOSITORY_ROOT/src/frontend" run lint

echo "Java、Python 和前端质量检查通过。"
