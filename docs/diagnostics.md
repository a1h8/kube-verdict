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
| 2 | The LLM proposes checks in their own prompt section, in the step-1 shape, instead of being told not to | implemented (2026-10-13), kept only if the replay shows no drop |
| 3 | Show diagnostics at the human gate and in the dashboard, grouped by hypothesis | implemented (2026-10-13) |
| 4 | Run read-only checks automatically and feed their output back as evidence | spec below — not implemented, needs a go |

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

## Step 2 — the LLM proposes its own checks

> Implemented 2026-10-13 (`PROMPT_VERSION = "v2"`). Decision pending the replay
> comparison below: baseline `v1` running, `v2` next.

### Why

The analyze prompt tells the model "Do NOT list diagnostic commands" under *Remediation*
(`rca/analyzer.py`). What it knows about how to verify its own diagnosis is thrown away.
When it lists them anyway, step 0 recovers them as bare `Diagnostic(source="llm")`, with
no purpose and no expected output. Step 2 gives those checks a place and the step-1
shape.

### Prompt

A new section **after** *Confidence*, so the existing numbering, parser markers and the
20 scripted answers in the tests do not move:

```
### 7. Checks
Up to 5 read-only commands that would confirm or rule out the root cause above,
one per line:
- <command> | verifies: <what it shows> | confirms if: <output> | refutes if: <output>
Read-only only (kubectl get / describe / logs / top, helm status / get / diff).
Never print Secret values (no `kubectl get secret -o yaml|json`).
```

*Remediation* keeps "commands that directly FIX the root cause". Its last sentence
becomes "Diagnostic commands go in section 7, not here".

### Parser

- *Confidence* now ends at `7. Checks` when the section exists. Answers without it
  parse as today.
- Each `- ` line in section 7 is split on ` | ` and read by prefix (`verifies:`,
  `confirms if:`, `refutes if:`). Missing fields stay `""`, so a bare command is still
  accepted.
- Kept only if `is_read_only(command)`. A non-read-only line is dropped and logged:
  an action can never enter through section 7. A command that reads Secret data
  (`get secret|secrets` with `-o yaml|json|jsonpath`) is dropped too.
- At most 5 kept. They become `Diagnostic(source="llm", purpose=…, confirms_if=…,
  refutes_if=…)` and are placed **before** the step-0 recovered lines. Deduplicated on
  the command.

### Measured, not assumed

The prompt change can change the answers themselves, not only add a section. The
veracity replay (`tools/veracity_replay.py --solver llm`) runs on the same cases before
(baseline, current prompt) and after. Step 2 is kept if the pass rate does not drop. The
report records the prompt version (`prompt: v1 | v2`, a constant in `rca/analyzer.py`)
so the two runs are never mixed.

### Tests

- Parser: a full line, a bare command, a non-read-only line dropped, a Secret-reading
  line dropped, more than 5 lines capped, no section 7 (unchanged parse), *Confidence*
  not swallowing section 7.
- `to_dict()` exposes LLM checks with their fields.
- The scripted answers (no section 7) keep producing the same reports: the existing
  workflow tests are the regression check.

## Step 3 — show diagnostics where the decision is made

> Implemented 2026-10-13. The empty-remediation message reads "No action to apply"
> alone when there are no checks either.

### Where

- **Human-gate payload** (`human_review_node`, `workflow/nodes.py`): adds
  `diagnostics`, the report's list of dicts. Additive; `remediation` is unchanged.
- **API** (`api/models.py` `IncidentReport`, `api/routes/sessions.py`): adds
  `diagnostics: list[dict]` to the incident report. `report` already carries the full
  report dict, so `state.report.diagnostics` is available without a new endpoint.
- **Dashboard** (`dashboard/src/DecisionJourney.jsx`): a `Diagnostics` component,
  shown under *Root cause* and inside *Human review*.

### What it shows

- Grouped by hypothesis: a rule's `hypothesis` (rule id) and `affected`, or
  "Proposed by the model" for `source: "llm"`. Groups follow the report order (rule
  weight first).
- Per check: the command (copyable), then *verifies*, *confirms if* and *refutes if*,
  each line shown only when not empty.
- The title says what they are not: **"Read-only checks — not part of the approval"**.
  Approve / Reject act on `remediation` only. That was already true; now it is
  visible.
- When `remediation` is empty, *Human review* says "No action to apply — run the checks
  below" instead of an empty list. The buttons stay: approving records the operator's
  decision, as today.

### Not changed

