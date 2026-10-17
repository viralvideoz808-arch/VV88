#!/data/data/com.termux/files/usr/bin/bash
# VV88 on an Android phone with Termux: one-time setup (safe to run again).
#
#   cd ~/VV88 && bash scripts/termux_setup.sh
#
# Use Termux from F-Droid or GitHub; the Play Store build is outdated and can't install packages.
# The first run takes 20-40 minutes (pydantic and whisper.cpp are compiled on the phone): keep the phone
# charging. Options:  VV88_WHISPER_MODEL=tiny|base|small   VV88_SKIP_OPENCV=1
set -eo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
WHISPER_DIR="$HOME/whisper.cpp"
MODEL="${VV88_WHISPER_MODEL:-base}"   # tiny = fastest, base = good balance on phones, small = better but slow

step() { printf '\n\033[1;33m[%s] %s\033[0m\n' "$1" "$2"; }
warn() { printf '\033[1;31m! %s\033[0m\n' "$*"; }

if [ -z "$PREFIX" ] || ! command -v pkg >/dev/null 2>&1; then
  echo "This script is for Termux on Android. On a PC, follow README.md instead."
  exit 1
fi

# Keep Android from suspending Termux during the long compile/download steps.
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

step 1/6 "System packages: Python, FFmpeg and build tools"
pkg update -y
# Non-interactive upgrade: take the packaged version of any changed config file instead of asking.
apt-get -y -o Dpkg::Options::=--force-confnew full-upgrade
pkg install -y python python-numpy ffmpeg git clang rust make cmake pkg-config libffi openssl libyaml \
  binutils curl wget fontconfig

step 2/6 "OpenCV for face-tracked reframing (optional, large download)"
if [ -n "${VV88_SKIP_OPENCV:-}" ]; then
  echo "Skipped (VV88_SKIP_OPENCV is set): clips use a centre crop instead of face tracking."
elif python -c "import cv2" 2>/dev/null; then
  echo "OpenCV is already installed."
else
  # Termux ships OpenCV in its x11 repository.
  { pkg install -y x11-repo && pkg install -y opencv-python; } \
    || warn "Couldn't install OpenCV: clips use a centre crop instead of face tracking."
fi

step 3/6 "Python packages (first run compiles pydantic with Rust: 10-30 minutes)"
ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null || echo 24)"
export ANDROID_API_LEVEL
python -m pip install --upgrade setuptools wheel
python -m pip install -e .
# Plain uvicorn (not uvicorn[standard]): its compiled extras are slow to build on a phone and not needed.
python -m pip install "fastapi>=0.110" "uvicorn>=0.29" "jinja2>=3.1" "itsdangerous>=2.1" \
  "python-multipart>=0.0.9" "yt-dlp>=2024.1.0"

step 4/6 "whisper.cpp speech-to-text (captions) and the '$MODEL' model"
if [ ! -x "$WHISPER_DIR/build/bin/whisper-cli" ]; then
  [ -d "$WHISPER_DIR/.git" ] || git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER_DIR"
  cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release -DGGML_NO_OPENMP=ON \
    -DWHISPER_BUILD_TESTS=OFF
  cmake --build "$WHISPER_DIR/build" -j"$(nproc)" --target whisper-cli
fi
MODEL_FILE="$WHISPER_DIR/models/ggml-$MODEL.bin"
model_works() {
  "$WHISPER_DIR/build/bin/whisper-cli" -m "$MODEL_FILE" -f "$WHISPER_DIR/samples/jfk.wav" -np -t 2 \
    -d 2000 >/dev/null 2>&1
}
if [ -f "$MODEL_FILE" ] && ! model_works; then
  warn "The saved $MODEL model doesn't load (an interrupted download?): downloading it again."
  rm -f "$MODEL_FILE"
fi
if [ ! -f "$MODEL_FILE" ]; then
  # Download to a temporary folder and move it in only when complete, so a cut-off download never
  # leaves a broken model behind.
  TMP_DL="$(mktemp -d)"
  bash "$WHISPER_DIR/models/download-ggml-model.sh" "$MODEL" "$TMP_DL"
  mv "$TMP_DL/ggml-$MODEL.bin" "$MODEL_FILE"
  rm -rf "$TMP_DL"
  model_works || { rm -f "$MODEL_FILE"; warn "The $MODEL model doesn't load. Run this script again."; exit 1; }
fi
echo "Model ready: $MODEL_FILE"

step 5/6 "Config files"
if [ ! -f config.yaml ]; then
  # Phone defaults: the downloaded whisper model, and the bundled caption font (fonts/Inter-Black.ttf).
  sed -e "s/^  whisper_model: small /  whisper_model: $MODEL /" \
      -e "s#^  fonts_dir: null#  fonts_dir: $ROOT/fonts#" config.example.yaml > config.yaml
  echo "Created config.yaml"
else
  if [ -n "${VV88_WHISPER_MODEL:-}" ]; then
    sed -i -E "s/^(  whisper_model: )[^ #]+/\1$MODEL/" config.yaml
    echo "config.yaml now uses the $MODEL model (restart: bash scripts/termux_stop.sh; bash scripts/termux_start.sh)"
  fi
  echo "Keeping your other settings in config.yaml"
fi
[ -f campaigns.yaml ] || { cp campaigns.example.yaml campaigns.yaml; echo "Created campaigns.yaml"; }

step 6/6 "Health check"
vv88 doctor || true

printf '\n\033[1;32mDone.\033[0m Start the control website with:  bash scripts/termux_start.sh\n'
