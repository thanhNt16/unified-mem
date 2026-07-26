import pytest


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    """A throwaway cwd; tests cd into it via monkeypatch."""
    monkeypatch.chdir(tmp_path)
    return tmp_path
