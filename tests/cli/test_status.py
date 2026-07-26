from kg.cli.init import init_project
from kg.cli.status import status_report
from kg.raw_ops import add_source
from kg.config import Config


def test_status_counts(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="s")
    cfg = Config.default(user_id="u", scope="s")
    add_source(paths, cfg, "one", type="text", title=None)
    add_source(paths, cfg, "two", type="text", title=None)
    rep = status_report(paths)
    assert rep["sources"] == 2
    assert rep["unextracted"] == 2
    assert rep["extracted"] == 0
