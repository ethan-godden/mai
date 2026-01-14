"""Typed settings for Mai.

Settings come from three places, highest priority first:

1. Environment variables prefixed ``MAI_``, with ``__`` between nesting levels
   (for example ``MAI_LLM__BASE_URL`` or ``MAI_LLM__MAIN__MODEL``).
2. A TOML file: the path in ``MAI_CONFIG``, else ``./config.toml``, else
   ``~/.config/mai/config.toml``.
3. The defaults defined in this module.

See ``config.example.toml`` for an annotated example.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

Tier = Literal["fast", "main", "heavy"]
"""Model tiers. See docs/MODELS.md for what each one is used for."""

CONFIG_ENV_VAR = "MAI_CONFIG"
DEFAULT_BASE_URL = "http://localhost:11434/v1"


class _Section(BaseModel):
    # Typos in a config file should fail loudly rather than be ignored.
    model_config = ConfigDict(extra="forbid")


class TierSettings(_Section):
    """One model tier. ``base_url`` overrides the shared endpoint when set."""

    model: str = ""
    base_url: str | None = None
    max_tokens: int = 2048
    temperature: float = 0.2
    reasoning_effort: str | None = None
    """Passed through as the OpenAI ``reasoning_effort`` parameter when set
    (for example "none" or "low"), for models that think before answering."""


@dataclass(frozen=True)
class ResolvedTier:
    """A tier with every fallback applied; what the LLM client actually uses."""

    name: Tier
    model: str
    base_url: str
    api_key: str
    max_tokens: int
    temperature: float
    reasoning_effort: str | None


class LLMSettings(_Section):
    """Connection to the local OpenAI-compatible inference server(s)."""

    base_url: str = DEFAULT_BASE_URL
    api_key: str = "local"  # local servers ignore it, but the header must be present
    timeout_s: float = 600.0
    max_retries: int = 2
    fast: TierSettings = Field(default_factory=TierSettings)
    main: TierSettings = Field(default_factory=TierSettings)
    heavy: TierSettings | None = None

    def tier(self, name: Tier) -> ResolvedTier:
        """Resolve a tier. An unconfigured ``heavy`` tier falls back to ``main``."""
        chosen: TierSettings = getattr(self, name) or self.main
        if name == "heavy" and not chosen.model:
            chosen = self.main
        return ResolvedTier(
            name=name,
            model=chosen.model,
            base_url=(chosen.base_url or self.base_url).rstrip("/"),
            api_key=self.api_key,
            max_tokens=chosen.max_tokens,
            temperature=chosen.temperature,
            reasoning_effort=chosen.reasoning_effort,
        )


class PathSettings(_Section):
    """Where runtime data lives. Both directories are git-ignored."""

    data_dir: Path = Path("data")
    runs_dir: Path = Path("runs")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAI_", env_nested_delimiter="__", extra="forbid")

    llm: LLMSettings = Field(default_factory=LLMSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    source: Path | None = Field(default=None, exclude=True)
    """The config file these settings were loaded from, if any."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # File values arrive as init kwargs (see load_settings); environment wins.
        return env_settings, init_settings


def find_config_file() -> Path | None:
    """Return the config file to use, or None to run on defaults."""
    explicit = os.environ.get(CONFIG_ENV_VAR)
    if explicit:
        return Path(explicit).expanduser()
    for candidate in (Path("config.toml"), Path.home() / ".config" / "mai" / "config.toml"):
        if candidate.is_file():
            return candidate
    return None


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from ``path`` (or the discovered config file) plus the environment.

    Raises FileNotFoundError if an explicitly requested file is missing, and
    pydantic.ValidationError if the file contains unknown or invalid values.
    """
    path = path or find_config_file()
    data: dict[str, Any] = {}
    if path is not None:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    return Settings(**data, source=path)
