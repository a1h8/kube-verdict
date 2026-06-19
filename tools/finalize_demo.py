"""Align the demo film to the actual voiceover and burn in matching subtitles.

The voiceover MP3 (docs/demo-voiceover-90s.mp3) is the canonical track. This
script:
  1. times each spoken sentence (from the TTS source) across the real voice
     duration, proportional to length → an aligned subtitle timeline + SRT;
  2. sizes each UI scene to the sum of its sentences' durations, so the visuals
     track what is being said;
  3. renders the whole film as a PIL frame sequence (UI screenshot + the active
     subtitle baked in — this ffmpeg has no libass/drawtext), then muxes the
     voice.

Output: recordings/demo-final.mp4 (voice + burned subtitles) + demo-final.srt
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "recordings"
FRAMES = REC / "frames"
RENDER = REC / "render"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
TTS = ROOT / "docs" / "demo-voiceover-90s-tts.txt"
VOICE = ROOT / "docs" / "demo-voiceover-90s.mp3"
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
WIDTH, HEIGHT, FPS = 1440, 900, 12

# Spoken sentence index ranges (0-based, end-exclusive) per scene, in order.
CLIP_SENTENCES = {
    "01-live-intro": (0, 6),
    "02-dead-end":   (6, 12),
    "03-backtrack":  (12, 17),
    "04-thresholds": (17, 26),
    "05-human-gate": (26, 37),
}

# Top-banner caption per scene (labels the decision beat on screen).
CLIP_OVERLAY = {
    "01-live-intro": "",
    "02-dead-end":   "Strict threshold rejects low-confidence path",
    "03-backtrack":  "KubeVerdict backtracks",
    "04-thresholds": "Lenient threshold finds valid remediation",
    "05-human-gate": "Human gate stops execution",
}

OUT_NAME = "demo-decision-thresholds"  # deep-dive artifact name


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def srt_ts(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = w if not cur else f"{cur} {w}"
        if draw.textbbox((0, 0), cand, font=font)[2] <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_screenshot(path: Path) -> Image.Image:
    """Letterbox a screenshot onto a WIDTH×HEIGHT black canvas."""
    img = Image.open(path).convert("RGB")
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (11, 18, 32))
    scale = min(WIDTH / img.width, HEIGHT / img.height)
    nw, nh = int(img.width * scale), int(img.height * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas.paste(img, ((WIDTH - nw) // 2, (HEIGHT - nh) // 2))
    return canvas


def draw_banner(canvas: Image.Image, text: str, font) -> None:
    """Top accent banner labelling the current decision beat."""
    if not text:
        return
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rectangle((0, 0, WIDTH, 52), fill=(37, 99, 235, 235))
    w = draw.textbbox((0, 0), text, font=font)[2]
    draw.text(((WIDTH - w) // 2, 12), text, font=font, fill=(255, 255, 255, 255))


def draw_subtitle(canvas: Image.Image, text: str, font) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    lines = wrap(draw, text, font, WIDTH - 240)
    lh = font.size + 12
    block_h = lh * len(lines)
    pad = 18
    box_top = HEIGHT - 56 - block_h
    # widest line → centered box
    widths = [draw.textbbox((0, 0), ln, font=font)[2] for ln in lines]
    box_w = max(widths) + pad * 2
    box_left = (WIDTH - box_w) // 2
    draw.rounded_rectangle(
        (box_left, box_top - pad, box_left + box_w, box_top + block_h + pad - 6),
        radius=14, fill=(0, 0, 0, 170),
    )
    y = box_top
    for ln, w in zip(lines, widths):
        draw.text(((WIDTH - w) // 2, y), ln, font=font, fill=(248, 250, 252, 255))
        y += lh


def main() -> None:
    voice_dur = probe_duration(VOICE)
    sentences = [ln.strip() for ln in TTS.read_text().splitlines() if ln.strip()]
    print(f"voice = {voice_dur:.2f}s, sentences = {len(sentences)}")

    weights = [max(len(s), 14) for s in sentences]
    total_w = sum(weights)
    durs = [w / total_w * voice_dur for w in weights]
    starts = [sum(durs[:i]) for i in range(len(durs))]

    # ── aligned SRT (matches the spoken words) ──────────────────────────────
    srt = []
    for i, (sent, st, d) in enumerate(zip(sentences, starts, durs), start=1):
        srt.append(f"{i}\n{srt_ts(st)} --> {srt_ts(st + d)}\n{sent}\n")
    (REC / f"{OUT_NAME}.srt").write_text("\n".join(srt))

    # ── scene (clip) timeline ───────────────────────────────────────────────
    scenes = []  # (clip, scene_start, scene_dur, [frame paths])
    cursor = 0.0
    for clip, (a, b) in CLIP_SENTENCES.items():
        dur = sum(durs[a:b])
        frames = sorted((FRAMES / clip).glob("*.png"))
        scenes.append((clip, cursor, dur, frames))
        cursor += dur
    film_dur = cursor

    def scene_at(t: float):
        for clip, s0, dur, frames in scenes:
            if t < s0 + dur or clip == scenes[-1][0]:
                if t >= s0 and frames:
                    idx = min(int((t - s0) / (dur / len(frames))), len(frames) - 1)
                    return clip, frames[idx]
        last = scenes[-1]
        return last[0], last[3][-1]

    def subtitle_at(t: float) -> str:
        for sent, st, d in zip(sentences, starts, durs):
            if st <= t < st + d:
                return sent
        return ""

    # ── render frames ───────────────────────────────────────────────────────
    if RENDER.exists():
        shutil.rmtree(RENDER)
    RENDER.mkdir(parents=True)
    bold = FONT_BOLD if Path(FONT_BOLD).exists() else FONT
    font = ImageFont.truetype(bold, 30)
    banner_font = ImageFont.truetype(bold, 24)
    cache: dict[Path, Image.Image] = {}
    n_out = int(film_dur * FPS) + 1
    for k in range(n_out):
        t = k / FPS
        clip, shot = scene_at(t)
        if shot not in cache:
            cache[shot] = fit_screenshot(shot)
        frame = cache[shot].copy()
        draw_banner(frame, CLIP_OVERLAY.get(clip, ""), banner_font)
        sub = subtitle_at(t)
        if sub:
            draw_subtitle(frame, sub, font)
        frame.save(RENDER / f"f{k:05d}.png")
    print(f"rendered {n_out} frames @ {FPS}fps ({film_dur:.1f}s)")

    # ── encode frames + voice (faststart + compat profile) ──────────────────
    final = REC / f"{OUT_NAME}.mp4"
    subprocess.run(
        [FFMPEG, "-y", "-framerate", str(FPS), "-i", str(RENDER / "f%05d.png"),
         "-i", str(VOICE),
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "libx264", "-profile:v", "high", "-level", "4.0",
         "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
         "-shortest", str(final)],
        check=True, capture_output=True,
    )
    shutil.rmtree(RENDER)
    vd = probe_duration(final)
    print(f"\nDONE → {final}  ({vd:.1f}s, voice + burned subtitles in sync)")


if __name__ == "__main__":
    main()
