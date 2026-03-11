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
from mai.extract import Document, ExtractError
from mai.fetch import Fetcher, FetchError
from mai.llm import Completion, LLMError, OpenAICompatClient
from mai.quick import QuickResult, quick_answer
from mai.read import read_url
from mai.render import make_renderer
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


def _ask_plain(settings: Settings, question: str, tier: Tier) -> None:
    """Answer from the model alone: no search, no citations."""

    async def run() -> Completion:
        async with OpenAICompatClient(settings.llm) as llm:
            return await llm.complete(
                tier,
                [
                    {"role": "system", "content": ASK_SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                on_text=_stream,
            )

    completion = asyncio.run(run())
    speed = f"{completion.tokens_per_s:.0f} tok/s" if completion.tokens_per_s else "speed n/a"
    console.print(
        f"\n[dim]{escape(completion.model)} · {completion.completion_tokens or '?'} tokens · "
        f"{completion.duration_s:.1f}s · {speed} · no search, no citations[/dim]"
    )


def _stream(text: str) -> None:
    console.print(text, end="", markup=False, highlight=False)


def _status(message: str) -> None:
    err_console.print(f"[dim]{escape(message)}[/dim]", highlight=False, soft_wrap=True)


async def _run_quick(settings: Settings, question: str, provider: list[str] | None) -> QuickResult:
    renderer = make_renderer(settings.fetch)
    try:
        async with (
            OpenAICompatClient(settings.llm) as llm,
            http_client(settings.search) as client,
            Fetcher(settings.fetch, settings.paths.data_dir / "cache" / "pages") as fetcher,
        ):
            return await quick_answer(
                question,
                llm=llm,
                providers=build_providers(client, settings.search, provider),
                fetcher=fetcher,
                settings=settings.quick,
                renderer=renderer,
                on_status=_status,
                on_text=_stream,
            )
    finally:
        if renderer is not None:
            await renderer.aclose()


def _print_quick_result(result: QuickResult) -> None:
    for name, message in result.search_errors.items():
        err_console.print(f"[yellow]{escape(name)} failed:[/yellow] {escape(message)}")
    if result.status == "no_sources":
        console.print(result.answer)
        return

    console.print("\n\n[bold]Sources[/bold]")
    for source in result.cited_sources() or result.sources:
        details = [str(part) for part in (source.site, source.published) if part]
        if source.snippet_only:
            details.append("search summary only, page not read")
        suffix = f" [dim]({escape(', '.join(details))})[/dim]" if details else ""
        console.print(f"[{source.number}] {escape(source.title)}{suffix}", highlight=False)
        console.print(f"    {escape(source.url)}", style="blue", highlight=False, soft_wrap=True)

    if result.invalid_citations:
        numbers = ", ".join(f"[{n}]" for n in result.invalid_citations)
        err_console.print(
            f"[yellow]Warning:[/yellow] the answer cites {numbers}, which match no source."
        )
    if not result.cited:
        err_console.print("[yellow]Warning:[/yellow] the answer contains no citations.")
    stages = " · ".join(f"{stage} {seconds:.1f}s" for stage, seconds in result.timings.items())
    total = sum(result.timings.values())
    model = result.completion.model if result.completion else "?"
    console.print(f"\n[dim]{total:.1f}s total ({stages}) · {escape(model)}[/dim]", highlight=False)


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="The question to answer.")],
    provider: Annotated[
        list[str] | None,
        typer.Option(
            "--provider",
            "-p",
            help=f"Search provider to use; repeatable. Available: {', '.join(sorted(PROVIDERS))}.",
        ),
    ] = None,
    no_search: Annotated[
        bool,
        typer.Option("--no-search", help="Answer from the model alone, without sources."),
    ] = False,
    tier: Annotated[Tier, typer.Option(help="Model tier for --no-search.")] = "main",
    config: ConfigOption = None,
) -> None:
    """Answer a question from the web, with numbered citations.

    Searches, reads the most relevant pages, and writes a short answer in which
    each claim cites its source.
    """
    settings = _load(config)
    try:
        if no_search:
            _ask_plain(settings, question, tier)
            return
        result = asyncio.run(_run_quick(settings, question, provider))
    except LLMError as error:
        err_console.print(f"[red]Model call failed:[/red] {escape(str(error))}")
        raise typer.Exit(code=2) from error
    except ValueError as error:  # unknown provider name
        err_console.print(f"[red]{escape(str(error))}[/red]")
        raise typer.Exit(code=2) from error

    _print_quick_result(result)
    if result.status == "no_sources":
        raise typer.Exit(code=1)


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


@app.command()
def fetch(
    url: Annotated[str, typer.Argument(help="The page or PDF to read.")],
    max_chars: Annotated[
        int | None, typer.Option("--max-chars", help="Print at most this much of the text.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the document as JSON.")] = False,
    config: ConfigOption = None,
) -> None:
    """Fetch a page and print its main content as Markdown."""
    settings = _load(config)

    async def run() -> Document:
        cache_dir = settings.paths.data_dir / "cache" / "pages"
        renderer = make_renderer(settings.fetch)
        try:
            async with Fetcher(settings.fetch, cache_dir) as fetcher:
                return await read_url(fetcher, url, renderer)
        finally:
            if renderer is not None:
                await renderer.aclose()

    try:
        document = asyncio.run(run())
    except (FetchError, ExtractError) as error:
        err_console.print(f"[red]Could not read page:[/red] {escape(str(error))}")
        raise typer.Exit(code=1) from error

    text = document.text if max_chars is None else document.text[:max_chars]
    if as_json:
        fields = {
            "url": document.url,
            "title": document.title,
            "author": document.author,
            "published": document.published.isoformat() if document.published else None,
            "site": document.site,
            "content_type": document.content_type,
            "word_count": document.word_count,
            "text": text,
        }
        typer.echo(json.dumps(fields, indent=2, ensure_ascii=False))
        return
    details = [document.site, document.author, str(document.published or "")]
    details.append(f"{document.word_count} words")
    err_console.print(f"[bold]{escape(document.title)}[/bold]")
    err_console.print(document.url, style="blue", highlight=False, soft_wrap=True)
    err_console.print(f"[dim]{escape(' · '.join(d for d in details if d))}[/dim]\n")
    # Only the document text goes to standard output, so it can be piped to a file.
    typer.echo(text)
