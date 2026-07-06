"""Build the short conversion demo (~30s) with burned-in subtitles.

Direct script (docs/demo-short-segments.json), slow macOS `say` voice, one
subtitle per spoken line, flow: Incident → Evidence → Verdict → Human Gate. This
is the cold-visitor entry point; the 78s decision walkthrough stays as advanced
proof.

Subtitles are burned into each frame with PIL (this machine's ffmpeg is a minimal
build with no libass/drawtext, so the subtitles filter is unavailable). One frame
per line = the scene's visual + that line's caption, timed to the line's audio.

Usage:  python tools/build_short_demo.py   (no API key)
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import tools.render_demo_video as r

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SCRIPT = DOCS / "demo-short-segments.json"
ASSETS = DOCS / "demo-video-assets" / "short"
MP3 = DOCS / "demo-short.mp3"
MP4 = DOCS / "demo-short.mp4"
SHARE = ROOT / "demo" / "kubeverdict-short.mp4"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

SHORT_SCENES = [
    {"kind": "terminal", "title": "Incident detected", "tag": "incident", "lines": [
        ["$ ", "kubectl get pods -n production", ""],
        ["", "api-6d8f9b7c4-xvk2p   0/1   CrashLoopBackOff   7", "red"],
        ["", "  Last State: Terminated   Reason: OOMKilled", "red"],
        ["", "", ""],
        ["", "# the symptom is loud — the cause is elsewhere", "muted"],
    ]},
    {"kind": "title", "title": "Evidence, not guesswork", "tag": "evidence",
     "body": "Live cluster state   vs   expected GitOps + helm-rendered state\n\nevents · manifests · drift · policies · past fixes"},
    {"kind": "verdict", "title": "Ranked root cause", "tag": "verdict",
     "rows": [("spec.replicas", "3", "1", "critical"),
              ("resources.limits.memory", "512Mi", "128Mi", "warning")],
     "footer": "Most likely: memory limit under-provisioned vs declared intent → OOMKilled"},
    {"kind": "title", "title": "Proposed — not applied", "tag": "human gate",
     "body": "helm upgrade api --set resources.limits.memory=512Mi\n\nKubeVerdict stops before any change.\nA human approval gate is mandatory."},
]


def _dur(path: Path) -> float:
    return float(subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True).stdout.strip() or 0.0)


def _caption(scene_png: Path, text: str, out_png: Path) -> None:
    """Burn a subtitle bar + centered caption at the bottom of a scene frame."""
    img = Image.open(scene_png).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    font = ImageFont.truetype(r.FONT, 30)
    draw.rectangle((0, h - 96, w, h), fill="#05070d")  # covers the scene footer
    draw.rectangle((0, h - 96, w, h - 92), fill=r.ACCENT)
    lines = r.wrap_lines(draw, text, font, w - 160)
    y = (h - 96) + (96 - len(lines) * 40) // 2
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        x = (w - tw) // 2
        draw.text((x + 1, y + 1), ln, font=font, fill="#000000")  # shadow
        draw.text((x, y), ln, font=font, fill="#f8fafc")
        y += 40
    img.save(out_png)


def main() -> None:
    script = json.loads(SCRIPT.read_text())
    voice, rate = script["voice"], str(script["rate"])
    lines = script["lines"]
    ASSETS.mkdir(parents=True, exist_ok=True)

    # 1. synthesize each line
    audio_parts: list[Path] = []
    durs: list[float] = []
    for i, ln in enumerate(lines):
        aiff = ASSETS / f"line_{i:02d}.aiff"
        subprocess.run(["say", "-v", voice, "-r", rate, "-o", str(aiff), ln["text"]], check=True)
        d = _dur(aiff)
        audio_parts.append(aiff)
        durs.append(d)
        print(f"[{i + 1}/{len(lines)}] scene {ln['scene']}  ({d:.1f}s)  {ln['text'][:46]}…")

    # 2. concat audio → mp3
    acat = ASSETS / "audio.txt"
    acat.write_text("\n".join(f"file '{p.as_posix()}'" for p in audio_parts) + "\n")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(acat),
                    "-c:a", "libmp3lame", "-b:a", "192k", str(MP3)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 3. render the 4 scene backgrounds (patch SCENES so the footer count matches)
    r.SCENES = SHORT_SCENES
    scene_png = [r.render_scene(i, s) for i, s in enumerate(SHORT_SCENES)]

    # 4. one caption frame per line (scene visual + that line's subtitle)
    vcat_lines: list[str] = []
    last = None
    for i, ln in enumerate(lines):
        frame = ASSETS / f"frame_{i:02d}.png"
        _caption(scene_png[ln["scene"]], ln["text"], frame)
        last = frame
        vcat_lines.append(f"file '{frame.as_posix()}'")
        vcat_lines.append(f"duration {round(durs[i], 2)}")
    vcat_lines.append(f"file '{last.as_posix()}'")
    vcat = ASSETS / "frames.txt"
    vcat.write_text("\n".join(vcat_lines) + "\n")

    # 5. mux (captions already burned in — no subtitle filter needed)
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(vcat),
                    "-i", str(MP3), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(MP4)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.copy(MP4, SHARE)
    print(f"\n✅ {MP4.relative_to(ROOT)}  duration={_dur(MP4):.1f}s  → {SHARE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
