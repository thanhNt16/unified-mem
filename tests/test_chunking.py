from kg.chunking import slugify, chunk_markdown


def test_slugify_basic():
    assert slugify("Keep Your Knowledge Graph Clean!") == "keep-your-knowledge-graph-clean"
    assert slugify("  Paris, France  ") == "paris-france"
    assert slugify("a/b:c") == "a-b-c"


def test_slugify_cap():
    assert len(slugify("x" * 200)) == 80


def test_chunk_markdown_short_text_is_one_chunk():
    chunks = chunk_markdown("hello world", tokens=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0].text == "hello world"
    assert chunks[0].index == 0
    assert chunks[0].token_count > 0
    assert chunks[0].start_char == 0


def test_chunk_markdown_respects_heading_boundaries():
    body = "# A\n\n" + ("alpha. " * 400) + "\n\n# B\n\n" + ("bravo. " * 400)
    chunks = chunk_markdown(body, tokens=64, overlap=8)
    assert len(chunks) > 1
    starts = {body[c.start_char:c.start_char + 2] for c in chunks}
    assert "# " in starts


def test_chunks_cover_full_text():
    body = "word. " * 1000
    chunks = chunk_markdown(body, tokens=32, overlap=8)
    assert chunks[-1].end_char == len(body)
    assert all(c.end_char > c.start_char for c in chunks)
