PY := .venv/bin/python

.PHONY: help test lint cov hooks
help:        ## list targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'
test:        ## run the suite (all fakes offline; a token-refresh warning = live API hit)
	$(PY) -m pytest -q
lint:        ## ruff (config in pyproject)
	$(PY) -m ruff check .
cov:         ## suite + coverage report (CI gates at 85%)
	$(PY) -m pytest -q --cov=mcp_microsoft_ads --cov-report=term-missing
hooks:       ## install pre-commit (gitleaks+ruff) and pre-push (pytest) into .git/hooks
	./scripts/install-hooks.sh
