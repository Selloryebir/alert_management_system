"""使用 ``python -m algorithm_service`` 启动服务。"""

import sys

import uvicorn

from algorithm_service.app import app
from algorithm_service.config import ConfigurationError, load_settings


def main() -> int:
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    uvicorn.run(app, host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
