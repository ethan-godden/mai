"""Command-line interface. Each command is a thin wrapper over library code."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from mai import __version__
from mai.config import Settings, load_settings
from mai.doctor import run_checks

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
