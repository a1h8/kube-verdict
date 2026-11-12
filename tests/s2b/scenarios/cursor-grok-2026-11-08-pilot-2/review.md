# Review of pilot 2 (Grok, through the chat, same prompt as pilot 1), 2026-11-09

Automated checks: 5/11 pass after a fix to the checker, which did not read objects written as `{kind, namespace,
name, state}` mappings. The brief allows that form, and the fix has a test that fails without it. The remaining A1
flags are false positives of the heuristic: each partial names only the first hops (the quota without the Job that
fills it, for example), as rule A1 asks. The A10 flags are times quoted in a hop and missing from the timeline: they are
noted, but contradict nothing. Every scenario was then reviewed on Kubernetes facts and on the S2b rule that a family's
chain must differ from S2's. No scenario is edited.

| Scenario | Review | Reason |
|---|---|---|
| quota-1 | accept | a quota scoped by `scopeSelector` PriorityClass `high` counts only matching non-terminal pods (Kubernetes documentation, Resource quota scopes); 6 × 512Mi + 4 × 256Mi = 4Gi is consistent; the origin is the CronJob's priorityClassName change |
| netpol-1 | accept | an egress policy selecting checkout pods, whose peer (`namespaceSelector` AND `podSelector` in one item) stops matching once `Namespace/data` loses its label; DNS is still allowed; a dropped connection times out, as stated |
| dependency-1 | accept | a readiness probe on 8080 while redis listens on 6379: the pods are never Ready, the Service has no endpoint, and clients are refused. A10: the T0-2m event is missing from the timeline |
| limitrange-1 | **reject (S2 duplicate)** | an injected default memory limit OOMKilling new pods is S2's l1 chain; S2b requires chains S2 does not have. Kubernetes-correct |
| hpa-1 | accept | a `ContainerResource` metric naming a container that the rename removed: `ScalingActive=False`, `FailedGetContainerResourceMetric`, and the HPA keeps its last scale |
| webhook-1 | accept | a mutating webhook selected by a namespace label injects `runAsUser 1000`; the image writes as root; the webhook call succeeds (not a fail-closed case) |
| configmap-1 | **reject (S2 duplicate path)** | a mounted ConfigMap updated in place and reloaded by the process is S2's c2 path (a different content, the same object kind and path) |
| pdb-1 | **reject** | `minAvailable` as a percentage needs pods whose controller has a scale subresource; for a Job's pods only an integer `minAvailable` is allowed (Kubernetes documentation, Configure a PodDisruptionBudget, *Arbitrary workloads and the scale subresource*) |
| ingress-0 | **reject** | an HPA at 48 % of a 70 % target with a 300 s scale-down stabilization would scale below 12 within the hour it is said to hold 12; the decoy's explanation contradicts the HPA algorithm, so its truth "no fault" is disputable |
| storage-0 | accept | a Released PV no claim uses, a WaitForFirstConsumer PVC no pod references, a resolved alert |
| pullsecret-0 | accept | `FailedToRetrieveImagePullSecret` followed by `Pulled`: the kubelet's documented behaviour when one of several pull secrets is missing |

**Yield: 7/11 (64 %).** By the rule of `docs/s2b-generation.md`, 50 % to 70 % gives a second pilot batch, and this is the
second. Pilot 1 (the fast model) gave 3/11.
