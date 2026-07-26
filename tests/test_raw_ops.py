import hashlib
from kg.cli.init import init_project
from kg.config import Config
from kg.raw_ops import add_source, list_sources
from kg.frontmatter import parse
from kg.registry import Registry


def _setup(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="s")
    cfg = Config.default(user_id="u", scope="s")
    return paths, cfg


def test_add_text_source_creates_raw_and_registry(tmp_path):
    paths, cfg = _setup(tmp_path)
    added, rel = add_source(paths, cfg, "hello world", type="text", title="Hi")
    assert added is True
    raw_file = paths.root / rel
    assert raw_file.is_file()
    fm, body = parse(raw_file.read_text(encoding="utf-8"))
    assert body.strip() == "hello world"
    assert fm.type == "text"
    assert len(fm.chunks) >= 1
    assert len(list_sources(paths, unextracted_only=False)) == 1


def test_re_add_same_content_is_noop(tmp_path):
    paths, cfg = _setup(tmp_path)
    add_source(paths, cfg, "hello world", type="text", title="Hi")
    added2, _ = add_source(paths, cfg, "hello world", type="text", title="Hi")
    assert added2 is False
    assert len(list_sources(paths, unextracted_only=False)) == 1


def test_unextracted_filter(tmp_path):
    paths, cfg = _setup(tmp_path)
    add_source(paths, cfg, "aaa", type="text", title=None)
    Registry(paths.registry).mark_extracted(
        hashlib.sha256(b"aaa").hexdigest(), [], {})
    assert len(list_sources(paths, unextracted_only=True)) == 0


def test_conversation_goes_to_subdir(tmp_path):
    paths, cfg = _setup(tmp_path)
    _, rel = add_source(paths, cfg, "session body", type="conversation",
                        title="sess-1", conversation=True)
    assert rel.startswith("raw/conversations/")
