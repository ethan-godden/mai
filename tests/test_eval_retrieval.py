import json
import math

import pytest

from mai.eval.retrieval import (
    EvalQuestion,
    Judgments,
    _Grade,
    _Grades,
    judge,
    judge_pool,
    load_corpus,
    mean_scores,
    ndcg_at_k,
    passage_id,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    render_results,
    score_ranking,
)
from mai.passages import Passage
from tests.fakes import FakeLLM


def question(n: int = 12) -> EvalQuestion:
    passages = [Passage(source=i % 3, position=i, text=f"passage {i}") for i in range(n)]
    urls = ["https://a", "https://b", "https://c"]
    return EvalQuestion(
        id="q1",
        question="Why?",
        queries=["why"],
        passages=passages,
        ids=[passage_id(urls[p.source], p.text) for p in passages],
        urls=urls,
    )


def test_metrics_on_a_known_ranking():
    ranked = [3, 0, 2, 0, 1]
    pool = [3, 3, 2, 2, 1, 0, 0]
    assert precision_at_k(ranked, 5) == 2 / 5  # grades 3 and 2 are relevant; 1 is not
    assert recall_at_k(ranked, pool, 5) == 2 / 4
    assert reciprocal_rank(ranked) == 1.0
    assert reciprocal_rank([0, 1, 2]) == 1 / 3
    assert reciprocal_rank([0, 1]) == 0.0

    actual = 7 / 1 + 3 / math.log2(4) + 1 / math.log2(6)
    ideal = 7 / 1 + 7 / math.log2(3) + 3 / math.log2(4) + 3 / math.log2(5) + 1 / math.log2(6)
    assert ndcg_at_k(ranked, pool, 5) == pytest.approx(actual / ideal)
    assert ndcg_at_k([3, 3, 2, 2, 1], pool, 5) == pytest.approx(1.0)  # the ideal ordering
    assert ndcg_at_k([0, 0], [0, 0], 5) == 0.0 and recall_at_k([0], [0, 1], 5) == 0.0


def test_judgments_round_trip_and_stay_sorted(tmp_path):
    path = tmp_path / "j" / "judgments.jsonl"
    judgments = Judgments(path)
    judgments.add("q2", {"bbb": 3})
    judgments.add("q1", {"zzz": 0, "aaa": 2})

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0] == {"question": "q1", "passage": "aaa", "grade": 2}  # stable order for diffs
    reloaded = Judgments(path)
    assert len(reloaded) == 3 and reloaded.get("q1", "zzz") == 0 and reloaded.get("q1", "x") is None
    assert reloaded.for_question("q1") == {"aaa": 2, "zzz": 0}


def test_passage_id_is_stable_and_distinguishes_sources():
    assert passage_id("https://a", "text") == passage_id("https://a", "text")
    assert passage_id("https://a", "text") != passage_id("https://b", "text")
    assert len(passage_id("https://a", "text")) == 16


async def test_judge_grades_in_batches_and_marks_failures():
    seen: list[str] = []

    class Judge(FakeLLM):
        async def structured(self, tier, messages, schema, **kwargs):
            prompt = messages[-1]["content"]
            seen.append(prompt)
            if "text 7" in prompt:  # the second batch fails outright
                self._structured = {}
            return await super().structured(tier, messages, schema, **kwargs)

    grades = _Grades(
        grades=[_Grade(passage=1, grade=3), _Grade(passage=2, grade=0), _Grade(passage=9, grade=1)]
    )
    llm = Judge({_Grades: grades})
    result = await judge(llm, "Why?", [f"text {i}" for i in range(1, 8)])

    assert result == [3, 0, None, None, None, None, None]  # ungraded stay None, never 0
    assert (
        len(seen) == 2 and "Passage 5:\ntext 5" in seen[0] and "Research question: Why?" in seen[0]
    )
    assert llm.calls[0][1] == "fast"


async def test_judge_pool_only_grades_what_is_missing(tmp_path):
    q = question()
    judgments = Judgments(tmp_path / "j.jsonl")
    judgments.add("q1", {q.ids[0]: 1})
    llm = FakeLLM({_Grades: _Grades(grades=[_Grade(passage=n, grade=2) for n in range(1, 6)])})

    graded = await judge_pool(llm, q, [0, 1, 2, 1], judgments)

    assert graded == 2 and len(llm.calls) == 1
    assert judgments.get("q1", q.ids[0]) == 1  # existing judgment untouched
    assert judgments.get("q1", q.ids[1]) == 2 and judgments.get("q1", q.ids[2]) == 2
    assert await judge_pool(llm, q, [0, 1, 2], judgments) == 0 and len(llm.calls) == 1


def test_score_ranking_requires_full_judgments(tmp_path):
    q = question()
    judgments = Judgments(tmp_path / "j.jsonl")
    judgments.add("q1", {q.ids[i]: (3 if i < 2 else 0) for i in range(10)})

    scores = score_ranking(q, list(range(12)), judgments)
    assert scores["precision@5"] == 2 / 5 and scores["recall@10"] == 1.0 and scores["mrr"] == 1.0
    assert scores["ndcg@10"] == pytest.approx(1.0)
    assert scores["sources@10"] == 3.0

    with pytest.raises(KeyError, match="No judgment"):
        score_ranking(q, [11, *range(10)], judgments)


def test_load_corpus_and_render(tmp_path):
    entry = {
        "id": "q1", "question": "Why?", "queries": ["why"],
        "sources": [{"url": "https://a", "title": "A"}],
        "passages": [{"source": 0, "position": 0, "text": "t", "heading": "H", "score": 0.0}],
    }  # fmt: skip
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([entry]))
    (loaded,) = load_corpus(path)
    assert loaded.passages[0].heading == "H" and loaded.ids == [passage_id("https://a", "t")]

    mean = mean_scores(
        [
            {
                m: 1.0
                for m in (
                    "ndcg@10",
                    "precision@5",
                    "precision@10",
                    "recall@10",
                    "mrr",
                    "sources@10",
                )
            }
        ]
        * 2
    )
    table = render_results({"bm25": {"mean": mean, "ms_per_question": 3.2}})
    assert table.splitlines()[2] == "| bm25 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.0 | 3 |"
