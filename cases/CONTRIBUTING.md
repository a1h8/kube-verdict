# Adding a case to the test bank

A **case** is a real or realistic K8s failure that validates the full pipeline
from ingestion to diagnosis — without an LLM.

There are two case formats, both valid and complementary:

| Format | Location | Input | When to use |
|---|---|---|---|
| **Synthetic** (this guide) | `cases/NNN_slug/` | `input.json` — manually crafted cluster state | Fast iteration, unit-level calibration |
| **Helm/kubectl** | `cases/helm_cases/hNNN_slug/` | `helm/values.yaml` + `observed/*.json` | Real Helm charts + captured kubectl output |

The synthetic format remains the primary calibration tool because it runs fully offline
(no kubectl, no cluster) and directly controls every pipeline input.
See `cases/helm_cases/` and `tests/helm_cases/` for the Helm format.

---

## Structure

```
cases/
└── 021_my_scenario/
    ├── input.json    ← cluster state (query + events + pod_status + anchors…)
    └── expect.json   ← human-written expectations (root_cause, fix, confidence)
```

Naming: **3-digit prefix**, increment by 1 (`021`, `022`…).  
Directory name: `NNN_<slug>` — lowercase, underscores, no spaces.

---

## `input.json` schema

```jsonc
{
  // ── Required ──────────────────────────────────────────────────────────────
  "scenario":  "Short readable name (e.g. CrashLoopBackOff - Missing Secret)",
  "namespace": "production",
  "query":     "Natural question as an SRE would ask it",
  "events":    [ /* see Events below */ ],

  // ── Pod (null if no pod is involved) ─────────────────────────────────────
  "pod_status": {
    "phase": "Pending | Running | Failed | Succeeded | Unknown",
    "containerStatuses": [
      {
        "name":         "container-name",
        "state":        { "waiting": { "reason": "CrashLoopBackOff", "message": "…" } },
        "lastState":    { "terminated": { "reason": "OOMKilled", "exitCode": 137 } },
        "restartCount": 0,
        "ready":        false
      }
    ],
    "initContainerStatuses": [ /* same structure, optional */ ],
    "conditions": [ { "type": "Ready", "status": "False", "reason": "…" } ]
  },

  // ── Helm drift (empty diffs [] if none) ───────────────────────────────────
  "helm_drift": {
    "release":   "release-name",
    "namespace": "production",
    "diffs": [
      {
        "field":    "container.myapp.resources.limits.memory",
        "declared": "512Mi",
        "observed": "50Mi"
      }
    ]
  },

  // ── Anchors: declared values relevant to the diagnosis ────────────────────
  // Format: "Kind/namespace/name: field.path declared='val' [source] | observed='val' [drift]"
  // [source] = manifest | schema | drift
  "anchors": [
    "Pod/production/myapp-xxx: container.myapp.resources.limits.memory declared='512Mi' [manifest] | observed='50Mi' [drift]"
  ],

  // ── Optional fields (null / [] when not relevant) ─────────────────────────
  "pvc_status": {
    "name": "my-pvc", "namespace": "production",
    "phase": "Pending", "storageClassName": "fast-ssd",
    "accessModes": ["ReadWriteOnce"], "storage": "50Gi"
  },
  "policy_report": {
    "policy": "policy-name",
    "violations": [
      {
        "resource": "Pod/production/myapp-xxx",
        "rule":     "check-rule",
        "message":  "Violation description",
        "result":   "fail"
      }
    ]
  },
  "metrics": {
    "pod": "myapp-xxx", "memory_mi": 490, "memory_limit_mi": 512, "usage_ratio": 0.96
  },
  "network_policies": [
    {
      "name": "deny-all-ingress", "namespace": "production",
      "spec": { "podSelector": {}, "policyTypes": ["Ingress"] }
    }
  ],
  "available_storage_classes": ["standard", "local-path"],
  "symptom": "Free-text description of an observable symptom absent from K8s events (e.g. connection refused seen in app logs)"
}
```

### `events[]` item

```jsonc
{
  "type":      "Warning | Normal",
  "reason":    "FailedScheduling | BackOff | OOMKilling | …",
  "object":    "Pod/myapp-xxx  or  Node/worker-01  or  ReplicaSet/myapp-deploy",
  "message":   "Full message as returned by kubectl get events",
  "count":     12,
  "firstTime": "2026-05-13T09:00:00Z",
  "lastTime":  "2026-05-13T09:15:00Z"
}
```

---

## `expect.json` schema

```jsonc
{
  // Resources directly affected (one or more)
  "affected_resources": ["Pod/production/myapp-xxx"],

  // Keywords that MUST appear in the context sent to the LLM
  // At least 50% must be found for the test to pass
  "root_cause_contains": ["memory", "limit", "OOMKilled", "drift"],

  // Substrings that must appear in the suggested remediation commands
  "fix_commands_contain": ["helm upgrade", "resources.limits.memory"],

  // Expected confidence level: "HIGH" | "MEDIUM" | "LOW"
  "confidence": "HIGH",

  // Minimum score the pipeline must reach on a synthetic graph
  // Rule of thumb: set ~0.10 below the _debug_score_breakdown total
  "confidence_score_min": 0.60,

  // true if the case should trigger the RemediationEngine (rule-based fallback)
  "fallback_expected": false,

  // Human explanation of the case — shown in the Dashboard tab
  "notes": "Concise explanation of what this case exercises in the pipeline.",

  // Indicative score breakdown (informational — not executed by tests)
  "_debug_score_breakdown": {
    "bfs_c":    0.20,
    "jac_c":    0.20,
    "tfidf_c":  0.14,
    "anchor_c": 0.10,
    "signal_c": 0.12,
    "policy_c": 0.00,
    "total":    0.76
  }
}
```

