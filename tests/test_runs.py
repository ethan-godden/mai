import json
from datetime import date, datetime

from mai.config import Settings
from mai.llm import CallRecord
from mai.passages import Passage
from mai.quick import NO_SOURCES_MESSAGE, QuickResult, Source
from mai.runs import render_report, run_slug, save_run, settings_snapshot
from mai.search import SearchResult
from tests.fakes import completion

STARTED = datetime(2026, 11, 23, 14, 25, 1)


def result(**changes) -> QuickResult:
    sources = [
        Source(1, "https://a.example/x", "Tidal power", "The Ledger", date(2024, 5, 14),
               [Passage(0, 2, "It has 48 turbines.", "Size", 3.2)]),
        Source(2, "https://b.example/y", "A summary", None, None,
               [Passage(1, 0, "48 turbines, says a summary.")], snippet_only=True),
    ]  # fmt: skip
    base = QuickResult(
        question="How many turbines? (Veldrin/Port Hask)",
        status="answered",
        answer="It has 48 turbines [1][2].",
        queries=["veldrin turbines"],
        search_results=[SearchResult(url="https://a.example/x", title="Tidal", providers=("s",))],
        search_errors={"wikipedia": "HTTP 500"},
        sources=sources,
        cited=[1, 2],
        timings={"rewrite": 1.0, "answer": 9.5},
        completion=completion("It has 48 turbines [1][2].", "fast"),
    )
    for key, value in changes.items():
        setattr(base, key, value)
    return base


def test_run_slug():
    assert (
        run_slug("How many turbines? (Veldrin/Port Hask)") == "how-many-turbines-veldrin-port-hask"
    )
    assert run_slug("???") == "run"
    assert len(run_slug("word " * 40)) <= 50 and not run_slug("word " * 40).endswith("-")


def test_save_run_writes_three_readable_files(tmp_path):
    calls = [CallRecord([{"role": "user", "content": "prompt"}], completion("out", "fast"))]
    directory = save_run(
        result(), tmp_path / "runs", settings=Settings(), started=STARTED, calls=calls
    )

    assert directory.name == "20261123-142501-how-many-turbines-veldrin-port-hask"
    assert sorted(p.name for p in directory.iterdir()) == ["report.md", "run.json", "trace.jsonl"]

    record = json.loads((directory / "run.json").read_text())
    assert record["format"] == 1 and record["mode"] == "quick"
    assert record["started"] == "2026-11-23T14:25:01"
    assert record["question"].startswith("How many turbines?")
    assert record["queries"] == ["veldrin turbines"]
    assert record["search_results"][0]["url"] == "https://a.example/x"
    assert record["search_errors"] == {"wikipedia": "HTTP 500"}
    source = record["sources"][0]
    assert source["published"] == "2024-05-14"
    assert source["passages"][0] == {
        "source": 0, "position": 2, "text": "It has 48 turbines.", "heading": "Size", "score": 3.2,
    }  # fmt: skip
    assert record["cited"] == [1, 2] and record["timings"]["answer"] == 9.5
    assert record["completion"]["model"] == "fake"
    assert record["settings"]["quick"]["max_passages"] == 10

    trace = [json.loads(line) for line in (directory / "trace.jsonl").read_text().splitlines()]
    assert trace == [
        {
            "messages": [{"role": "user", "content": "prompt"}],
            "text": "out", "tier": "fast", "model": "fake", "finish_reason": "stop",
            "prompt_tokens": 100, "completion_tokens": 50, "duration_s": 1.0, "ttft_s": 0.2,
        }
    ]  # fmt: skip


def test_secrets_never_reach_a_run_record(tmp_path):
    settings = Settings(
        llm={"api_key": "sk-secret"},  # type: ignore[arg-type]
        search={"semantic_scholar_api_key": "s2-secret"},  # type: ignore[arg-type]
    )
    snapshot = settings_snapshot(settings)
    assert "api_key" not in snapshot["llm"]
    assert "semantic_scholar_api_key" not in snapshot["search"]

    directory = save_run(result(), tmp_path, settings=settings, started=STARTED)
    saved = "".join(p.read_text() for p in directory.iterdir())
    assert "sk-secret" not in saved and "s2-secret" not in saved


def test_a_second_run_in_the_same_second_gets_its_own_directory(tmp_path):
    first = save_run(result(), tmp_path, settings=Settings(), started=STARTED)
    second = save_run(result(), tmp_path, settings=Settings(), started=STARTED)
    assert second.name == first.name + "-2"


def test_report_shows_answer_sources_and_caveats():
    report = render_report(result(invalid_citations=[7]), STARTED)

    assert report.startswith(
        "# How many turbines? (Veldrin/Port Hask)\n\nIt has 48 turbines [1][2]."
    )
    assert "1. [Tidal power](https://a.example/x) (The Ledger, 2024-05-14)" in report
    assert "search summary only, because the page could not be read: [2]" in report
    assert "The answer cites [7], which match no source." in report
    assert "quick mode, 2026-11-23 14:25, fake" in report
    assert "not yet checked against the sources automatically" in report


def test_report_for_an_unanswered_or_uncited_run():
    empty = result(
        status="no_sources", answer=NO_SOURCES_MESSAGE, sources=[], cited=[], completion=None
    )
    report = render_report(empty, STARTED)
    assert NO_SOURCES_MESSAGE in report and "## Sources" not in report and "no model" in report

    uncited = render_report(result(cited=[], answer="It has 48 turbines."), STARTED)
    assert "The answer contains no citations." in uncited
    assert "2. [A summary]" in uncited  # all sources listed when none is cited