No execution from the dashboard (step 4). No new endpoint. The vitrine
(`docs/vitrine.md`) reuses the component when it is built.

### Tests

- `dashboard/src/DecisionJourney.test.jsx`: grouping by hypothesis, the llm group,
  empty fields hidden, the "not part of the approval" title, the empty-remediation
  message, nothing rendered when there are no diagnostics.
- `tests/unit`: the gate payload carries `diagnostics`, the API incident report too.

## Step 4 — run the checks, feed the output back as evidence

> Spec (2026-10-13). Not implemented: it reads a live cluster on its own, so it needs an
> explicit go, after the step-2 replay comparison.

### Why

A check the operator has to run by hand turns into a question asked after the verdict.
If the system runs it itself, the output becomes evidence **before** the verdict:
hypothesis → checks → output → re-analysis. This is the step that can raise veracity,
not only presentation.

### Security — "read-only" is not "safe"

1. **RBAC is the guarantee, not command parsing.** Checks run with kube-verdict's own
   service account. The gate is what that account is allowed to do, not
   `is_read_only()`, which only selects candidates.
   - *Decided 2026-10-13 (option B):* kube-verdict reads no Secret at all. `k8s/rbac.yaml`
     granted `get / list / watch` on `secrets` (values readable) while the Helm chart
     granted nothing; `secrets` is removed from `k8s/rbac.yaml` and the collector no
     longer lists Secrets. Whether a referenced Secret or key is missing comes from
     what Kubernetes already reports (`secret "x" not found`, `couldn't find key K in
     Secret …` in events and container status). Rejected, option A: a metadata-only
     list, which keeps the account able to read values and loses the key names anyway.
     Found while deciding (see `docs/veracity-replay.md` § *Evidence only the test
     loader produces*): the missing-dependency detector exists only in the test fixture
     loader, and the collector's pod → Secret edges were never created (pods are
     collected before Secrets), so the collected Secrets were only context text.
   - At startup, a `SelfSubjectRulesReview` per watched namespace. If any verb other
     than `get / list / watch` is allowed anywhere, auto-execution is disabled and
     recorded as `fallback: true` with the reason. Readable `secrets` is recorded as a
     warning; the executor refuses the kind anyway (rule 3).
2. **No shell, no command string executed.** A check is parsed into a structured query
   — verb (`get / describe / logs / top / events`), kind, name, namespace, label
   selector, and a small allowlisted flag set (`--previous`, `--tail`, `-o wide`,
   `--show-labels`) — and run through the Kubernetes Python client. Anything that does
   not parse into that shape (pipes, `jsonpath`, `helm`, unknown flags) is shown to the
   operator and never run. There is no `subprocess` call, so there is nothing to inject.
3. **Never Secrets**: the kind `secrets` is refused by the executor itself, even for
   names.
4. **Caps**: at most 5 checks per hypothesis path and 10 per session; 10 s per call;
   output cut to 4 KB and 50 log lines; logs only from pods already in the graph.
5. **Redaction** before the output enters the prompt or the state: `Bearer …`,
   `password=`, `token=`, `AKIA…`, private keys.
6. **Off by default**: `CHECKS_AUTORUN_ENABLED=false`. When on, every executed check is
   recorded (command, parsed query, byte count, digest, duration) in `edge_log` and
   shown in the Decision Journey next to the check.

### Flow

A new node `run_checks`, after `analyze` and before the confidence router, only when
the label is LOW or MEDIUM and the report has rule or LLM checks. Outputs enter the
context as a `### CHECKS` section, and **one** extra analyze runs on the same
hypothesis (bounded: never a loop). The re-analysis answer replaces the first one;
both are kept in `reasoning_history`.

### Offline and measured

- A case may ship `checks/`: a JSON map from the parsed query to an output, served by
  a fixture executor like the Prometheus / Loki / OTel fixtures. An unmapped check
  returns "no output".
- The veracity replay gets `--autorun-checks`. Step 4 is kept only if the pass rate
  rises over the step-2 baseline without more overconfidence.

### Tests

- Parser: each allowed shape, and each refusal (pipe, jsonpath, helm, unknown flag,
  `secrets`).
- RBAC self-check: an allowed `create` → disabled with the reason; readable `secrets` →
  warning, executor still refuses the kind.
- Caps and redaction.
- One case end to end offline (h014 first: `kubectl logs` on the not-ready pod returns
  the `x509` line through `checks/`): the second analyze prompt carries the check
  output, and the run is recorded in `edge_log`.

