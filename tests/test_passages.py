from mai.extract import Document
from mai.passages import Passage, chunk_document, select_passages, tokenize


def doc(text: str) -> Document:
    return Document(url="https://example.com", title="T", text=text, content_type="text/html")


def test_tokenize_lowercases_and_drops_stopwords():
    assert tokenize("What is the COP of a Heat-Pump at -15°C?") == [
        "cop",
        "heat",
        "pump",
        "15",
        "c",
    ]


def test_chunks_follow_headings_and_respect_the_word_limit():
    text = "# Title\n\nIntro one.\n\nIntro two.\n\n## Costs\n\n" + "\n\n".join(
        f"Paragraph {n} " + "word " * 40 for n in range(5)
    )
    passages = chunk_document(doc(text), source=3, max_words=100)

    assert passages[0] == Passage(3, 0, "Intro one.\n\nIntro two.", "Title")
    assert [p.heading for p in passages[1:]] == ["Costs"] * 3
    assert [p.position for p in passages] == [0, 1, 2, 3]
    assert all(len(p.text.split()) <= 100 for p in passages)
    assert all(p.source == 3 for p in passages)
    assert "Paragraph 0" in passages[1].text and "Paragraph 1" in passages[1].text  # grouped
    assert "Paragraph 4" in passages[3].text  # nothing dropped


def test_an_oversized_block_is_split_at_sentence_ends():
    block = " ".join(
        f"Sentence {n} has exactly six words." for n in range(30)
    )  # one PDF-style block
    passages = chunk_document(doc(block), source=0, max_words=50)

    assert len(passages) > 1
    assert all(len(p.text.split()) <= 50 for p in passages)
    assert all(p.text.endswith(".") for p in passages)  # cut between sentences
    assert " ".join(p.text for p in passages) == block  # no text lost or duplicated


def test_text_with_no_sentence_breaks_is_still_bounded():
    passages = chunk_document(doc("word " * 500), source=0, max_words=120)
    assert [len(p.text.split()) for p in passages] == [120, 120, 120, 120, 20]


def test_empty_document_has_no_passages():
    assert chunk_document(doc("\n\n# Only a heading\n\n"), source=0) == []


def corpus() -> list[Passage]:
    return [
        Passage(0, 0, "Heat pumps keep working in cold climates down to minus 25 degrees."),
        Passage(0, 1, "Cold climate heat pumps cost more to install than gas boilers."),
        Passage(0, 2, "A heat pump moves heat rather than generating it, in any climate."),
        Passage(0, 3, "Heat pump efficiency in cold weather is measured as COP."),
        Passage(1, 0, "Gas boilers burn fuel and are cheap to install."),
        Passage(1, 1, "The recipe needs two eggs and a cup of flour."),
        Passage(2, 0, "Installing a heat pump in a cold climate can cut heating bills."),
    ]


def test_selection_ranks_relevant_passages_and_caps_each_source():
    chosen = select_passages(corpus(), "heat pump cold climate gas", max_passages=10, per_source=2)
    assert sum(p.source == 0 for p in chosen) == 2  # capped although four match
    assert {p.source for p in chosen} == {0, 1, 2}
    assert not any("eggs" in p.text for p in chosen)  # no overlap, never returned
    assert chosen[0].score >= chosen[-1].score
    assert all(p.score != 0.0 for p in chosen)


def test_selection_limits_and_edge_cases():
    assert len(select_passages(corpus(), "heat pump", max_passages=3, per_source=3)) == 3
    assert select_passages(corpus(), "zebra migration") == []
    assert select_passages([], "anything") == []
    assert select_passages(corpus(), "the of and") == []  # nothing but stopwords
