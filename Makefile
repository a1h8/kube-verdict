.DEFAULT_GOAL := help
.PHONY: help demo install test ui lint

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

demo: ## One-command offline demo — a real render-vs-live RCA (no cluster, no Ollama)
	@python -c "import yaml" 2>/dev/null || pip install -q pyyaml
	@python demo/real_run.py

install: ## Install KubeVerdict (editable, full deps)
	pip install -e .

test: ## Run the offline test suite
	pytest -q

ui: ## Launch the Streamlit UI (offline pipeline trace — pick any h0NN case)
	streamlit run ui/app.py

lint: ## Ruff lint
	ruff check .
