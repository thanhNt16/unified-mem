from kg.cli.init import init_project
from kg.index import IndexMode, plan_index


def test_fast_mode_disables_embeddings_and_document_extraction(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="p")
    (tmp_path / "a.py").write_text("def f(): pass\n")
    plan = plan_index(paths, tmp_path, mode="fast", extractor_version="v1")
    assert plan.mode is IndexMode.FAST
    assert plan.action == "full_rebuild"
    assert plan.run_structural is True
    assert plan.run_embeddings is False
    assert plan.run_document_extraction is False
    assert plan.run_similarity is False


def test_moderate_and_full_mode_work_flags(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="p")
    (tmp_path / "a.md").write_text("# note\n")
    moderate = plan_index(paths, tmp_path, mode="moderate", extractor_version="v1")
    full = plan_index(paths, tmp_path, mode="full", extractor_version="v1")
    assert moderate.run_embeddings is True
    assert moderate.run_document_extraction is True
    assert moderate.run_similarity is False
    assert full.run_similarity is True
    assert full.run_community is True


def test_invalid_mode_and_threshold_rejected(tmp_path):
    paths = init_project(tmp_path, user_id="u", scope="p")
    try:
        plan_index(paths, tmp_path, mode="wrong", extractor_version="v1")
    except ValueError as exc:
        assert "invalid index mode" in str(exc)
    else:
        raise AssertionError("expected invalid mode error")

    try:
        plan_index(paths, tmp_path, mode="fast", extractor_version="v1", incremental_threshold=2)
    except ValueError as exc:
        assert "incremental_threshold" in str(exc)
    else:
        raise AssertionError("expected threshold error")
