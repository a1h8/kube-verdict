# 90-Second Demo Voiceover

The demo now opens on the **anchor-by-render** wedge with a **BEFORE/AFTER** beat
(`kubectl` symptom → KubeVerdict verdict), then walks the decision process. The
voiceover is **multi-voice**: the anchor-by-render intro uses a different voice
than the rest, for contrast.

## Two outputs, two purposes

| Video | Purpose | Script | Builder |
|-------|---------|--------|---------|
| `demo-voiceover-90s-slides.mp4` | **General demo** — narration | [`demo-voiceover-segments.json`](demo-voiceover-segments.json) | `generate_voiceover.py` (ElevenLabs) or macOS `say` → `render_demo_video.py` |
| `demo-dialogue.mp4` | **FAQ** — a skeptic (“why not just kubectl?”) ↔ expert, making the *why* explicit | [`demo-voiceover-dialogue-segments.json`](demo-voiceover-dialogue-segments.json) | `build_dialogue_demo.py` (macOS `say`, no key) |

Both reuse the same scenes (anchor intro → BEFORE/AFTER → decision process → human gate).

## Canonical source

The script lives in [`demo-voiceover-segments.json`](demo-voiceover-segments.json) —
a list of segments, each naming a voice. It is the single source of truth for both
the audio and the flat TTS text.

- **Voices** (ElevenLabs classic pre-made, free tier — swap `voice_id` for your own):
  - `main` → **Adam** (`pNInz6obpgDQGcFmaJgB`) — narration, dynamic settings.
  - `anchor` → **Rachel** (`21m00Tcm4TlvDq8ikWAM`) — the anchor-by-render intro, for contrast.

## Render pipeline

```bash
export ELEVENLABS_API_KEY=sk-...
python tools/generate_voiceover.py     # segments → docs/demo-voiceover-90s.mp3 (multi-voice)
python tools/render_demo_video.py      # slides + audio → docs/demo-voiceover-90s-slides.mp4
```

`generate_voiceover.py --dry-run` regenerates `demo-voiceover-90s-tts.txt` and prints the
voice plan without calling the API (spends no credits). The slides and the `.mp3`/`.mp4`
outputs are build artifacts (git-ignored); the `.json` script is the tracked source.

## Narrative (voice in brackets)

1. **[anchor]** Most Kubernetes tools start from live symptoms. KubeVerdict starts one step earlier.
2. **[anchor]** It renders what should be running — from Helm/GitOps — and compares it to the live cluster.
3. **[anchor]** The drift between declared intent and live reality becomes the evidence. This is anchor-by-render.
4. **[main]** BEFORE — `kubectl` shows the pod crash-looping and OOMKilled. Loud symptom, cause elsewhere.
5. **[main]** AFTER — KubeVerdict renders the expected state and finds the drift: memory 512Mi→128Mi, replicas 3→1.
6. **[main]** Strict threshold profile: it follows a plausible path and scores it…
7. **[main]** …but confidence does not improve. Dead end — and the failure stays visible; we backtrack.
8. **[main]** Same incident, lenient policy: same signals, different routing, cleaner convergence.
9. **[main]** Networking incident: a valid remediation, but it stops at the human decision gate.
10. **[main]** Explore, compare, justify — the final operational choice stays with you.

## Click Track (recording the UI over the narration)

- Show the **BEFORE** terminal: `kubectl get pods` (CrashLoopBackOff) + `describe` (OOMKilled)
- Cut to KubeVerdict → **🎯 Render-vs-live** step on `h012_gitops_render_vs_live` (the AFTER drift table)
- Load `h009_liveness_probe_loop` · `Manual (step-by-step)` · `Strict demo` · `Run simulation`
- Follow one branch to a `dead end` → `Backtrack`
- Rerun with `Lenient demo` (cleaner convergence)
- Switch to `h006_networkpolicy_blocked` → run until `Resolution found`
- Show `Operator decision` → `approve` / `reject`
