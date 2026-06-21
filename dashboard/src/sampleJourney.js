// Recorded sample investigation, bundled client-side so the Decision Journey
// demos on the static GitHub Pages build (no API backend). Mirrors the shape
// POST /sessions/sample returns (api/sample_journey.py → _state_to_response):
// report_dict surfaced as `report`, plus status + review_payload. The
// ingestion_stats drive the B9 fallback-status overlay.
const report = {
  summary: "payment-api is CrashLoopBackOff because PVC payment-data is unbound.",
  root_cause:
    "No PersistentVolume matches storageClass 'standard' for the 10Gi payment-data PVC, so the pod cannot mount its volume.",
  confidence: "HIGH",
  causal_chain: [
    "PVC payment-data is Pending",
    "pod cannot mount volume",
    "container fails to start → CrashLoopBackOff",
  ],
  remediation: [
    "kubectl describe pvc payment-data -n production",
    "kubectl apply -f pv-standard-10gi.yaml",
  ],
  rollback: ["kubectl delete -f pv-standard-10gi.yaml"],
  events: [
    "Warning FailedMount pod/payment-api-xyz: Unable to attach or mount volumes: payment-data",
  ],
  alerts: ["FIRING KubePodCrashLooping: payment-api severity=critical"],
  policy_violations: ["FAIL require-limits: container payment-api has no memory limit"],
};

export const SAMPLE_JOURNEY = {
  session_id: "sample",
  status: "AWAITING_REVIEW",
  query: "[SAMPLE] payment-api pods crashlooping in production",
  confidence: "HIGH",
  verdict: "HUMAN_REVIEW",
  verdict_reasons: [
    "namespace 'production' is production — always HUMAN_REVIEW minimum",
    "blast radius MEDIUM — review before applying",
  ],
  current_hypothesis:
    "PVC payment-data is Pending — no PersistentVolume matches storageClass 'standard'",
  blast_radius: {
    risk: "MEDIUM",
    summary: "1 namespace, 2 resources, rollback available",
    resources: ["PersistentVolumeClaim/payment-data", "Deployment/payment-api"],
    namespaces: ["production"],
    cluster_scoped: false,
    command_count: 2,
    rollback_available: true,
  },
  reasoning_history: [
    {
      step: 1,
      hypothesis: "OOMKilled — memory limit too low on payment-api",
      confidence: "LOW",
      retry_count: 1,
      summary:
        "No OOM events and metrics show memory well under limit — probability declining, switched.",
    },
    {
      step: 2,
      hypothesis: "ImagePullBackOff — registry auth drift",
      confidence: "LOW",
      retry_count: 2,
      summary:
        "Image pulls succeed; events show FailedMount, not pull errors — retries exhausted, switched.",
    },
  ],
  edge_log: [
    {
      router: "confidence",
      edge_taken: "retry",
      reason: "confidence=LOW — retrying (1/2) on OOM hypothesis",
      snapshot: { confidence: "LOW", retry_count: 1, max_retries: 2 },
    },
    {
      router: "confidence",
      edge_taken: "next_path",
      reason: "probability declining (LOW×2) — early switch to next hypothesis",
      snapshot: { confidence: "LOW", retry_count: 2, max_retries: 2 },
    },
    {
      router: "policy",
      edge_taken: "review",
      reason:
        "HUMAN_REVIEW: namespace 'production' is production — always HUMAN_REVIEW minimum",
      snapshot: { confidence: "HIGH", score: 0.85, risk: "MEDIUM" },
    },
  ],
  drift_evidence: [
    {
      kind: "Deployment",
      name: "payment-api",
      namespace: "production",
      diffs: [
        { field_path: "spec.replicas", declared: "3", observed: "1", severity: "critical" },
        { field_path: "container.payment-api.resources.memory", declared: "512Mi", observed: "128Mi", severity: "warning" },
      ],
    },
  ],
  ingestion_stats: {
    k8s: { fallback: false },
    helm: { fallback: false },
    metrics: { fallback: false },
    prometheus: { fallback: true, error: "no Prometheus endpoint configured" },
    otel: { fallback: true, error: "OTel collector unreachable" },
    loki: { fallback: true, error: "Loki endpoint not set" },
  },
  report,
  causal_chain: report.causal_chain,
  suggestions: report.remediation,
  review_payload: {
    summary: report.summary,
    root_cause: report.root_cause,
    remediation: report.remediation,
    confidence: "HIGH",
    no_solution: false,
  },
};
