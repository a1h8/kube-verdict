# S2b — validation cases for the correlation engine, written before its code

> Spec 2026-11-08, no case written yet. Review before any case. Purpose: `docs/correlation-engine.md` § 5. S2 is
> now a development set (`docs/s2-results.md`). S2b measures whether the engine generalizes, so its cases must not
> be written to fit it.

## What S2b inherits from S2

The rules of `docs/s2-correlation-cases.md` hold unchanged:
- a cause two or more hops from the symptom;
- the evidence for each hop, in `evidence.yaml`;
- at least 2 decoys, tens of objects and at least 2 namespaces per case;
- a time order that holds, and an external source.

They also share the same fixtures format, the same parity (the real collectors over the fake API), the same
freeze by SHA-256, the same arms (D, L₀, S₀) and the same blind grading (`docs/s2/grading.md`, unchanged).

## What differs

**Size and families.** At least 33 cases, in 11 families of 3, each family with 2 causes and 1 case with no
incident. That gives 22 causes and 11 cases with no incident.
- **The 7 families of S2 again,** with chains S2 does not have. A family keeps its kind of fault, but not the
  object at fault or the path to it. For example, quota by a StatefulSet's PVCs rather than by requests or Jobs;
  NetworkPolicy by an egress rule rather than ingress.
- **4 families S2 has none of**, all on objects the collectors already read:

| Family | An example of a chain (the author picks their own) |
|---|---|
| `pdb` | A PodDisruptionBudget with `minAvailable` equal to the replicas blocks a drain, so a node upgrade stalls. |
| `ingress` | An Ingress backend names a Service port that no longer exists after a chart upgrade. |
| `storage` | A PVC stays Pending because its StorageClass's provisioner is gone, so the StatefulSet stops scaling. |
| `pullsecret` | The `imagePullSecrets` of a ServiceAccount point to a Secret rotated out, so a new pod fails to pull. |

**Decoys from S2's lessons.** Each case with a cause holds at least one decoy of a kind that misled an arm on S2,
as the case's spec states:
- a resolved fault older than the symptom;
- a drift with no effect;
- a first-hop object a rule would name.

Each case with no incident holds faults that look current but are over, or that sit in another namespace and
have no effect.

## Who writes the cases

The person or model who writes the engine also wrote S2. To keep S2b independent of the engine, these four steps
are kept apart, each recorded:

1. **The scenarios** are written by a model of another provider, or by a person, from a brief that holds:
   - this document's rules and families;
   - the fixtures format;
   - the list of kinds the collectors read.

   The brief does not hold `docs/correlation-engine.md`, the engine's code or S2's results. Each scenario gives:
   - the question;
   - the chain, hop by hop, with the object fields that carry each hop;
   - the decoys;
   - the ground truth: cause object, origin, mechanism, what passes, what is only partial.

   The brief and the scenarios are kept in `tests/s2b/scenarios/` as given.
2. **The fixtures** are built from the scenarios with the S2 builders. The builder may fix a scenario only where
   Kubernetes would not accept it, for example an invalid field or an impossible order. Each such fix is logged
   in the case's spec with its reason, and the ground truth is never changed.
3. **The check.** The author of the scenarios, or another reader, checks that each built case still shows its
   chain from the evidence alone.
4. **The freeze**, as for S2, before the control is run.

## Order

1. Review this spec.
2. The brief, then the scenarios, from an external author.
3. The fixtures, their check, the freeze.
4. The control: D as it is now, on S2b, offline. Its records are kept like S2's, and its answers are graded
   blind like S2's.
5. Only then the engine's code (`docs/correlation-engine.md` § 6).

## Not in this spec

- S3 (ITBench).
- The engine itself.
- Any change to S2 or its results.
