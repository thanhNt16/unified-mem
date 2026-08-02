from importlib.resources import files


def test_built_graph_ui_assets_are_packaged() -> None:
    root = files("kg.viz").joinpath("assets")
    assert root.joinpath("index.html").is_file()
    assert root.joinpath("graph-snapshot.json").is_file()
    assert root.joinpath("capabilities.json").is_file()
    html = root.joinpath("index.html").read_text(encoding="utf-8")
    assert "https://" not in html
