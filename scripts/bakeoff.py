"""Run the model bake-off and save results.

Usage:
    uv run python scripts/bakeoff.py MODEL [MODEL ...] [options]

Examples:
    uv run python scripts/bakeoff.py qwen3.8:27b qwen3.8:27b-q4_K_M
    uv run python scripts/bakeoff.py some-model --base-url http://localhost:8080/v1
    uv run python scripts/bakeoff.py qwen3.8:27b --reasoning-effort none

Each model is run through every task in src/mai/bakeoff.py, one request at a
time so speed figures are not distorted by concurrency. For every model a JSON
file with scores, speed and the raw outputs is written to the results
directory, and a Markdown comparison table is printed for docs/MODELS.md.

Close other heavy applications first: speed and memory figures are only
comparable if the machine is otherwise idle.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from mai.bakeoff import BakeoffData, render_table, run_bakeoff
from mai.config import DEFAULT_BASE_URL, LLMSettings, TierSettings
from mai.doctor import total_memory_gb
from mai.llm import LLMError, OpenAICompatClient

ROOT = Path(__file__).resolve().parent.parent


async def ollama_details(base_url: str, model: str) -> dict[str, Any]:
    """Best-effort backend facts. Returns {} for servers that are not Ollama."""
    root = base_url.removesuffix("/v1")
    details: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            version = await client.get(f"{root}/api/version")
            details["backend"] = f"ollama {version.json()['version']}"
            show = await client.post(f"{root}/api/show", json={"model": model})
            info = show.json().get("details", {})
            details["format"] = info.get("format")
            details["quantization"] = info.get("quantization_level")
            details["runner"] = info.get("runner") or (
                "llamacpp" if info.get("format") == "gguf" else None
            )
            running = await client.get(f"{root}/api/ps")
            for entry in running.json().get("models", []):
                if entry.get("name") == model:
                    details["memory_gb"] = round(entry["size"] / 2**30, 1)
    except (httpx.HTTPError, KeyError, ValueError):
        pass
    return details


async def run_model(args: argparse.Namespace, model: str, data: BakeoffData) -> dict[str, Any]:
    settings = LLMSettings(
        base_url=args.base_url,
        main=TierSettings(
            model=model,
            max_tokens=args.max_tokens,
            temperature=0.0,
            reasoning_effort=args.reasoning_effort,
        ),
    )
    async with OpenAICompatClient(settings) as llm:
        # Load the model before timing anything.
        await llm.complete(
            "main", [{"role": "user", "content": "Reply with: ready"}], max_tokens=16
        )
        result = await run_bakeoff(llm, data)

    label = model if not args.reasoning_effort else f"{model} (effort={args.reasoning_effort})"
    return {
        "label": label,
        "model": model,
        "base_url": args.base_url,
        "reasoning_effort": args.reasoning_effort,
        "date": datetime.now().isoformat(timespec="seconds"),
        "machine": f"{platform.machine()} {platform.platform()}, {total_memory_gb():.0f} GB",
        **await ollama_details(args.base_url, model),
        **result,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Mai model bake-off.")
    parser.add_argument("models", nargs="+", help="Model names as the server knows them.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--reasoning-effort", default=None, help='For example "none" or "low".')
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--data", type=Path, default=ROOT / "evals" / "bakeoff")
    parser.add_argument("--out", type=Path, default=ROOT / "evals" / "bakeoff" / "results")
    args = parser.parse_args()

    data = BakeoffData.load(args.data)
    args.out.mkdir(parents=True, exist_ok=True)
    runs = []
    for model in args.models:
        print(f"Running {model} ...", flush=True)
        try:
            run = await run_model(args, model, data)
        except LLMError as error:
            print(f"  failed: {error}")
            continue
        slug = re.sub(r"[^A-Za-z0-9.]+", "-", run["label"]).strip("-")
        path = args.out / f"{datetime.now():%Y-%m-%d}-{slug}.json"
        path.write_text(json.dumps(run, indent=2) + "\n")
        print(f"  saved {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
        runs.append(run)

    if runs:
        print("\n" + render_table(runs))


if __name__ == "__main__":
    asyncio.run(main())
