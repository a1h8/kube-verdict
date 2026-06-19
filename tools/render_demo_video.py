from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT_DIR = DOCS / "demo-video-assets"
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
WIDTH = 1280
HEIGHT = 720
BG = "#0b1220"
FG = "#f8fafc"
SUB = "#94a3b8"
ACCENT = "#60a5fa"

SCENES = [
    (
        "KubeVerdict",
        "Decision process, dead ends, threshold tuning,\nand human approval",
        6,
    ),
    (
        "Case 1: h009_liveness_probe_loop",
        "A real incident.\nThe service keeps restarting.\nThe liveness probe timing has drifted.",
        10,
    ),
    (
        "Strict thresholds",
        "The system follows a plausible branch.\nIt scores the path.\nBut confidence does not improve enough.",
        13,
    ),
    (
        "Dead end",
        "This branch becomes a dead end.\nThe failure stays visible.\nWe can inspect it and backtrack.",
        16,
    ),
    (
        "Same case, different policy",
        "Now we replay the same incident.\nSame signals.\nDifferent threshold profile.",
        16,
    ),
    (
        "Cleaner convergence",
        "With a more lenient routing policy,\nthe system converges more cleanly\ntoward the useful path.",
        15,
    ),
    (
        "Case 2: h006_networkpolicy_blocked",
        "A networking incident.\nThe diagnosis converges.\nA remediation path is proposed.",
        13,
    ),
    (
        "Human decision gate",
        "The system does not act automatically.\nThe operator reviews the recommendation\nand approves or rejects it.",
        16,
    ),
]


def wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        lines.append(current)
    return [line for line in lines if line]


def render_slide(index: int, title: str, body: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(FONT, 46)
    body_font = ImageFont.truetype(FONT, 28)
    small_font = ImageFont.truetype(FONT, 20)

    draw.rounded_rectangle((84, 84, WIDTH - 84, HEIGHT - 84), radius=28, outline="#1e293b", width=3)
    draw.text((120, 126), title, font=title_font, fill=FG)
    draw.line((120, 190, WIDTH - 120, 190), fill=ACCENT, width=4)

    y = 245
    for line in wrap_lines(draw, body, body_font, WIDTH - 240):
        draw.text((120, y), line, font=body_font, fill=SUB)
        y += 46

    draw.text((120, HEIGHT - 120), "KubeVerdict demo narration", font=small_font, fill=ACCENT)
    draw.text((WIDTH - 220, HEIGHT - 120), f"{index + 1:02d}/{len(SCENES):02d}", font=small_font, fill=SUB)

    path = OUT_DIR / f"slide_{index:02d}.png"
    image.save(path)
    return path


def main() -> None:
    concat_path = OUT_DIR / "slides.txt"
    lines: list[str] = []
    for index, (title, body, duration) in enumerate(SCENES):
        image_path = render_slide(index, title, body)
        lines.append(f"file '{image_path.as_posix()}'")
        lines.append(f"duration {duration}")
    lines.append(f"file '{render_slide(len(SCENES) - 1, SCENES[-1][0], SCENES[-1][1]).as_posix()}'")
    concat_path.write_text("\n".join(lines) + "\n")

    audio = DOCS / "demo-voiceover-90s.mp3"
    output = DOCS / "demo-voiceover-90s-slides.mp4"
    cmd = [
        "/opt/homebrew/bin/ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-i",
        str(audio),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "25",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(output),
    ]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
