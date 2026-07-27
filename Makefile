PKG := kg
H ?= claude

.PHONY: dev build test install uninstall clean bench

dev:           ## editable install for working on kg itself
	uv sync
	uv pip install -e .

build:         ## build the wheel into dist/
	uv build

test:          ## run the test suite (deterministic; live-session tier lives in .github/workflows/test.yml)
	uv run pytest

install:       ## full bootstrap: build + global tool install + kg install + kg init
	uv sync && uv build && uv tool install ./dist/$(PKG)-*.whl --force && \
		kg install $(H) --apply --skills-src $(CURDIR)/skills && kg init

uninstall:     ## reverse of install
	-kg install --uninstall $(H)
	-uv tool uninstall $(PKG)

clean:
	rm -rf dist build *.egg-info

bench:          ## run deterministic benchmark (CI-safe 10-doc tier)
	uv run kg bench --scale 10
