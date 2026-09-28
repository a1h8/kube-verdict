# Demo Recording Runbook

## Goal

Record the real KubeVerdict UI with OBS Studio, using the `🎬 Demo` tab as the
main flow, plus a shot of the live Grafana Monitoring Ops dashboard (B15).

This avoids brittle browser automation and makes repeat takes easy.

## Prerequisites

- Ubuntu/GNOME (this project's dev environment) — Kap is macOS-only, so this
  runbook uses OBS Studio instead (the closest equivalent: window capture,
  fixed frame rate, cursor toggle, no audio mixing needed since narration is
  added in post anyway).
- Install OBS Studio yourself (`sudo apt install obs-studio`) — needs a
  password prompt only you can answer.
- No-install alternative: GNOME's built-in screen recorder
  (`Ctrl+Shift+Alt+R` to start/stop, saves to `~/Videos/Screencasts/`) is
  enough for a silent, single-take recording — it just can't do the
  named-per-shot multi-take workflow below as cleanly as OBS's scenes.
- Streamlit UI running on `http://127.0.0.1:8501`
- Grafana reachable on `http://127.0.0.1:3000` (`kubectl -n kubeverdict-obs
  port-forward svc/grafana 3000:80`), logged in as `admin` — see the
  Monitoring Ops dashboard (`kubeverdict-ops` uid)
- Firefox open on both, one tab each
- Audio already generated:
  - `docs/demo-voiceover-90s.mp3`
  - `docs/demo-voiceover-90s.srt`

## Recommended demo flow

Use the UI in this order:

1. `🔍 Root Cause Analysis`
2. `🎬 Demo`
3. `Act 1 — Dead End`
4. `Run simulation`
5. Follow one branch to a dead end
6. `Backtrack`
7. `Compare strict vs lenient`
8. Switch to `Lenient demo`
9. Run again
10. `Act 2 — Human Gate`
11. Run until `Operator decision`
12. Switch to the Grafana tab — Monitoring Ops dashboard, live panels

## OBS Studio settings

- Source: **Window Capture** (not Display Capture) — pick the Firefox
  window, not the whole screen
- Resolution: keep the Firefox window at a stable size
- Frame rate: 30 fps is enough
- Cursor: enabled (Window Capture source properties → "Show Cursor")
- Mic: off (no Audio Input Capture source)
- System audio: off (no Desktop Audio source, or muted in the mixer)
- One Scene per shot (see naming below) makes retakes fast — duplicate the
  Scene, only the browser tab differs between them

Record silent video first. Add narration later in post.

## Stable browser layout

- Use one Firefox window only, one tab per shot
- Keep zoom at `100%`
- For shots 1-5: keep the `🎬 Demo` tab visible
- For shot 6: switch to the Grafana tab, `kubeverdict-ops` dashboard,
  auto-refresh already set to `10s` in the dashboard JSON — no manual refresh
  needed mid-shot
- Do not resize during recording

## Recording script

### Shot 1 — Live pipeline intro

Duration: `3–5s`

- Open `🔍 Root Cause Analysis`
- Show that the pipeline is active
- Do not stay on this screen

Narration:

> This confirms the pipeline is active, but the live context does not contain enough evidence for a useful diagnosis.

### Shot 2 — Dead end

Duration: `25–35s`

- Open `🎬 Demo`
- Click `Act 1 — Dead End`
- Keep:
  - case `h009_liveness_probe_loop`
  - mode `Manual (step-by-step)`
  - threshold `Strict demo`
- Click `Run simulation`
- Follow one branch until `dead end`

Narration:

> Now we switch to a controlled incident case.  
> This branch looks plausible, but confidence does not improve enough, so the system marks it as a dead end.

### Shot 3 — Backtrack

Duration: `8–12s`

- Click `Backtrack`

Narration:

> The failed path remains visible, so we can inspect it and backtrack.

### Shot 4 — Threshold comparison

Duration: `15–20s`

- Click `Compare strict vs lenient`
- Then switch to `Lenient demo`
- Run again

Narration:

> Same problem. Same signals. Different routing policy.  
> This time, the system converges more cleanly.

### Shot 5 — Human gate

Duration: `15–20s`

- Click `Act 2 — Human Gate`
- Run until `Operator decision`
- Show `approve` and optionally `reject`

Narration:

> The system reaches a valid remediation path, but the final operational decision remains human.

### Shot 6 — Monitoring Ops dashboard (B15)

Duration: `10–15s`

- Switch to the Grafana tab, `kubeverdict-ops` dashboard
- Let it sit through one auto-refresh (10s) so the panels visibly update,
  not a static screenshot
- Hover briefly over the p95 latency panel to show the tooltip

Narration:

> And this is the system watching itself — request latency, error rate, LLM call duration, and collector health, exported by KubeVerdict's own OpenTelemetry instrumentation into the same observability stack.

## Multiple takes

For repeatable takes:

- keep one take per shot (one OBS Scene per shot)
- export/save separate files
- name them:
  - `01-live-intro.mkv`
  - `02-dead-end.mkv`
  - `03-backtrack.mkv`
  - `04-thresholds.mkv`
  - `05-human-gate.mkv`
  - `06-monitoring-ops.mkv`

This makes retries much easier than one long recording.

## Final edit

Assemble in this order:

1. `01-live-intro`
2. `02-dead-end`
3. `03-backtrack`
4. `04-thresholds`
5. `05-human-gate`
6. `06-monitoring-ops`

Then add:

- `docs/demo-voiceover-90s.mp3`
- optional subtitles from `docs/demo-voiceover-90s.srt`

Note: the voiceover script/timing (`docs/demo-voiceover-90s.md`) predates
shot 6 — extend the narration and timing to cover it before syncing audio,
or leave shot 6 silent/self-explanatory with on-screen text instead.

## Final message

Use this as the closing line:

> The system can explore, compare, and justify decisions, but the final action stays under human control.
