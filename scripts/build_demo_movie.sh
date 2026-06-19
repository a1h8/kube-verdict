#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RECORDINGS_DIR="${1:-$ROOT_DIR/recordings}"
VOICEOVER="${2:-$ROOT_DIR/docs/demo-voiceover-90s.mp3}"
SUBTITLES="${3:-$ROOT_DIR/docs/demo-voiceover-90s.srt}"
CONCAT_FILE="$RECORDINGS_DIR/concat.txt"
RAW_VIDEO="$RECORDINGS_DIR/demo-raw.mp4"
FINAL_VIDEO="$RECORDINGS_DIR/demo-final.mp4"
FINAL_SUBTITLES="$RECORDINGS_DIR/demo-final.srt"

mkdir -p "$RECORDINGS_DIR"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found in PATH"
  exit 1
fi

if [ ! -f "$VOICEOVER" ]; then
  echo "Voiceover not found: $VOICEOVER"
  exit 1
fi

if [ ! -f "$CONCAT_FILE" ]; then
  cat >"$CONCAT_FILE" <<'EOF'
file '01-live-intro.mov'
file '02-dead-end.mov'
file '03-backtrack.mov'
file '04-thresholds.mov'
file '05-human-gate.mov'
EOF
  echo "Created template concat file at $CONCAT_FILE"
  echo "Edit it if your clip names differ, then rerun."
  exit 0
fi

echo "Building raw demo video from clips..."
ffmpeg -y \
  -f concat -safe 0 -i "$CONCAT_FILE" \
  -vf fps=30 -pix_fmt yuv420p \
  -c:v libx264 -c:a aac \
  "$RAW_VIDEO"

echo "Adding voiceover..."
ffmpeg -y \
  -i "$RAW_VIDEO" \
  -i "$VOICEOVER" \
  -map 0:v:0 -map 1:a:0 \
  -c:v copy -c:a aac -shortest \
  "$FINAL_VIDEO"

if [ -f "$SUBTITLES" ]; then
  cp "$SUBTITLES" "$FINAL_SUBTITLES"
  echo "Copied subtitles: $FINAL_SUBTITLES"
else
  echo "Subtitles not found, skipping copy: $SUBTITLES"
fi

echo
echo "Done:"
echo "  Raw video  : $RAW_VIDEO"
echo "  Final video: $FINAL_VIDEO"
echo "  Subtitles  : $FINAL_SUBTITLES"
