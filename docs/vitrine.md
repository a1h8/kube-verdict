# Vitrine Narrative (B15)

> Spec for the last open item of **Showcase — Unified Vitrine** in `docs/roadmap.md`.
> Not implemented yet — this document precedes the code.

## Purpose

The 3 dashboards (`docs/dashboard.md`) exist and work, but as 3 isolated links:
**Evaluation Score**, **Decision Journey** (`#/journey`), **Monitoring Ops** (Grafana/OTLP).
A demo/pitch audience has to be told how they relate; nothing in the product tells
that story itself.

The Vitrine narrative is a landing view in `dashboard/` that chains them into one
guided path instead of 3 separate destinations:

```
alert fires → Decision Journey (reasoning + evidence) → verdict → Monitoring Ops (service health)
```

## Non-goals

- No new backend endpoint — reuses `GET /sessions/{id}/state` exactly as Decision
  Journey already does (`dashboard/src/DecisionJourney.jsx`).
- Does not replace or redesign any of the 3 existing dashboards.
- No deployment/infra change. This spec is about `dashboard/` only.

## Route

New hash route `#/vitrine`, following the existing tiny hash router in
`dashboard/src/main.jsx` (`#/journey` → `DecisionJourney`, else → `App`). The Score
dashboard (`App`) stays the default at `#/` — `#/vitrine` is an additional entry
point, linked prominently from the hero `Btn` row in `App.jsx` (next to the existing
`Decision Journey ▸` button).

## Structure — 4-step guided scroll

One scrollable page, not a wizard — each step is a section, in order:

1. **Alert** — the trigger that starts a session: Alertmanager webhook payload or
   manual `/run` query. Renders the same alert summary Decision Journey already
   derives from session metadata (namespace, resource, query) — no new field needed.
2. **Reasoning** — embeds the existing Decision Journey `Timeline` + `BeamTree` +
   `HypothesisEvidencePanel` components for that session, not a reimplementation.
3. **Verdict** — the `IncidentReport` card: root cause, confidence, blast radius,
   rollback plan, policy decision. Same data Decision Journey's verdict panel
   already renders.
4. **Service health** — embeds (iframe) or deep-links to the Grafana Monitoring Ops
   dashboard (`helm/kube-verdict/dashboards/monitoring-ops.json`), scoped to the
   time window of the session being told. Iframe only if Grafana's `allow_embedding`
   is on for the target instance; falls back to a plain link otherwise — this must
   be a config flag, not a hardcoded assumption, since embedding won't be enabled on
   every cloud/demo instance by default.

## Session source

A dropdown (or `?session=` query param) picks which session to narrate:

- Live: any session id from the running API.
- Demo/fixture: **not** `tests/golden/real_00N.json` — those only hold `risk` /
  `rollback_available` / `verdict` for the B11 regression diff, with no
  `reasoning_history`, `edge_log`, `hypothesis_sources` or `ingestion_stats`. There
  is nothing in them for the Reasoning step to render.
- The existing `dashboard/src/sampleJourney.js` (`SAMPLE_JOURNEY`) has the right
  *shape* (it's what Decision Journey already renders with zero backend on GitHub
  Pages) but the wrong *content* for this use case: a generic PVC/payment-api
  scenario where `ingestion_stats` shows `prometheus` / `otel` / `loki` **all
  `fallback: true`** — i.e. the one sample that exists today is a demo of the
  signals *not* being connected, which is the opposite of what step 4 (Service
  health) is supposed to prove.
- **New fixtures needed**: one `SAMPLE_JOURNEY`-shaped object per h013/h014/h015 —
  the three cases that exist specifically to exercise one observability signal
  each (`docs/roadmap.md` B14): SLO burn-rate (Prometheus), cert expiry (Loki),
  etcd compaction (OTel). Each needs real `reasoning_history` / `edge_log` /
  `hypothesis_sources` and `ingestion_stats` with the matching collector
  `fallback: false`. **Live evidence to source this from is uneven across the
  three — checked against `tests/golden/` and `docs/evidence/` directly, not
  assumed:**
  - **h015 (OTel)** — captured live twice, frozen in `tests/golden/real_003.json`
    and `real_004.json` (commit `727d82c`, after fixing two real Tempo bugs).
    Richest source to build the fixture from.
  - **h014 (Loki)** — captured live once (`docs/evidence/prometheus-live.md`,
    2026-09-26T10:37), but only as narrative prose, not frozen as a golden JSON.
    Usable, but the fixture's `reasoning_history`/`edge_log` will need to be
    reconstructed from that write-up rather than copied from a structured file.
  - **h013 (Prometheus)** — **no live capture exists at all.** Only the
    deterministic fixture (`tests/integration/cases/h013_*/`) validated through
    the real collector code path offline — it has never been run against an
    actual firing SLO burn-rate alert. Building a "live-looking" sample for it
    today means authoring plausible-but-not-actually-observed content, which
    undercuts the vitrine's whole point (proving signals are really connected).
    **Capturing h013 live first** (same `tools/b13_capture.py` path used for
    h001/h002/h015) should happen before this fixture is written, not after.

## Relation to the GCP/Scaleway goal

This is a `dashboard/` front-end change — it works unchanged against local, GCP or
Scaleway, because it only ever reads whatever `PROMETHEUS_URL` / `LOKI_URL` /
`OTEL_BACKEND_URL` / API endpoint the deployment already points at (`config.py`,
`docs/cloud-prerequisites.md`). The vitrine is the artifact that turns the
`cloud-prerequisites.md` §5 validation gate into something an audience watches,
not just a checklist. Building it now (against local/fixture data) means the only
work left before the real GCP/Scaleway demo is the infra in `cloud-prerequisites.md`
itself — not more dashboard code.

## Acceptance criteria

- [ ] `#/vitrine` renders all 4 sections from a single session id, local or fixture.
- [ ] Works with zero backend running, using the new `SAMPLE_JOURNEY`-shaped fixtures
  described in *Session source* (h015 built from `real_003.json` / `real_004.json`, h014
  reconstructed from `docs/evidence/prometheus-live.md`, h013 only once it has been
  captured live) — not the `real_00N.json` golden files themselves, which carry no
  reasoning/evidence to render. Mirrors how the Score dashboard needs no backend.
- [ ] Monitoring Ops embed/link is driven by a config flag, not hardcoded to one Grafana instance.
- [ ] Linked from the hero section in `App.jsx`.
- [ ] Component test following the existing `DecisionJourney.test.jsx` pattern.
