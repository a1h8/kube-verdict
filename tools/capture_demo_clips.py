"""Drive the live KubeVerdict Streamlit UI via Firefox WebDriver and capture
the five demo scenarios as screenshot-sequence .mov clips.

Prerequisites (checked by the caller, see scripts):
  * Streamlit UI running on http://localhost:8501
  * geckodriver running on http://127.0.0.1:4444
  * Ollama up with the configured model pulled (llm_ok == True)
  * ffmpeg in PATH

Output: recordings/01-live-intro.mov ... 05-human-gate.mov
Each clip is encoded to a fixed target duration so the concatenated film
lines up with the 110s voiceover + subtitles.
"""
from __future__ import annotations

import base64
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.firefox_webdriver import create_session, FirefoxSession  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RECORDINGS = ROOT / "recordings"
FRAMES = RECORDINGS / "frames"
APP_URL = "http://localhost:8501"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
WIDTH, HEIGHT = 1440, 900

# clip name -> target duration in seconds (matched to the srt narration segments)
CLIP_TARGET = {
    "01-live-intro": 16.0,
    "02-dead-end": 17.0,
    "03-backtrack": 12.0,
    "04-thresholds": 26.0,
    "05-human-gate": 39.0,
}


# ── low-level driver helpers ───────────────────────────────────────────────
def click_tab(s: FirefoxSession, label: str) -> bool:
    return bool(s.execute(
        """
        const want = arguments[0];
        const t = Array.from(document.querySelectorAll('button[role="tab"]'))
            .find(e => e.innerText.trim().includes(want));
        if (!t) return false;
        t.scrollIntoView({block:'center'}); t.click(); return true;
        """, [label]))


def click_btn(s: FirefoxSession, text: str, exact: bool = False) -> bool:
    return bool(s.execute(
        """
        const want = arguments[0], exact = arguments[1];
        const els = Array.from(document.querySelectorAll('button, [role="button"]'));
        const m = els.find(e => {
            const t = (e.innerText||'').trim();
            return exact ? t === want : t.includes(want);
        });
        if (!m) return false;
        m.scrollIntoView({block:'center'}); m.click(); return true;
        """, [text, exact]))


def click_radio_option(s: FirefoxSession, text: str) -> bool:
    """Click a Streamlit radio option whose visible label contains `text`."""
    return bool(s.execute(
        """
        const want = arguments[0];
        const labels = Array.from(document.querySelectorAll('label'));
        const m = labels.find(l => (l.innerText||'').includes(want));
        if (!m) return false;
        m.scrollIntoView({block:'center'});
        const input = m.querySelector('input');
        (input || m).click();
        return true;
        """, [text]))


def has_text(s: FirefoxSession, text: str) -> bool:
    return bool(s.execute(
        "return (document.body.innerText||'').includes(arguments[0]);", [text]))


