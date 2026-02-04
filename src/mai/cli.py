"""Command-line interface. Each command is a thin wrapper over library code."""

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from mai import __version__
from mai.config import Settings, Tier, load_settings
from mai.doctor import run_checks
from mai.llm import Completion, LLMError, OpenAICompatClient
from mai.search import PROVIDERS, SearchOutcome, build_providers, http_client, search_all

app = typer.Typer(
    name="mai",
    help="Local-first research agent.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Path to a config.toml (default: auto-discover)."),
]


ASK_SYSTEM_PROMPT = "You are a careful research assistant. Answer accurately and concisely."


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"mai {__version__}")
        raise typer.Exit


def _load(config: Path | None) -> Settings:
    try:
        return load_settings(config)
    except FileNotFoundError as error:
        err_console.print(f"[red]Config file not found:[/red] {error.filename}")
    except (ValidationError, ValueError) as error:
        err_console.print(f"[red]Invalid configuration:[/red]\n{error}")
    raise typer.Exit(code=2)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_print_version, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Local-first research agent."""


@app.command()
def doctor(config: ConfigOption = None) -> None:
    """Check that the machine, configuration and model server are ready."""
    checks = asyncio.run(run_checks(_load(config)))

    marks = {"ok": "[green]ok[/green]", "warn": "[yellow]warn[/yellow]", "fail": "[red]FAIL[/red]"}
    table = Table(show_header=True, header_style="bold")
    for column in ("Check", "Status", "Detail", "How to fix"):
        table.add_column(column)
    for check in checks:
        table.add_row(check.name, marks[check.status], escape(check.detail), escape(check.fix))
    console.print(table)

    failures = sum(check.status == "fail" for check in checks)
    if failures:
        console.print(f"[red]{failures} check(s) failed.[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Ready.[/green]")


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="The question to answer.")],
    tier: Annotated[Tier, typer.Option(help="Model tier to use.")] = "main",
    config: ConfigOption = None,
) -> None:
    """Answer a question with a local model.

    Until milestone M3 this is a plain completion: no web search, no citations.
    """
    settings = _load(config)

    async def run() -> Completion:
        async with OpenAICompatClient(settings.llm) as llm:
            return await llm.complete(
                tier,
                [
                    {"role": "system", "content": ASK_SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                on_text=lambda text: console.print(text, end="", markup=False, highlight=False),
            )

    try:
        completion = asyncio.run(run())
    except LLMError as error:
        err_console.print(f"[red]Model call failed:[/red] {escape(str(error))}")
        raise typer.Exit(code=2) from error

    speed = f"{completion.tokens_per_s:.0f} tok/s" if completion.tokens_per_s else "speed n/a"
    console.print(
        f"\n[dim]{escape(completion.model)} · {completion.completion_tokens or '?'} tokens · "
        f"{completion.duration_s:.1f}s · {speed}[/dim]"
    )


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="What to search for.")],
    provider: Annotated[
        list[str] | None,
        typer.Option(
            "--provider",
            "-p",
            help=f"Provider to use; repeatable. Available: {', '.join(sorted(PROVIDERS))}.",
        ),
    ] = None,
    max_results: Annotated[int | None, typer.Option("--max-results", "-n")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print results as JSON.")] = False,
    config: ConfigOption = None,
) -> None:
    """Search the web and list merged results, without reading the pages."""
    settings = _load(config).search

    async def run() -> SearchOutcome:
        async with http_client(settings) as client:
            providers = build_providers(client, settings, provider)
            return await search_all(
                providers, query, max_results=max_results or settings.max_results
            )

    try:
        outcome = asyncio.run(run())
    except ValueError as error:
        err_console.print(f"[red]{escape(str(error))}[/red]")
        raise typer.Exit(code=2) from error

    for name, message in outcome.errors.items():
        err_console.print(f"[yellow]{escape(name)} failed:[/yellow] {escape(message)}")
    if as_json:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in outcome.results], indent=2))
    else:
        for rank, result in enumerate(outcome.results, start=1):
            source = ", ".join(result.providers + result.engines)
            date = f" · {result.published}" if result.published else ""
            console.print(f"[bold]{rank}. {escape(result.title)}[/bold]")
            console.print(f"   {escape(result.url)}", style="blue", highlight=False, soft_wrap=True)
            console.print(f"   [dim]{escape(source)}{date}[/dim]")
            if result.snippet:
                console.print(f"   {escape(result.snippet[:300])}", highlight=False)
    if not outcome.results:
        err_console.print("[red]No results.[/red]")
        raise typer.Exit(code=1)
