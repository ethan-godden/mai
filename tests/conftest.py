import pytest

from mai.config import CONFIG_ENV_VAR


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """Keep tests independent of the developer's own config file and environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    for name in list(__import__("os").environ):
        if name.startswith("MAI_"):
            monkeypatch.delenv(name)