def wait_text(s: FirefoxSession, text: str, timeout: float = 90.0, poll: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if has_text(s, text):
            return True
        time.sleep(poll)
    return False


def wait_marker(s: FirefoxSession, markers, timeout: float = 240.0, poll: float = 1.5) -> str | None:
    """Wait until the page body contains any of the marker strings; return the hit."""
    if isinstance(markers, str):
        markers = [markers]
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = s.execute("return document.body.innerText || '';") or ""
        for m in markers:
            if m in body:
                return m
        time.sleep(poll)
    return None


def wait_turn_complete(s: FirefoxSession, timeout: float = 240.0, poll: float = 1.5) -> str | None:
    """Wait for an LLM turn to finish. Ignores the static 'dead end' help text;
    only returns on a real terminal outcome with no spinner running.
    Returns 'dead_end' | 'resolved' | 'pending' | None(timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = s.execute("return document.body.innerText || '';") or ""
        running = ("Running turn" in body) or ("Running initial analysis" in body)
        if not running:
            if "confidence_stagnant" in body or "confidence_regressed" in body:
                return "dead_end"
            if "Resolution found" in body:
                return "resolved"
            if "Max turns" in body and "reached" in body:
                return "pending"
        time.sleep(poll)
    return None


def main_panel_len(s: FirefoxSession) -> int:
    return int(s.execute(
        """
        const m = document.querySelector('section.main, [data-testid="stMain"], .main');
        return (m ? m.innerText.length : (document.body.innerText||'').length);
        """) or 0)


def scroll_to(s: FirefoxSession, text: str) -> None:
    s.execute(
        """
        const want = arguments[0];
        const all = Array.from(document.querySelectorAll('h1,h2,h3,h4,p,div,button,span'));
        const el = all.find(e => (e.innerText||'').trim().includes(want));
        if (el) el.scrollIntoView({block:'center'});
        """, [text])


def scroll_top(s: FirefoxSession) -> None:
    s.execute("window.scrollTo(0,0);")


class Recorder:
    def __init__(self, s: FirefoxSession, clip: str):
        self.s = s
        self.clip = clip
        self.dir = FRAMES / clip
        self.dir.mkdir(parents=True, exist_ok=True)
        for old in self.dir.glob("*.png"):
            old.unlink()
        self.n = 0

    def frame(self, settle: float = 0.6) -> None:
        time.sleep(settle)
        png = base64.b64decode(self.s.screenshot())
        (self.dir / f"{self.n:03d}.png").write_bytes(png)
        self.n += 1
        print(f"   · {self.clip} frame {self.n} ({len(png)} bytes)")


# ── scenario scripts ───────────────────────────────────────────────────────
DONE_MARKERS = ["dead end", "Dead end", "Resolution found", "Choose a proposal"]


def clip_01(s: FirefoxSession) -> None:
    print("▶ clip 01 live-intro")
    rec = Recorder(s, "01-live-intro")
    click_tab(s, "Root Cause Analysis")
    time.sleep(1.5)
    scroll_top(s)
    rec.frame(settle=1.0)
    rec.frame(settle=0.4)
    scroll_to(s, "collectors")
    rec.frame(settle=0.8)
    scroll_top(s)
    rec.frame(settle=0.6)


def _goto_demo(s: FirefoxSession) -> None:
    """Fresh page load so each scenario starts from clean session state."""
    s.navigate(APP_URL)
    time.sleep(8)
    click_tab(s, "Demo")
    time.sleep(2.0)


def clip_02(s: FirefoxSession) -> None:
    print("▶ clip 02 dead-end (h009 / Strict → stagnant dead end)")
    rec = Recorder(s, "02-dead-end")
    _goto_demo(s)
    click_btn(s, "Act 1 — Dead End")
    wait_text(s, "h009_liveness_probe_loop", timeout=30)
    time.sleep(2.0)
    scroll_top(s)
    rec.frame(settle=1.0)               # configured: case + Strict + storyboard
    if not click_btn(s, "Run simulation"):
        print("   ! Run simulation not found"); rec.frame(); return
    wait_marker(s, "Choose a proposal", timeout=180)
    time.sleep(1.0)
    scroll_to(s, "Turn 0")
    rec.frame(settle=0.8)               # Root turn + proposal chooser
    scroll_to(s, "Choose a proposal")
    rec.frame(settle=0.6)
    # advance one turn → strict classifies it as a dead end
    if click_btn(s, "Continue with"):
        outcome = wait_turn_complete(s, timeout=200)
        print(f"   · turn 1 outcome = {outcome}")
        time.sleep(1.5)
        scroll_to(s, "unexplored proposals" if outcome == "dead_end" else "Turn 1")
        rec.frame(settle=1.0)           # dead-end panel
        rec.frame(settle=0.6)
    else:
        print("   ! Continue button not found"); rec.frame()


def clip_03(s: FirefoxSession) -> None:
    print("▶ clip 03 backtrack")
    rec = Recorder(s, "03-backtrack")
    # continues from clip 02 dead-end state
    if not wait_marker(s, ["Dead end", "Backtrack"], timeout=10):
        print("   ! no dead-end state to backtrack from")
    scroll_to(s, "unexplored proposals")
    rec.frame(settle=0.8)               # dead-end + backtrack targets
    clicked = click_btn(s, "↩ Turn") or click_btn(s, "Backtrack to here")
    if clicked:
        time.sleep(2.5)
        scroll_to(s, "Choose a proposal")
        rec.frame(settle=1.0)           # back at the decision point
        scroll_to(s, "Path")
        rec.frame(settle=0.6)
    else:
        print("   ! backtrack button not found")
        rec.frame(); rec.frame()


def clip_04(s: FirefoxSession) -> None:
    print("▶ clip 04 thresholds (Auto BFS → compare strict vs lenient)")
    rec = Recorder(s, "04-thresholds")
    # Act 3 bootstraps the same h009 case in Auto (full BFS) mode
    _goto_demo(s)
    click_btn(s, "Act 3 — Strict vs Lenient")
    if not wait_marker(s, "Compare strict vs lenient", timeout=30):
        print("   ! Auto mode / Compare button did not render")
        rec.frame(); return
    time.sleep(1.0)
    scroll_to(s, "Compare strict vs lenient")
    rec.frame(settle=0.8)
    if click_btn(s, "Compare strict vs lenient"):
        time.sleep(3.0)                 # spinner appears
        # wait for the actual result subheader (only rendered once both BFS finish)
        done = wait_marker(s, "Threshold comparison — same case", timeout=480)
        print(f"   · compare done = {bool(done)}")
        time.sleep(1.5)
        scroll_to(s, "Threshold comparison — same case")
        rec.frame(settle=1.0)           # comparison metrics (strict vs lenient)
        rec.frame(settle=0.6)
        scroll_to(s, "Dead ends")
        rec.frame(settle=0.8)
    else:
        print("   ! Compare button click failed"); rec.frame()


def clip_05(s: FirefoxSession) -> None:
    print("▶ clip 05 human-gate (h006 → resolved → operator decision)")
    rec = Recorder(s, "05-human-gate")
    _goto_demo(s)
    click_btn(s, "Act 2 — Human Gate")
    wait_text(s, "h006_networkpolicy_blocked", timeout=30)
    time.sleep(2.0)
    scroll_top(s)
    rec.frame(settle=1.0)               # h006 loaded
    if not click_btn(s, "Run simulation"):
        print("   ! Run simulation not found"); rec.frame(); return
    wait_marker(s, "Choose a proposal", timeout=180)
    time.sleep(1.0)
    # advance one turn; h006 resolves under Lenient → operator gate appears.
    # Wait directly on the gate marker (robust to slow turns / detection glitches).
    for attempt in range(3):
        if has_text(s, "Operator decision"):
            break
        if not click_btn(s, "Continue with"):
            break
        if wait_marker(s, "Operator decision", timeout=220):
            break
        print(f"   · h006 attempt {attempt+1}: no gate yet")
        time.sleep(2.0)
    time.sleep(1.0)
    print(f"   · h006 operator_gate={has_text(s, 'Operator decision')}")
    scroll_to(s, "Resolution found")
    rec.frame(settle=1.0)               # resolution banner
    scroll_to(s, "Operator decision")
    rec.frame(settle=1.0)               # approve / reject gate
    rec.frame(settle=0.6)


# ── assembly ───────────────────────────────────────────────────────────────
def assemble(clip: str) -> None:
    frames = sorted((FRAMES / clip).glob("*.png"))
    if not frames:
        print(f"   ! no frames for {clip}; skipping")
        return
    target = CLIP_TARGET[clip]
    per = max(target / len(frames), 1.2)
    concat = FRAMES / clip / "frames.txt"
    lines = []
    for f in frames:
        lines.append(f"file '{f.as_posix()}'")
        lines.append(f"duration {per:.3f}")
    lines.append(f"file '{frames[-1].as_posix()}'")  # concat demuxer last-frame quirk
    concat.write_text("\n".join(lines) + "\n")
    out = RECORDINGS / f"{clip}.mov"
    cmd = [
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
               f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"   ✓ {out.name}  ({len(frames)} frames × {per:.1f}s ≈ {target:.0f}s)")


def main() -> None:
    RECORDINGS.mkdir(exist_ok=True)
    s = create_session()
    try:
        s.set_window_rect(40, 40, WIDTH, HEIGHT)
        s.navigate(APP_URL)
        time.sleep(8)
        clip_01(s)
        clip_02(s)
        clip_03(s)
        clip_04(s)
        clip_05(s)
    finally:
        s.delete()
    print("\nAssembling clips…")
    for clip in CLIP_TARGET:
        assemble(clip)
    print("\nDone. Run: ./scripts/build_demo_movie.sh", RECORDINGS)


if __name__ == "__main__":
    main()