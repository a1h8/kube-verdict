# Diagnostics — read-only checks as part of the investigation

> Status (2026-10-13): steps 0 and 1 done. Steps 2–4 have their own spec section
> before they are implemented.

## Why

Found by h022 (`docs/test-cases.md` → *Scenario catalog v2 — tier C cases* § h022): read-only
commands (`kubectl get / describe / logs`) were counted as remediation with no rollback,
so any LOW run could end `NO_GO`. Step 0 moved them to a separate `diagnostics` field
that the gate never reads. But a bare list of commands tells the operator neither why
to run one nor how to read its output. And the system could run them itself.

## Steps (decided 2026-10-12, in this order)

| Step | What | Status |
|---|---|---|
| 0 | `is_read_only()` splits read-only lines out of `remediation` into `diagnostics`; gate fails closed on an unknown namespace or no command | done |
| 1 | Structured diagnostics: each check carries its hypothesis, purpose, and what confirms / refutes it | implemented (2026-10-13) |
| 2 | The LLM proposes checks in their own prompt section, in the step-1 shape, instead of being told not to | to spec |
| 3 | Show diagnostics at the human gate and in the dashboard, grouped by hypothesis | to spec |
| 4 | Run read-only checks automatically and feed their output back as evidence | to spec, with a security spec |

Step 4 is last on purpose: "read-only" is not "safe". `kubectl get secret -o yaml`
changes nothing and leaks secrets. Auto-execution needs a read-only service account
enforced by RBAC (not by parsing the command), secrets excluded, output capped, and
fixture-backed results offline, like the Prometheus / Loki / OTel collectors.

## Step 1 — structured diagnostics

### Model

New module `rca/diagnostics.py`. `is_read_only()` and `split_diagnostics()` move there
from `rca/analyzer.py` (re-exported by the analyzer, so no caller breaks).

```python
@dataclass(frozen=True)
class Check:                 # what a rule declares
    command: str             # read-only, verified by is_read_only()
    purpose: str             # what it verifies, one sentence
    confirms_if: str         # the output that supports the hypothesis
    refutes_if: str = ""     # the output that rules it out, when there is one

@dataclass(frozen=True)
class Diagnostic:            # what the report carries
    command: str
    source: str              # "rule" | "llm"
    hypothesis: str = ""     # rule_id for a rule check, "" for an unstructured LLM line
    affected: str = ""       # kind/namespace/name the check is about
    purpose: str = ""
    confirms_if: str = ""
    refutes_if: str = ""
```

`RCAReport.diagnostics` becomes `list[Diagnostic]`. `to_dict()` exposes it as a list of
dicts with those keys. The field was added in step 0 and never released, so its type
can change.

### Rules

- `_Rule` gets `checks(entity, graph) -> list[Check]`. `commands()` returns actions
  only. Each of the 7 rules moves its read-only commands from `commands()` to
  `checks()`, with purpose / confirms_if / refutes_if written for that rule.
- `RemediationHypothesis` gets `checks: list[Check]`.
- `_apply_rule_fallback` turns each check into a `Diagnostic(source="rule",
  hypothesis=rule_id, affected=h.affected, …)`. `[rule:…]` header lines stay in
  `remediation` only (in front of the hypothesis's actions, as today). A diagnostic
  carries its hypothesis itself, so it needs no header.
- Order: hypothesis weight descending, then the rule's own order. Deduplicated on
  `(command, affected)`: the first one (highest weight) wins.

Intent per rule (exact wording is written with the code):

| Rule | Checks | Confirms if |
|---|---|---|
| `oom_kill` | `describe pod`, `top pod` | last state `OOMKilled` / exit 137; memory close to the limit |
| `crashloop_db` | `logs --previous`, `get endpoints`, `get services` | `connection refused` / timeout to the dependency; its endpoints are empty |
| `image_pull_backoff` | `describe pod` | `manifest unknown`, `unauthorized` or `not found` in the pull events |
| `missing_config` | `describe pod`, `get configmaps`, `get secrets` | the referenced name is absent from the list |
| `pending_unschedulable` | `describe pod`, `get nodes --show-labels`, `describe nodes` (taints) | no node has the required label, or a taint has no toleration |
| `helm_drift` | `get pod -o jsonpath`, `helm diff upgrade` | the drifted key differs between live and chart |
| `degraded_deployment` | `describe`, `get pods -l`, `logs -l` | replicas not ready, and the reason in pod events / logs |

### Safety nets

- A rule check that is not `is_read_only()` raises at rule load in tests: a unit test
  walks every rule against a fixture graph and asserts each `Check.command` is
  read-only, and each `commands()` line is not.
- LLM lines in *Remediation* that are read-only still move out (step 0), as
  `Diagnostic(source="llm")` with only `command` set, until step 2 gives them a shape.

### Not changed

`remediation`, `rollback`, blast radius, dry-run and the gate. The prompt (step 2).
The dashboard (step 3).

### Changed while implementing (2026-10-13)

- `kubectl rollout status` and `kubectl rollout history` are read-only: added to
  `is_read_only()` (`degraded_deployment` uses `rollout status` as a check).
  `rollout undo` / `restart` stay actions.
- `degraded_deployment` now has no action at all: it is a generic symptom whose cause
  is in its pods' own rules, so it only contributes checks.
- An LLM `#` comment cannot become the purpose of the next check: `_extract_bullets`
  never keeps comment lines. LLM diagnostics stay command-only until step 2.
- A rule hypothesis whose actions are only `#` comments no longer gets a `[rule:…]`
  header in `remediation` (nothing to group).

### Tests

- `tests/unit/test_diagnostics_split.py` keeps the `is_read_only` cases (imported from
  the new module) and checks `Diagnostic` for LLM lines.
- `tests/unit/test_analyzer_fallback.py`: rule diagnostics carry rule_id, affected,
  purpose and confirms_if; ordering and dedup; headers only in remediation.
- New `tests/unit/test_rule_checks.py`: the read-only contract above, and every rule
  declaring at least one check with a non-empty purpose and confirms_if.
- h022: its diagnostics are `source="llm"` (the scripted answer's
  `kubectl get events`), verdict unchanged.
