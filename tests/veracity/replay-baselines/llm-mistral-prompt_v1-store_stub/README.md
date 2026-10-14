# Baseline — mistral, prompt v1, no retrieval (store stub)

Frozen 2026-10-14. Produced by `tools/veracity_replay.py --solver llm --runs 3` from a
worktree at commit `d61b03e` + the replay fixes `d9bb9b2` and `279f451` (prompt
`PROMPT_VERSION = "v1"`, no section 7), Ollama 0.40.3 (`mistral:latest`, CPU), through
`kubectl port-forward` to `k3d-dev/kubeverdict/ollama`. 18 native cases × 3 runs.

**Not the production configuration**: the harness passes `StubStore`, so no retrieval
(see `docs/target/veracity.md` § *How a case reaches the model*). It is the
no-retrieval point of the retrieval comparison.

Incidents during the run: the Ollama pod was OOMKilled once (8 Gi limit) and one run
hung 21 h on a dead port-forward (fixed by the child-process watchdog). One record was
written empty while /tmp was full and was re-run. Records are complete (54/54).

Analysis: `docs/target/veracity.md` § *First LLM baseline*.
