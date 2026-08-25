from algorithm_service import __main__ as service_main
from algorithm_service.config import ConfigurationError, Settings, load_settings

import pytest


def test_default_settings_bind_to_local_demo_port() -> None:
    assert load_settings({}) == Settings(host="127.0.0.1", port=8001)


@pytest.mark.parametrize("raw_port", ["", "not-a-port", "0", "65536"])
def test_invalid_port_is_rejected(raw_port: str) -> None:
    with pytest.raises(ConfigurationError):
        load_settings({"ALGORITHM_PORT": raw_port})


def test_empty_host_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="ALGORITHM_HOST"):
        load_settings({"ALGORITHM_HOST": "  "})


def test_main_reports_configuration_error_without_starting_server(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ALGORITHM_PORT", "invalid")
    started = False

    def fake_run(*args: object, **kwargs: object) -> None:
        nonlocal started
        started = True

    monkeypatch.setattr(service_main.uvicorn, "run", fake_run)

    assert service_main.main() == 2
    assert started is False
    assert "配置错误：ALGORITHM_PORT 必须是整数" in capsys.readouterr().err
