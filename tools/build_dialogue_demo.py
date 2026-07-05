"""Build the dialogue variant of the demo video (skeptic ↔ expert).

Reads docs/demo-voiceover-dialogue-segments.json, synthesizes each line with its
speaker's macOS `say` voice, concatenates to docs/demo-dialogue.mp3, then reuses
the render_demo_video scenes — timed to the dialogue audio (each scene lasts as
long as the lines mapped to it) — and muxes docs/demo-dialogue.mp4.

Usage:  python tools/build_dialogue_demo.py
No API key needed (macOS `say`). Outputs are git-ignored build artifacts.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import tools.render_demo_video as r

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SCRIPT = DOCS / "demo-voiceover-dialogue-segments.json"
ASSETS = DOCS / "demo-video-assets" / "dialogue"
MP3 = DOCS / "demo-dialogue.mp3"
MP4 = DOCS / "demo-dialogue.mp4"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"


def _dur(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out or 0.0)


def main() -> None:
    script = json.loads(SCRIPT.read_text())
    voices = script["voices"]
    rate = str(script.get("rate", 178))
    segments = script["segments"]
    ASSETS.mkdir(parents=True, exist_ok=True)

    # 1. synthesize each line; accumulate per-scene duration
    parts: list[Path] = []
    scene_dur: dict[int, float] = {}
    for i, seg in enumerate(segments):
        say_voice = voices[seg["voice"]]["say"]
        aiff = ASSETS / f"line_{i:02d}_{seg['voice']}.aiff"
        subprocess.run(["say", "-v", say_voice, "-r", rate, "-o", str(aiff), seg["text"]], check=True)
        parts.append(aiff)
        scene_dur[seg["scene"]] = scene_dur.get(seg["scene"], 0.0) + _dur(aiff)
        print(f"[{i + 1:02d}/{len(segments)}] {seg['voice']:8}({say_voice}) → scene {seg['scene']}: {seg['text'][:48]}…")

    # 2. concat → mp3
    concat = ASSETS / "concat.txt"
    concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts) + "\n")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-c:a", "libmp3lame", "-b:a", "192k", str(MP3)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 3. render scenes, timed to each scene's dialogue duration
    slide_concat = r.OUT_DIR / "dialogue_slides.txt"
    lines: list[str] = []
    last = None
    for i, scene in enumerate(r.SCENES):
        p = r.render_scene(i, scene)
        last = p
        lines.append(f"file '{p.as_posix()}'")
        lines.append(f"duration {round(scene_dur.get(i, 4.0), 2)}")
    lines.append(f"file '{last.as_posix()}'")
    slide_concat.write_text("\n".join(lines) + "\n")

    # 4. mux
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(slide_concat),
                    "-i", str(MP3), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(MP4)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"\n✅ {MP4.relative_to(ROOT)}  duration={_dur(MP4):.1f}s  size={MP4.stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()
