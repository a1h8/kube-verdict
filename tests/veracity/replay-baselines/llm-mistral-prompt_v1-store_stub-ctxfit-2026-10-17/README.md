# mistral, prompt v1, no retrieval, context window sized to the prompt — 2026-10-17

The first LLM measurement where the model read every prompt in full. Records: `llm/<case>/run_<n>.json`.

- **Code:** commit `9bb83ba` (head of #129): context window sized per call (F8 fixed), not-ready
  pods as seeds, incident shortcut off, cause-concept grading with amendments A1–A3.
- **Model:** `mistral:latest` (`6577803aa9a0`, 7.2B Q4_K_M), Ollama 0.40.4, CPU only (`k3d-dev`),
  reached through `kubectl proxy`. Store: stub (no retrieval).
- **Complete for the 19 native cases**, 3 runs each: 57 non-empty answers; every
  call read its whole prompt (`llm_calls`: 79–94 % of the estimate, which over-estimates by ~12 %
  by design); `num_ctx` 3072–7168. h026 was measured on 2026-10-18 (03:07–03:21Z), same code,
  model and settings, after the first run was stopped; a reconcileflow test suite ran on the same
  machine at that time (latency of those three runs not comparable).
- **Harness defect seen (R1):** in the first pass, the child processes of h024 runs 2–3 and of
  h026 ended without a record or a message — `tools/veracity_replay.py` `_run_in_child` ignores
  the child's exit code. The second pass (`--resume`) re-ran h024 runs 2–3, which are recorded.
  The cause of the silent stops is not established (a memory-pressure kill is plausible, not
  proven).
- **Known limits of the code measured, all found after this run:** the false missing
  ServiceAccount hint was still in the prompt (S1); the old rule engine still injected its
  hypothesis (`HYPOTHESIS TO INVESTIGATE`); the memory/image/probe drifts of h002, h003, h009,
  h024 come from the test loader (F11).

**Result:** 31/57 runs; 11/19 cases by majority of runs (≥ 2/3). Confidence label: MEDIUM 52,
HIGH 4, empty 1. On the two healthy cases (h022, h026) the model names a cause 6/6 times. Median wall time per run 242 s (max 693 s).
Comparison with the structured diagnosis: `docs/veracity-replay.md` § *First full-prompt LLM run*.
