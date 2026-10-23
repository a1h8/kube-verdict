.DEFAULT_GOAL := help
.PHONY: help demo install test ui lint ci

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

demo: ## One-command offline demo — a real render-vs-live RCA (no cluster, no Ollama)
	@python -c "import yaml" 2>/dev/null || pip install -q pyyaml
	@python demo/real_run.py

install: ## Install KubeVerdict (editable, full deps)
	pip install -e .

test: ## Run the offline test suite (the CI's selection: no Ollama, no live cluster)
	HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 pytest -q -m "not integration"

ui: ## Launch the Streamlit UI (offline pipeline trace — pick any h0NN case)
	streamlit run ui/app.py

lint: ## Ruff lint (the CI's rules, from pyproject.toml)
	ruff check .

ci: lint test ## What the CI checks: the lint, then the offline tests
