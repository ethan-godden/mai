"""Command-line interface. Each command is a thin wrapper over library code."""

from typing import Annotated

import typer

from mai import __version__

app = typer.Typer(
    name="mai",
    help="Local-first research agent.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"mai {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_print_version, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Local-first research agent."""
