# Demo Recording Runbook

## Goal

Record the real KubeVerdict UI with Kap, using the `🎬 Demo` tab as the main flow.

This avoids brittle browser automation and makes repeat takes easy.

## Prerequisites

- Streamlit UI running on `http://127.0.0.1:8501`
- Firefox open on the UI
- Kap installed
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

## Kap settings

- Capture area: browser window only
- Resolution: keep Firefox window at a stable size
- Frame rate: 30 fps is enough
- Cursor: enabled
- Mic: off
- System audio: off

Record silent video first. Add narration later in post.

## Stable browser layout

- Open Firefox on `http://127.0.0.1:8501`
- Use one window only
- Keep zoom at `100%`
- Keep the `🎬 Demo` tab visible
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

## Multiple takes

For repeatable takes:

- keep one take per shot
- save separate files from Kap
- name them:
  - `01-live-intro.mov`
  - `02-dead-end.mov`
  - `03-backtrack.mov`
  - `04-thresholds.mov`
  - `05-human-gate.mov`

This makes retries much easier than one long recording.

## Final edit

Assemble in this order:

1. `01-live-intro`
2. `02-dead-end`
3. `03-backtrack`
4. `04-thresholds`
5. `05-human-gate`

Then add:

- `docs/demo-voiceover-90s.mp3`
- optional subtitles from `docs/demo-voiceover-90s.srt`

## Final message

Use this as the closing line:

> The system can explore, compare, and justify decisions, but the final action stays under human control.
