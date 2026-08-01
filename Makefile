PKG := kg
H ?= claude

.PHONY: dev build test install uninstall clean bench evaluate-cbm

dev:           ## editable install for working on kg itself
	uv sync
	uv pip install -e .

build:         ## build the wheel into dist/
	uv build

test:          ## run the test suite (deterministic; live-session tier lives in .github/workflows/test.yml)
	uv run pytest

install:       ## full bootstrap: build + global tool install + kg install + kg init
	uv sync && uv build && uv tool install ./dist/$(PKG)-*.whl --force && \
		kg install $(H) --apply --skills-src $(CURDIR)/src/kg/skills && kg init

uninstall:     ## reverse of install
	-kg install --uninstall $(H)
	-uv tool uninstall $(PKG)

clean:
	rm -rf dist build *.egg-info

bench:          ## run deterministic benchmark (CI-safe 10-doc tier); requires repo checkout (bench harness lives in ./bench)
	PYTHONPATH=$(CURDIR) uv run kg bench --scale 10

evaluate-cbm:   ## check CBM regression metric floors
	PYTHONPATH=$(CURDIR) uv run pytest tests/ -x
	PYTHONPATH=$(CURDIR) uv run kg bench --scale 10 --dimension all
	PYTHONPATH=$(CURDIR) uv run pytest tests/test_scale_10k.py -v -s
	PYTHONPATH=$(CURDIR) uv run python bench/scale_100k.py
	PYTHONPATH=$(CURDIR) uv run pytest tests/test_evaluate_cbm.py -v
