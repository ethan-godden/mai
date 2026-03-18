"""Build the retrieval evaluation corpus and compare ranking methods on it.

    uv run python scripts/eval_retrieval.py build
    uv run python scripts/eval_retrieval.py run bm25 [METHOD ...] [--label NAME]
    uv run python scripts/eval_retrieval.py list

``build`` runs Mai's own query rewriting, search and page reading for every
question in evals/retrieval/questions.json and saves the resulting passages
to data/evals/retrieval/corpus.json. The corpus holds text from third-party
web pages, so it stays in the git-ignored data directory.

``run`` ranks each question's passages with every named method, has the local
model grade any top-10 passage not graded before, and prints the metrics.
Grades are kept in evals/retrieval/judgments.jsonl (identifiers and grades
only) and results in evals/retrieval/results/.

Needs the model server and, for ``build``, the search service.
See docs/guide/evaluation.md.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import time
from datetime import datetime
from pathlib import Path

from mai.config import load_settings
from mai.eval.methods import METHODS
from mai.eval.retrieval import (
    Judgments,
    judge_pool,
    load_corpus,
    mean_scores,
    render_results,
    score_ranking,
)
from mai.extract import Document
from mai.fetch import Fetcher
from mai.llm import OpenAICompatClient
from mai.passages import chunk_document
from mai.quick import read_pages, search_queries
from mai.render import make_renderer
from mai.rewrite import rewrite_question
from mai.search import build_providers, http_client

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "evals" / "retrieval"
CORPUS = ROOT / "data" / "evals" / "retrieval" / "corpus.json"
POOL_DEPTH = 10


async def build() -> None:
    settings = load_settings()
    questions = json.loads((EVAL_DIR / "questions.json").read_text())
    renderer = make_renderer(settings.fetch)
    corpus = []
    try:
        async with (
            OpenAICompatClient(settings.llm) as llm,
            http_client(settings.search) as client,
            Fetcher(settings.fetch, settings.paths.data_dir / "cache" / "pages") as fetcher,
        ):
            providers = build_providers(client, settings.search)
            for entry in questions:
                queries = await rewrite_question(llm, entry["question"])
                results, _ = await search_queries(providers, queries, settings.quick)
                pages = await read_pages(
                    fetcher, renderer, [r.url for r in results], settings.quick.read_budget_s
                )
                sources, passages = [], []
                for result, page in zip(results, pages, strict=True):
                    if not isinstance(page, Document):
                        continue
                    passages += chunk_document(
                        page, len(sources), max_words=settings.quick.passage_words
                    )
                    sources.append({"url": page.url, "title": result.title})
                corpus.append(
                    {
                        **entry,
                        "queries": queries,
                        "sources": sources,
                        "passages": [dataclasses.asdict(p) for p in passages],
                    }
                )
                print(f"{entry['id']}: {len(sources)} pages, {len(passages)} passages", flush=True)
    finally:
        if renderer is not None:
            await renderer.aclose()
    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    CORPUS.write_text(json.dumps(corpus, indent=1, ensure_ascii=False))
    print(f"Saved {CORPUS.relative_to(ROOT)}")


async def run(names: list[str], label: str | None) -> None:
    unknown = [name for name in names if name not in METHODS]
    if unknown:
        raise SystemExit(f"Unknown method(s): {unknown}. Available: {sorted(METHODS)}")
    questions = load_corpus(CORPUS)
    judgments = Judgments(EVAL_DIR / "judgments.jsonl")

    rankings: dict[str, dict[str, list[int]]] = {}
    timing: dict[str, float] = {}
    for name in names:
        method = METHODS[name]()
        method(questions[0].question, questions[0].queries, questions[0].passages)  # warm up
        started = time.perf_counter()
        rankings[name] = {q.id: method(q.question, q.queries, q.passages) for q in questions}
        timing[name] = (time.perf_counter() - started) * 1000 / len(questions)
        print(f"ranked with {name}: {timing[name]:.0f} ms per question", flush=True)

    async with OpenAICompatClient(load_settings().llm) as llm:
        for question in questions:
            pool = [i for name in names for i in rankings[name][question.id][:POOL_DEPTH]]
            graded = await judge_pool(llm, question, pool, judgments)
            if graded:
                print(f"{question.id}: judged {graded} new passages", flush=True)
            # A second pass picks up anything the judge failed to grade the first time.
            await judge_pool(llm, question, pool, judgments, seed=1)

    results = {}
    for name in names:
        per_question = {q.id: score_ranking(q, rankings[name][q.id], judgments) for q in questions}
        results[name] = {
            "mean": mean_scores(list(per_question.values())),
            "ms_per_question": round(timing[name], 1),
            "per_question": per_question,
        }

    out = EVAL_DIR / "results" / f"{datetime.now():%Y-%m-%d}-{label or '-'.join(names)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "questions": len(questions),
        "passages": sum(len(q.passages) for q in questions),
        "judgments": len(judgments),
        "judge_model": load_settings().llm.tier("fast").model,
        "results": results,
    }
    out.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"\n{len(questions)} questions, {summary['passages']} passages, "
        f"{len(judgments)} judgments. Saved {out.relative_to(ROOT)}\n"
    )
    print(render_results(results))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="Build the corpus from live search results.")
    commands.add_parser("list", help="List available ranking methods.")
    runner = commands.add_parser("run", help="Rank, judge and score.")
    runner.add_argument("methods", nargs="+")
    runner.add_argument("--label", help="Name for the results file.")
    args = parser.parse_args()
    if args.command == "build":
        asyncio.run(build())
    elif args.command == "list":
        print("\n".join(sorted(METHODS)))
    else:
        asyncio.run(run(args.methods, args.label))


if __name__ == "__main__":
    main()
