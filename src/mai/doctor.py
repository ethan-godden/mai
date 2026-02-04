"""Environment checks behind ``mai doctor``.

Each check reports what was found and, when something is wrong, how to fix it.
Checks never change anything on disk.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

import httpx

from mai.config import Settings, Tier

Status = Literal["ok", "warn", "fail"]

MIN_RECOMMENDED_MEMORY_GB = 32


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    fix: str = ""


def total_memory_gb() -> float | None:
    """Physical memory in GiB, or None if the platform does not report it."""
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30
    except (ValueError, OSError, AttributeError):
        return None


def _system_checks() -> list[Check]:
    checks = [Check("Python", "ok", platform.python_version())]

    machine = f"{platform.system()} {platform.machine()}"
    apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
    checks.append(
        Check("Machine", "ok", machine)
        if apple_silicon
        else Check("Machine", "warn", machine, "Mai is developed for Apple Silicon Macs.")
    )

    memory = total_memory_gb()
    if memory is None:
        checks.append(Check("Memory", "warn", "unknown"))
    elif memory < MIN_RECOMMENDED_MEMORY_GB:
        checks.append(
            Check("Memory", "warn", f"{memory:.0f} GB", "Use smaller models; see docs/MODELS.md.")
        )
    else:
        checks.append(Check("Memory", "ok", f"{memory:.0f} GB"))
    return checks


def _config_check(settings: Settings) -> Check:
    if settings.source is None:
        return Check(
            "Config file",
            "warn",
            "none found, using defaults",
            "Copy config.example.toml to config.toml and set the model names.",
        )
    return Check("Config file", "ok", str(settings.source))


def _writable(path: Path) -> bool:
    """True if ``path`` exists and is writable, or could be created."""
    probe = path.resolve()
    while not probe.exists():
        probe = probe.parent
    return probe.is_dir() and os.access(probe, os.W_OK)


def _path_checks(settings: Settings) -> list[Check]:
    checks = []
    for label, path in (
        ("Data directory", settings.paths.data_dir),
        ("Runs directory", settings.paths.runs_dir),
    ):
        ok = _writable(path)
        checks.append(
            Check(label, "ok" if ok else "fail", str(path), "" if ok else "Path is not writable.")
        )
    return checks


async def _list_models(client: httpx.AsyncClient, base_url: str, api_key: str) -> set[str]:
    response = await client.get(
        f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"}
    )
    response.raise_for_status()
    return {item["id"] for item in response.json().get("data", [])}


def _has_model(available: set[str], wanted: str) -> bool:
    # Ollama lists "name:latest" for a model pulled as "name".
    return wanted in available or f"{wanted}:latest" in available


async def _backend_checks(
    settings: Settings, transport: httpx.AsyncBaseTransport | None
) -> list[Check]:
    checks: list[Check] = []
    tiers = {name: settings.llm.tier(name) for name in get_args(Tier)}
    available: dict[str, set[str] | None] = {}

    async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
        for base_url in dict.fromkeys(t.base_url for t in tiers.values()):
            try:
                models = await _list_models(client, base_url, settings.llm.api_key)
            except (httpx.HTTPError, ValueError, KeyError) as error:
                available[base_url] = None
                checks.append(
                    Check(
                        "Inference server",
                        "fail",
                        f"{base_url} unreachable ({type(error).__name__})",
                        "Start the model server; see docs/guide/setup.md.",
                    )
                )
            else:
                available[base_url] = models
                checks.append(Check("Inference server", "ok", f"{base_url} ({len(models)} models)"))

    heavy_configured = settings.llm.heavy is not None and bool(settings.llm.heavy.model)
    for name, tier in tiers.items():
        label = f"Model: {name}"
        if name == "heavy" and not heavy_configured:
            checks.append(Check(label, "ok", "not set (optional), falls back to main"))
            continue
        if not tier.model:
            checks.append(
                Check(label, "fail", "not configured", f"Set [llm.{name}] model in config.toml.")
            )
            continue
        models = available[tier.base_url]
        if models is None:
            checks.append(Check(label, "warn", f"{tier.model} (server unreachable, not verified)"))
        elif _has_model(models, tier.model):
            checks.append(Check(label, "ok", tier.model))
        else:
            checks.append(
                Check(
                    label,
                    "fail",
                    f"{tier.model} not found on {tier.base_url}",
                    "Download the model or correct its name; see docs/guide/setup.md.",
                )
            )
    return checks


async def _search_checks(
    settings: Settings, transport: httpx.AsyncBaseTransport | None
) -> list[Check]:
    if "searxng" not in settings.search.providers:
        return []
    url = settings.search.searxng_url.rstrip("/")
    try:
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
            (await client.get(f"{url}/healthz")).raise_for_status()
    except httpx.HTTPError as error:
        return [
            Check(
                "Search: SearXNG",
                "fail",
                f"{url} unreachable ({type(error).__name__})",
                "Run scripts/searxng.sh setup once, then scripts/searxng.sh run.",
            )
        ]
    return [Check("Search: SearXNG", "ok", url)]


async def run_checks(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[Check]:
    """Run every check. ``transport`` lets tests substitute a fake server."""
    return [
        *_system_checks(),
        _config_check(settings),
        *_path_checks(settings),
        *await _backend_checks(settings, transport),
        *await _search_checks(settings, transport),
    ]
