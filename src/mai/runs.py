"""Run records: everything a run did, saved as plain files.

Each run gets a directory under the runs folder:

    runs/20261123-142501-is-it-worth-replacing-a-gas-boiler/
        report.md     the answer and its sources, for reading
        run.json      queries, search results, sources, passages, timings, settings
        trace.jsonl   every model call: prompt, output, tokens, timing

They are plain files on purpose: a run can be read, diffed, searched, and
later reused as evaluation or fine-tuning data without any tooling.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from mai import __version__
from mai.config import Settings
from mai.llm import CallRecord
from mai.quick import QuickResult, sources_footer

RUN_FORMAT = 1
"""Bump when run.json changes shape in a way readers must know about."""

_SECRET_FIELDS = {"llm": {"api_key"}, "search": {"semantic_scholar_api_key"}}


def run_slug(question: str, max_length: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "run"


def settings_snapshot(settings: Settings) -> dict[str, Any]:
    """The settings a run used, without secrets."""
    snapshot = settings.model_dump(mode="json", exclude={"source"})
    for section, fields in _SECRET_FIELDS.items():
        for name in fields:
            snapshot.get(section, {}).pop(name, None)
    return snapshot


def _json_default(value: object) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[union-attr]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[union-attr]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    raise TypeError(f"Cannot serialise {type(value).__name__}")


def _dump(value: Any, **kwargs: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False, **kwargs)


def render_report(result: QuickResult, started: datetime) -> str:
    """The human-readable report for a quick run, as Markdown."""
    parts = [f"# {result.question}", result.answer.strip()]
    if result.sources:
        parts.append("## Sources\n\n" + sources_footer(result))
    notes = []
    snippet_only = [s.number for s in result.cited_sources() if s.snippet_only]
    if snippet_only:
        numbers = ", ".join(f"[{n}]" for n in snippet_only)
        notes.append(
            f"- Based on a search summary only, because the page could not be read: {numbers}"
        )
    if result.invalid_citations:
        numbers = ", ".join(f"[{n}]" for n in result.invalid_citations)
        notes.append(f"- The answer cites {numbers}, which match no source.")
    if result.status == "answered" and not result.cited:
        notes.append("- The answer contains no citations.")
    if notes:
        parts.append("## Notes\n\n" + "\n".join(notes))
    model = result.completion.model if result.completion else "no model"
    parts.append(
        f"---\n\n*Mai {__version__}, quick mode, {started:%Y-%m-%d %H:%M}, {model}. "
        "Citations are not yet checked against the sources automatically.*"
    )
    return "\n\n".join(parts) + "\n"


def save_run(
    result: QuickResult,
    runs_dir: Path,
    *,
    settings: Settings,
    started: datetime,
    calls: Sequence[CallRecord] = (),
) -> Path:
    """Write the run's files and return its directory."""
    base = f"{started:%Y%m%d-%H%M%S}-{run_slug(result.question)}"
    directory = runs_dir / base
    suffix = 2
    while directory.exists():  # two runs of one question in the same second
        directory = runs_dir / f"{base}-{suffix}"
        suffix += 1
    directory.mkdir(parents=True)

    record = {
        "format": RUN_FORMAT,
        "mai_version": __version__,
        "mode": "quick",
        "started": started,
        **dataclasses.asdict(result),
        "search_results": result.search_results,  # pydantic models, not dataclasses
        "settings": settings_snapshot(settings),
    }
    (directory / "run.json").write_text(_dump(record, indent=2) + "\n")
    (directory / "report.md").write_text(render_report(result, started))
    with (directory / "trace.jsonl").open("w") as trace:
        for call in calls:
            trace.write(_dump({"messages": call.messages, **dataclasses.asdict(call.completion)}))
            trace.write("\n")
    return directory
