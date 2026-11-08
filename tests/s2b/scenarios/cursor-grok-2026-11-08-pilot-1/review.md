# Review of the pilot batch (Grok, fast model, in Cursor), 2026-11-08

Automated checks (`tools/s2b_check.py`): 4/11 pass. Then the engine's author reviewed each scenario, every one including
those that pass, and rejected only on a fact of Kubernetes or on the brief's rules. No scenario is edited.

| Scenario | Checks | Review | Reason |
|---|---|---|---|
| quota-1 | fail (1 hop) | **reject** | a ResourceQuota is namespaced and constrains only its own namespace; a pod of `shop` cannot be refused by `payments/rq-compute` ("cross-namespace accounting" does not exist) |
| netpol-1 | pass | **reject** | a NetworkPolicy selects pods of its own namespace only (`podSelector`); `namespaceSelector` selects peers, so a policy in `observability` cannot restrict the egress of `shop` pods. A dropped packet also times out; it is not refused |
| dependency-1 | pass | accept | redis scaled to 0 leaves its Service with no endpoints; the clients' connections are refused. Note for the fixtures: "no such host" is wrong, since a Service name resolves without endpoints; the builder writes the documented refusal |
| limitrange-1 | fail (1 hop, size) | **reject** | with a 1Gi request and no limit, LimitRanger sets the 256Mi default limit, so the refusal is a request above the limit, not "limit is 1Gi"; the quoted message contradicts the mechanism |
| hpa-1 | fail (size) | **reject** | the condition is `ScalingActive=False` with reason `FailedGetPodsMetric`, not `AbleToScale`; a ConfigMap does not remove a sidecar from a pod template; HPA → ConfigMap is not a relation the API defines |
| webhook-1 | fail (1 hop, kind, size) | **reject** | its decoy is a kind the product cannot read (a ValidatingWebhookConfiguration), so it is invisible; `failurePolicy: Fail` is not stated, and without it the pod is admitted |
| configmap-1 | fail (size) | **reject** | `features::enabled` is a valid YAML key, so the stated parse failure cannot happen |
| pdb-1 | pass | accept | `minAvailable: 2` with 2 replicas allows 0 disruptions. The drain's message is kubectl's output, not an event: the evidence is the PDB's `status.disruptionsAllowed: 0` and the Node's `spec.unschedulable` |
| ingress-0 | pass | accept | no incident; decoys visible (a resolved alert, a harmless annotation, a policy in another namespace) |
| storage-0 | fail (22 objects) | accept on content, fails the size rule (A9) | |
| pullsecret-0 | fail (24 objects) | accept on content, fails the size rule (A9) | |

**Yield**, the pilot rule of `docs/s2b-generation.md`:
- **3/11 (27 %)** strictly;
- **5/11 (45 %)** if the size rule alone is waived.

Both are under 50 %. By the rule, a second pilot is run. Two of the rejected mechanisms are wrong on the scope of
namespaced objects (quota-1, netpol-1), the same kind of error as Gemini Flash's rounds.
