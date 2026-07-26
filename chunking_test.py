from chunking import chunk_text


def test_empty_text_returns_empty_list():
    assert chunk_text("") == []


def test_short_text_returns_single_chunk():
    text = "A short filing excerpt."
    assert chunk_text(text, size=800, overlap=150) == [text]


def test_long_text_splits_into_multiple_chunks():
    text = "x" * 2000
    chunks = chunk_text(text, size=800, overlap=150)
    assert len(chunks) > 1


def test_consecutive_chunks_overlap():
    text = "".join(str(i % 10) for i in range(2000))
    chunks = chunk_text(text, size=800, overlap=150)
    tail_of_first = chunks[0][-150:]
    assert tail_of_first in chunks[1]


def test_chunk_size_is_respected():
    text = "y" * 5000
    chunks = chunk_text(text, size=800, overlap=150)
    assert all(len(c) <= 800 for c in chunks)


def test_step_math_with_small_numbers():
    text = "abcdefghijklmnopqrst"
    chunks = chunk_text(text, size=10, overlap=3)
    assert chunks[0] == "abcdefghij"
    assert chunks[1] == "hijklmnopq"


def test_whitespace_only_chunks_are_dropped():
    text = "start" + " " * 100 + "end"
    chunks = chunk_text(text, size=20, overlap=5)
    assert all(c.strip() for c in chunks)


def test_no_content_is_lost():
    text = "".join(str(i % 10) for i in range(1000))
    chunks = chunk_text(text, size=300, overlap=50)
    rejoined = chunks[0]
    for c in chunks[1:]:
        rejoined += c[50:]
    assert rejoined == text