"""算法服务进程配置。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001


class ConfigurationError(ValueError):
    """启动配置不可用。"""


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """读取环境变量并在启动服务前拒绝无效配置。"""

    values = os.environ if environ is None else environ
    host = values.get("ALGORITHM_HOST", DEFAULT_HOST).strip()
    if not host:
        raise ConfigurationError("ALGORITHM_HOST 不能为空")

    raw_port = values.get("ALGORITHM_PORT", str(DEFAULT_PORT)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ConfigurationError("ALGORITHM_PORT 必须是整数") from exc
    if not 1 <= port <= 65535:
        raise ConfigurationError("ALGORITHM_PORT 必须在 1 到 65535 之间")

    return Settings(host=host, port=port)
