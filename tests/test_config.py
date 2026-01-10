import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from mai.config import CONFIG_ENV_VAR, DEFAULT_BASE_URL, load_settings

EXAMPLE = Path(__file__).parent.parent / "config.example.toml"


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_defaults_when_no_file():
    settings = load_settings()
    assert settings.source is None
    assert settings.llm.base_url == DEFAULT_BASE_URL
    assert settings.llm.tier("main").model == ""


def test_loads_discovered_file(tmp_path):
    write(tmp_path / "config.toml", '[llm.main]\nmodel = "m"\n')
    settings = load_settings()
    assert settings.source == Path("config.toml")
    assert settings.llm.main.model == "m"


def test_config_env_var_selects_file(tmp_path, monkeypatch):
    path = write(tmp_path / "other.toml", '[llm.fast]\nmodel = "f"\n')
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    assert load_settings().llm.fast.model == "f"


def test_environment_overrides_file(tmp_path, monkeypatch):
    path = write(
        tmp_path / "c.toml", '[llm]\nbase_url = "http://file/v1"\n[llm.main]\nmodel = "m"\n'
    )
    monkeypatch.setenv("MAI_LLM__BASE_URL", "http://env/v1")
    settings = load_settings(path)
    assert settings.llm.base_url == "http://env/v1"
    assert settings.llm.main.model == "m"  # untouched file values survive


def test_unknown_key_is_rejected(tmp_path):
    path = write(tmp_path / "c.toml", '[llm]\nbase_urll = "typo"\n')
    with pytest.raises(ValidationError):
        load_settings(path)


def test_unknown_section_is_rejected(tmp_path):
    path = write(tmp_path / "c.toml", '[lmm]\nbase_url = "typo"\n')
    with pytest.raises(ValidationError):
        load_settings(path)


def test_missing_explicit_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.toml")


def test_tier_resolution_and_fallbacks(tmp_path):
    path = write(
        tmp_path / "c.toml",
        '[llm]\nbase_url = "http://shared/v1/"\n'
        '[llm.fast]\nmodel = "f"\nbase_url = "http://fast/v1"\n'
        '[llm.main]\nmodel = "m"\nmax_tokens = 4096\n',
    )
    llm = load_settings(path).llm
    assert llm.tier("fast").base_url == "http://fast/v1"
    assert llm.tier("main").base_url == "http://shared/v1"  # trailing slash removed
    heavy = llm.tier("heavy")
    assert (heavy.name, heavy.model, heavy.max_tokens) == ("heavy", "m", 4096)


def test_example_config_is_valid():
    load_settings(EXAMPLE)
    assert "llm" in tomllib.loads(EXAMPLE.read_text())