---

## How to calibrate `confidence_score_min`

The pipeline score on a **synthetic graph** is typically 0.10–0.20 below the ideal score
(no Prometheus, no OTel, no FAISS history).

| Situation | Suggested `confidence_score_min` |
|---|---|
| Strong Helm drift (3+ diffs) + anchors + restarts | 0.60–0.70 |
| Helm drift (1–2 diffs) + OOMKilled/CrashLoop | 0.45–0.55 |
| Light drift + few anchors | 0.40–0.50 |
| No K8s events, symptom only | 0.30–0.40 |
| Kyverno policy violation present | add +0.10 |

**Quick formula**: `confidence_score_min = _debug_score_breakdown.total − 0.15`  
Then adjust after running `pytest tests/cases/ -k "score_meets_minimum"`.

---

## Score components (for `_debug_score_breakdown`)

| Component | Formula | Cap |
|---|---|---|
| `bfs_c` | `min(bfs_depth / 5, 1) × 0.25` | 0.25 |
| `jac_c` | `min(jaccard_kept_ratio, 1) × 0.25` | 0.25 |
| `tfidf_c` | `min(tfidf_top_k / 20, 1) × 0.20` | 0.20 |
| `anchor_c` | `min(anchor_count × 0.05, 0.15)` | 0.15 |
| `signal_c` | `min(signal_count × 0.03, 0.15)` | 0.15 |
| `drift_c` | `min(drift_item_count × 0.07, 0.20)` | 0.20 |
| `policy_c` | `min(fail × 0.10 + audit × 0.05 + webhooks × 0.05, 0.30)` | 0.30 |

Typical `bfs_depth` on a synthetic graph: 3 for most cases, 4 when a Node or PVC is involved.  
`jaccard_kept_ratio`: 0.80–1.0 (small graph → few duplicates removed).  
`tfidf_top_k`: count of chunks in `ctx.related` (typically 1–10 on a synthetic graph).  
`anchor_count`: number of entries in the `anchors[]` field of `input.json`.  
`signal_count`: unhealthy seeds + firing Prometheus alerts (excludes drift — counted separately).  
`drift_item_count`: number of drift annotations in the context (one per `helm_drift.diffs[]` entry, plus OOMKilled/CrashLoopBackOff pod drift, plus Deployment readyReplica mismatch).

**Example for a case with 3 Helm drift diffs + 1 OOMKilled pod drift:**  
`drift_c = min(4 × 0.07, 0.20) = 0.20`

---

## What belongs in `anchors[]`

An anchor is a declared value (manifest or K8s schema) relevant to the diagnosis.

```
# Observed drift (manifest → live)
"Pod/production/myapp-xxx: container.myapp.image declared='v1.0' [manifest] | observed='v2.0-private' [drift]"

# Schema value (default or important field)
"Pod/production/myapp-xxx: container.myapp.readinessProbe.httpGet.path declared='/health' [schema]"

# Declared value without drift (context only)
"PersistentVolumeClaim/production/data: spec.storageClassName declared='fast-ssd' [manifest]"
```

Rule: **at least 1 anchor** per case. Anchors drive the `helm upgrade --set` fix suggestions.

---

## Good cases vs cases to avoid

| ✅ Good case | ❌ Avoid |
|---|---|
| Helm drift + clear symptom | Isolated event with no identifiable cause |
| Kyverno policy violation that lifts confidence | Case too similar to an existing one |
| Init container / probe / resource quota failure | Case that requires live Prometheus or OTel |
| Two compounding drifts (e.g. case 019) | Fully healthy pod (no signal at all) |

---

## Running the tests

```bash
# All cases (slow — ~10 min, runs the embedder)
pytest tests/cases/ -q

# Single case
pytest tests/cases/ -k "021"

# Collection only (fast — validates JSON + graph, no embedder)
pytest tests/cases/test_case_bank.py::TestGraphSeeds -q

# Verify score of a new case before adding it
pytest tests/cases/test_case_bank.py::TestConfidenceInputs -k "021" -v
```

---

## Submission checklist

- [ ] Directory named `NNN_slug` (3-digit prefix)
- [ ] `input.json` valid (optional fields set to `null`, not omitted)
- [ ] `expect.json` with non-empty `root_cause_contains` and `fix_commands_contain`
- [ ] At least 1 anchor in `input.json`
- [ ] `confidence_score_min` calibrated after running `pytest -k "NNN"`
- [ ] `notes` explains what this case exercises that existing cases do not
- [ ] All tests pass: `pytest tests/cases/ -q`
