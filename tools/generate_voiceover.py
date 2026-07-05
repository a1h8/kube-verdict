"""Render the KubeVerdict demo voiceover with ElevenLabs (multi-voice).

Reads ``docs/demo-voiceover-segments.json`` — a list of segments, each naming a
voice from the ``voices`` map — synthesizes every segment with its own ElevenLabs
voice (so the anchor-by-render intro can use a different voice than the rest),
and concatenates them into ``docs/demo-voiceover-90s.mp3`` (the canonical track
consumed by ``render_demo_video.py`` and ``finalize_demo.py``).

Usage:
    export ELEVENLABS_API_KEY=sk-...
    python tools/generate_voiceover.py            # synthesize + concat
    python tools/generate_voiceover.py --dry-run  # no API call; just regenerate the TTS text

Voice IDs in the JSON are ElevenLabs classic pre-made voices (free tier); swap
them for your own. No credits are spent in --dry-run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SEGMENTS = DOCS / "demo-voiceover-segments.json"
ASSETS = DOCS / "demo-video-assets" / "voiceover"
OUT_MP3 = DOCS / "demo-voiceover-90s.mp3"
TTS_TXT = DOCS / "demo-voiceover-90s-tts.txt"

API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
FFMPEG = "/opt/homebrew/bin/ffmpeg"


def load_script() -> dict:
    return json.loads(SEGMENTS.read_text())


def write_tts_text(segments: list[dict]) -> None:
    """Regenerate the flat TTS text (one sentence per line) that finalize_demo.py times against."""
    lines = [seg["text"].strip() for seg in segments]
    TTS_TXT.write_text("\n\n".join(lines) + "\n")
    print(f"wrote {TTS_TXT.relative_to(ROOT)} ({len(lines)} segments)")


def synthesize_segment(text: str, voice_id: str, model_id: str, settings: dict,
                       api_key: str, dest: Path) -> None:
    import requests

    resp = requests.post(
        API_URL.format(voice_id=voice_id),
        headers={"xi-api-key": api_key, "Content-Type": "application/json",
                 "Accept": "audio/mpeg"},
        json={"text": text, "model_id": model_id, "voice_settings": settings},
        timeout=120,
    )
    if resp.status_code != 200:
        raise SystemExit(f"ElevenLabs error {resp.status_code} for voice {voice_id}: "
                         f"{resp.text[:300]}")
    dest.write_bytes(resp.content)


def concat_mp3s(parts: list[Path], out: Path) -> None:
    concat_file = ASSETS / "concat.txt"
    concat_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts) + "\n")
    subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-c:a", "libmp3lame", "-b:a", "192k", str(out)],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Regenerate the TTS text only; do not call ElevenLabs.")
    args = parser.parse_args()

    script = load_script()
    voices = script["voices"]
    model_id = script.get("model_id", "eleven_multilingual_v2")
    segments = script["segments"]

    write_tts_text(segments)

    if args.dry_run:
        print("dry-run: skipping ElevenLabs synthesis. Voices used:")
        for key, v in voices.items():
            print(f"  {key:8} → {v['name']} ({v['voice_id']})")
        print(f"{len(segments)} segments ready. Set ELEVENLABS_API_KEY and rerun without --dry-run.")
        return

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        sys.exit("ELEVENLABS_API_KEY is not set. `export ELEVENLABS_API_KEY=...` or use --dry-run.")

    ASSETS.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, seg in enumerate(segments):
        voice = voices[seg["voice"]]
        dest = ASSETS / f"seg_{i:02d}_{seg['voice']}.mp3"
        print(f"[{i + 1:02d}/{len(segments)}] {seg['voice']:6} ({voice['name']}): "
              f"{seg['text'][:60]}…")
        synthesize_segment(seg["text"], voice["voice_id"], model_id,
                           voice.get("settings", {}), api_key, dest)
        parts.append(dest)

    concat_mp3s(parts, OUT_MP3)
    print(f"\n✅ wrote {OUT_MP3.relative_to(ROOT)} from {len(parts)} segments.")
    print("Next: python tools/render_demo_video.py")


if __name__ == "__main__":
    main()
