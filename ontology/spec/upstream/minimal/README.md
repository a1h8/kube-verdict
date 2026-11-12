Minimal objects submitted to the API server with `kubectl create --dry-run=server` by `tools/observe_defaults.py`
(docs/schema-registry.md, step 6). They hold only what the server requires to accept them; what the server adds is the
observed default. They are inputs, not knowledge: no value here is a fact the product reads.
