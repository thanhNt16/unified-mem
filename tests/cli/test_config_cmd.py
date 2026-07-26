import pytest
from kg.cli.init import init_project
from kg.cli.config_cmd import config_get


def test_config_get(tmp_path):
    init_project(tmp_path, user_id="quan", scope="s")
    assert config_get(tmp_path / ".kg", "project.user_id") == "quan"
    assert config_get(tmp_path / ".kg", "chunking.tokens") == 512


def test_config_get_missing_key(tmp_path):
    init_project(tmp_path, user_id="u", scope="s")
    with pytest.raises(KeyError):
        config_get(tmp_path / ".kg", "nope.nope")
