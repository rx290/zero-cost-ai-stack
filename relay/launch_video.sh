#!/usr/bin/env bash
# Token-free launch video for any project folder (owner 2026-11-01): opencode runs the /brag skill on a free router
# model (fallback chain), Hyperframes renders locally with headless Chrome + ffmpeg, Kokoro (local) does the voice.
#   launch_video.sh <project_dir> [brag options, e.g. --tone polished --format vertical --voice]
#   MODELS="freellmapi/gemini-3-flash-preview ollama/qwen3:14b" launch_video.sh ...   (override the chain)
# Output: <project_dir>/brag-output*/brag.mp4, brag.jpg, share-copy.txt. Log: $LOGDIR (default ~/.local/state/launch_videos).
set -u
PROJ=$(realpath "${1:?usage: launch_video.sh <project_dir> [brag options]}"); shift
OPTS="$*"
MODELS=${MODELS:-"freellmapi/gemini-3-flash-preview freellmapi/minimax-m2.5 freellmapi/qwen3.8-27b freellmapi/gpt-oss-120b"}
LOGDIR=${LOGDIR:-$HOME/.local/state/launch_videos}; mkdir -p "$LOGDIR"
[ -n "${KOKORO_VENV:-}" ] && export PATH=$KOKORO_VENV/bin:$PATH   # local Kokoro TTS for the voice-over
cd "$PROJ" || exit 1
for m in $MODELS; do
  log="$LOGDIR/$(basename "$PROJ")_$(date +%m%d_%H%M)_${m//\//_}.log"
  echo "== $(basename "$PROJ") with $m -> $log"
  timeout 7200 opencode run -m "$m" \
    "Use the brag skill (its SKILL.md is at ~/.agents/skills/brag/SKILL.md; <skill-dir> is ~/.agents/skills/brag) to make a launch video for this project. Options: $OPTS. Work through every step and gate in SKILL.md without asking me anything, using npx hyperframes for check and render. Never put secrets, keys, tokens or private paths on screen. If a brag-output folder already has a plan or composition, continue from it instead of starting over. Finish only when brag.mp4, brag.jpg and share-copy.txt exist." \
    > "$log" 2>&1
  out=$(ls -td brag-output*/ 2>/dev/null | head -1)
  if [ -n "$out" ] && [ -s "$out/brag.mp4" ]; then
    echo "DONE $PROJ/$out""brag.mp4 (model $m)"; exit 0
  fi
  echo "no video from $m, trying the next model"; tail -3 "$log"
done
echo "FAILED: no model produced brag.mp4"; exit 1
