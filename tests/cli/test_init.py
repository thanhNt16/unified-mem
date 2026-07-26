from kg.cli.init import init_project


def test_init_creates_full_layout(tmp_path):
    p = init_project(tmp_path, user_id="quan", scope="my-agent")
    assert (tmp_path / ".kg").is_dir()
    assert p.config.is_file()
    assert p.ontology.is_file()
    assert p.registry.is_file()
    assert p.wiki_index.is_file()
    assert p.raw.is_dir()
    assert "quan" in p.config.read_text()


def test_init_is_idempotent(tmp_path):
    init_project(tmp_path, user_id="a", scope="s")
    cfg_before = (tmp_path / ".kg" / "config.toml").read_text()
    init_project(tmp_path, user_id="a", scope="s")  # must not crash
    cfg_after = (tmp_path / ".kg" / "config.toml").read_text()
    assert cfg_before == cfg_after
