.PHONY: test lint docs build tutorials showcase

test:
	python -m pytest
lint:
	python -m ruff check .
	python -m ruff format --check .
docs:
	python -m mkdocs build --strict
build:
	python -m build
tutorials:
	python examples/run_tutorials.py
showcase:
	python examples/showcase.py
