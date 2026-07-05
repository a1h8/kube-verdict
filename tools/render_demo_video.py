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

MONO = "/System/Library/Fonts/Menlo.ttc"
TERM_BG = "#0d1117"
TERM_GREEN = "#3fb950"
TERM_RED = "#f85149"
TERM_YELLOW = "#d29922"
TERM_FG = "#c9d1d9"

# Each scene: kind = "title" | "terminal" | "verdict".
#   title    → headline + body (also the anchor-by-render intro, voice change in the VO)
#   terminal → BEFORE: a stylized shell showing the live kubectl symptom
#   verdict  → AFTER: KubeVerdict's render-vs-live drift (declared vs observed)
SCENES = [
    {"kind": "title", "title": "Anchor-by-render",
     "body": "Most Kubernetes tools start from live symptoms.\nKubeVerdict starts one step earlier.",
     "duration": 8, "tag": "① expected state"},
    {"kind": "title", "title": "Render the expected state",
     "body": "Helm / GitOps sources\n   ↓  helm template\nRendered expected manifests\n   ↓  compared with the live cluster\nDeclared-vs-observed drift → evidence",
     "duration": 9, "tag": "② anchor-by-render"},
    {"kind": "terminal", "title": "BEFORE — the live symptom",
     "duration": 11, "tag": "kubectl",
     "lines": [
         ["$ ", "kubectl get pods -n production", ""],
         ["", "NAME                    READY   STATUS             RESTARTS", "hdr"],
         ["", "api-6d8f9b7c4-xvk2p     0/1     CrashLoopBackOff   7", "red"],
         ["", "", ""],
         ["$ ", "kubectl describe pod api-6d8f9b7c4-xvk2p", ""],
         ["", "  State:     Waiting  (CrashLoopBackOff)", "yellow"],
         ["", "  Last State: Terminated  Reason: OOMKilled", "red"],
         ["", "", ""],
         ["", "# loud symptom — but the cause is elsewhere", "muted"],
     ]},
    {"kind": "verdict", "title": "AFTER — KubeVerdict render-vs-live",
     "duration": 12, "tag": "verdict",
     "rows": [
         ("spec.replicas", "3", "1", "critical"),
         ("resources.limits.memory", "512Mi", "128Mi", "warning"),
     ],
     "footer": "Root cause: memory limit under-provisioned vs declared intent → OOMKilled"},
    {"kind": "title", "title": "Strict thresholds",
     "body": "The system follows a plausible branch.\nIt scores the path.\nBut confidence does not improve enough.",
     "duration": 12, "tag": "decision"},
    {"kind": "title", "title": "Dead end",
     "body": "This branch becomes a dead end.\nThe failure stays visible.\nWe can inspect it and backtrack.",
     "duration": 12, "tag": "decision"},
    {"kind": "title", "title": "Same case, lenient policy",
     "body": "Same signals, different routing.\nThis time the system converges more cleanly\ntoward the useful path.",
     "duration": 12, "tag": "decision"},
    {"kind": "title", "title": "Human decision gate",
     "body": "On a networking incident it reaches a valid remediation —\nbut it does not act automatically.\nThe operator approves or rejects.",
     "duration": 13, "tag": "human gate"},
    {"kind": "title", "title": "Explore · compare · justify",
     "body": "The reasoning is explicit, reviewable, and tunable.\nThe final operational choice stays with you.",
     "duration": 8, "tag": "close"},
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


def _footer(draw: ImageDraw.ImageDraw, index: int, tag: str, small_font: ImageFont.FreeTypeFont) -> None:
    draw.text((120, HEIGHT - 120), f"KubeVerdict  ·  {tag}", font=small_font, fill=ACCENT)
    draw.text((WIDTH - 220, HEIGHT - 120), f"{index + 1:02d}/{len(SCENES):02d}", font=small_font, fill=SUB)


def _new_image(bg: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (WIDTH, HEIGHT), bg)
    return image, ImageDraw.Draw(image)


def render_title_slide(index: int, scene: dict) -> Path:
    image, draw = _new_image(BG)
    title_font = ImageFont.truetype(FONT, 46)
    body_font = ImageFont.truetype(FONT, 28)
    small_font = ImageFont.truetype(FONT, 20)

    draw.rounded_rectangle((84, 84, WIDTH - 84, HEIGHT - 84), radius=28, outline="#1e293b", width=3)
    draw.text((120, 126), scene["title"], font=title_font, fill=FG)
    draw.line((120, 190, WIDTH - 120, 190), fill=ACCENT, width=4)

    y = 245
    for line in wrap_lines(draw, scene["body"], body_font, WIDTH - 240):
        draw.text((120, y), line, font=body_font, fill=SUB)
        y += 46

    _footer(draw, index, scene.get("tag", "demo"), small_font)
    path = OUT_DIR / f"slide_{index:02d}.png"
    image.save(path)
    return path


def render_terminal_slide(index: int, scene: dict) -> Path:
    """BEFORE scene: a stylized shell showing the live kubectl symptom."""
    image, draw = _new_image(BG)
    title_font = ImageFont.truetype(FONT, 40)
    mono = ImageFont.truetype(MONO, 24)
    small_font = ImageFont.truetype(FONT, 20)
    colors = {"": TERM_FG, "hdr": SUB, "red": TERM_RED, "yellow": TERM_YELLOW,
              "muted": "#6e7681", "green": TERM_GREEN}

    draw.text((120, 110), scene["title"], font=title_font, fill=FG)
    # terminal window
    draw.rounded_rectangle((120, 180, WIDTH - 120, HEIGHT - 150), radius=14, fill=TERM_BG,
                           outline="#30363d", width=2)
    for i, dot in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        draw.ellipse((150 + i * 26, 200, 166 + i * 26, 216), fill=dot)

    y = 240
    for prompt, text, style in scene["lines"]:
        x = 156
        if prompt:
            draw.text((x, y), prompt, font=mono, fill=TERM_GREEN)
            x += draw.textlength(prompt, font=mono)
        draw.text((x, y), text, font=mono, fill=colors.get(style, TERM_FG))
        y += 36

    _footer(draw, index, scene.get("tag", "kubectl"), small_font)
    path = OUT_DIR / f"slide_{index:02d}.png"
    image.save(path)
    return path


def render_verdict_slide(index: int, scene: dict) -> Path:
    """AFTER scene: KubeVerdict render-vs-live drift (declared vs observed)."""
    image, draw = _new_image(BG)
    title_font = ImageFont.truetype(FONT, 40)
    head_font = ImageFont.truetype(FONT, 24)
    row_font = ImageFont.truetype(MONO, 26)
    small_font = ImageFont.truetype(FONT, 20)
    sev_color = {"critical": TERM_RED, "warning": TERM_YELLOW, "info": ACCENT}

    draw.text((120, 110), scene["title"], font=title_font, fill=FG)
    draw.line((120, 172, WIDTH - 120, 172), fill=ACCENT, width=3)

    cols = [140, 620, 860, 1080]
    headers = ["field", "declared", "observed", "severity"]
    for c, h in zip(cols, headers):
        draw.text((c, 210), h, font=head_font, fill=SUB)

    y = 262
    for field, declared, observed, sev in scene["rows"]:
        draw.text((cols[0], y), field, font=row_font, fill=TERM_FG)
        draw.text((cols[1], y), declared, font=row_font, fill=TERM_GREEN)
        draw.text((cols[2], y), observed, font=row_font, fill=sev_color.get(sev, TERM_FG))
        draw.text((cols[3], y), f"● {sev}", font=row_font, fill=sev_color.get(sev, TERM_FG))
        y += 52

    y += 20
    for line in wrap_lines(draw, scene.get("footer", ""), head_font, WIDTH - 280):
        draw.text((140, y), line, font=head_font, fill=SUB)
        y += 34

    _footer(draw, index, scene.get("tag", "verdict"), small_font)
    path = OUT_DIR / f"slide_{index:02d}.png"
    image.save(path)
    return path


def render_scene(index: int, scene: dict) -> Path:
    kind = scene.get("kind", "title")
    if kind == "terminal":
        return render_terminal_slide(index, scene)
    if kind == "verdict":
        return render_verdict_slide(index, scene)
    return render_title_slide(index, scene)


def main() -> None:
    concat_path = OUT_DIR / "slides.txt"
    lines: list[str] = []
    last_path = None
    for index, scene in enumerate(SCENES):
        image_path = render_scene(index, scene)
        last_path = image_path
        lines.append(f"file '{image_path.as_posix()}'")
        lines.append(f"duration {scene['duration']}")
    lines.append(f"file '{last_path.as_posix()}'")
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
