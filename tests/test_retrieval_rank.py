import numpy as np

from mai.passages import Passage
from mai.retrieval.embed import CachedEmbedder
from mai.retrieval.rank import fuse, passage_text, rank_dense
from tests.fakes import FakeEmbedder


def passages() -> list[Passage]:
    return [
        Passage(0, 0, "The recipe needs two eggs and flour."),
        Passage(0, 1, "Automobiles lose value quickly after purchase.", heading="Depreciation"),
        Passage(1, 0, "A car should be serviced once a year."),
    ]


def test_dense_ranking_finds_meaning_without_shared_words():
    embedder = FakeEmbedder(synonyms={"automobiles": "car", "cars": "car"})
    ranked, vectors = rank_dense(embedder, passages(), "cars")

    assert [index for index, _ in ranked][:2] == [2, 1] or [index for index, _ in ranked][:2] == [
        1,
        2,
    ]
    assert ranked[-1][0] == 0 and ranked[-1][1] == 0.0  # the recipe shares nothing
    assert vectors.shape == (3, 64)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert rank_dense(embedder, [], "q")[0] == []


def test_headings_are_part_of_what_is_embedded():
    embedder = FakeEmbedder()
    rank_dense(embedder, passages(), "q")
    assert embedder.passage_calls[0][1].startswith("Depreciation\nAutomobiles")
    assert passage_text(passages()[0]) == "The recipe needs two eggs and flour."


def test_fuse_rewards_agreement_between_rankings():
    assert fuse([[1, 2, 3], [3, 1, 4]]) == [1, 3, 2, 4]
    assert fuse([[5, 6]]) == [5, 6]
    assert fuse([]) == []


def test_cache_embeds_each_passage_once_and_survives_reopening(tmp_path):
    inner = FakeEmbedder()
    path = tmp_path / "cache" / "embeddings.sqlite"
    cached = CachedEmbedder(inner, path)

    first = cached.embed_passages(["alpha beta", "gamma"])
    second = cached.embed_passages(["gamma", "delta", "alpha beta"])

    assert inner.passage_calls == [["alpha beta", "gamma"], ["delta"]]  # only the new text
    assert np.array_equal(second[0], first[1]) and np.array_equal(second[2], first[0])
    assert cached.embed_passages([]).shape == (0, 0)
    cached.close()

    reopened_inner = FakeEmbedder()
    reopened = CachedEmbedder(reopened_inner, path)
    assert np.array_equal(reopened.embed_passages(["alpha beta"])[0], first[0])
    assert reopened_inner.passage_calls == []  # served from disk
    assert reopened.name == "fake-embedder"


def test_cache_is_per_model(tmp_path):
    path = tmp_path / "embeddings.sqlite"
    one = FakeEmbedder()
    CachedEmbedder(one, path).embed_passages(["text"])
    other = FakeEmbedder()
    other.name = "another-model"
    CachedEmbedder(other, path).embed_passages(["text"])
    assert other.passage_calls == [["text"]]  # a different model never reuses vectors
