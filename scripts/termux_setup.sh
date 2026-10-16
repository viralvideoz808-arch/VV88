#!/data/data/com.termux/files/usr/bin/bash
# VV88 on an Android phone with Termux: one-time setup (safe to run again).
#
#   cd ~/VV88 && bash scripts/termux_setup.sh
#
# Use Termux from F-Droid or GitHub; the Play Store build is outdated and can't install packages.
# The first run takes a while (pydantic and whisper.cpp are compiled on the phone): keep Termux open
# and the phone charging.
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

step 1/6 "System packages: Python, FFmpeg and build tools (answer Y if asked)"
pkg update -y
# Non-interactive upgrade: take the packaged version of any changed config file instead of asking.
apt-get -y -o Dpkg::Options::=--force-confnew full-upgrade
pkg install -y python python-numpy ffmpeg git clang rust make cmake pkg-config libffi openssl libyaml \
  binutils curl wget fontconfig

step 2/6 "OpenCV for face-tracked reframing (optional)"
pkg install -y opencv-python 2>/dev/null || pkg install -y python-opencv-python 2>/dev/null \
  || warn "OpenCV isn't available here: clips use a centre crop instead of face tracking."

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
[ -f "$WHISPER_DIR/models/ggml-$MODEL.bin" ] || bash "$WHISPER_DIR/models/download-ggml-model.sh" "$MODEL"

step 5/6 "Config files"
if [ ! -f config.yaml ]; then
  # Phone defaults: the downloaded whisper model, and the bundled caption font (fonts/Inter-Black.ttf).
  sed -e "s/^  whisper_model: small /  whisper_model: $MODEL /" \
      -e "s#^  fonts_dir: null#  fonts_dir: $ROOT/fonts#" config.example.yaml > config.yaml
  echo "Created config.yaml"
else
  echo "Keeping your existing config.yaml"
fi
[ -f campaigns.yaml ] || { cp campaigns.example.yaml campaigns.yaml; echo "Created campaigns.yaml"; }

step 6/6 "Health check"
vv88 doctor || true

printf '\n\033[1;32mDone.\033[0m Start the control website with:  bash scripts/termux_start.sh\n'
