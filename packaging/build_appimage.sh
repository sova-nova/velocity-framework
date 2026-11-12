#!/usr/bin/env bash
# Build velocity's Linux app as an AppImage (decided 2026-11-11): one file people download,
# mark executable and double-click, with Python and everything else inside, and no OCR.
#
#   packaging/build_appimage.sh        # -> build/velocity-<version>-x86_64.AppImage
#
# Needs uv and an internet connection (packages, and appimagetool on first use). It builds
# in its own environment (build/venv), so your development environment is untouched. The
# release workflow (.github/workflows/release.yml) runs this same script.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
APPIMAGETOOL_VERSION="1.9.0"
# Its release publishes no checksum: this is the file's sha256 when it was first used
# (2026-11-11, the same on 2026-11-12). A different file stops the build. When changing
# the version, download it once and put its sha256 here.
APPIMAGETOOL_SHA256="46fdd785094c7f6e545b61afcfb0f3d98d8eab243f644b4b17698c01d06083d1"
APPIMAGETOOL="$BUILD/tools/appimagetool-$APPIMAGETOOL_VERSION-x86_64.AppImage"

cd "$ROOT"
mkdir -p "$BUILD/tools"

echo "==> Python environment for the app (no OCR)"
UV_PROJECT_ENVIRONMENT="$BUILD/venv" uv sync --locked --no-default-groups --group app --group build
PY="$BUILD/venv/bin/python"
VERSION="$("$PY" -c 'import velocity; print(velocity.__version__)')"

echo "==> Bundling velocity $VERSION with PyInstaller"
rm -rf "$BUILD/dist" "$BUILD/work" "$BUILD/AppDir"
"$BUILD/venv/bin/pyinstaller" --noconfirm --clean \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" packaging/velocity.spec

echo "==> No system libraries in the bundle (they must match the user's drivers)"
if ls "$BUILD/dist/velocity/_internal" | grep -E '^lib(stdc\+\+|gcc_s|gbm|drm|EGL|GL)[.-]'; then
  echo "These must come from the user's system; see LINUX_SYSTEM_LIBRARIES in the spec."
  exit 1
fi

echo "==> Smoke test"
"$PY" packaging/smoke_test.py "$BUILD/dist/velocity/velocity"

echo "==> AppDir"
APPDIR="$BUILD/AppDir"
mkdir -p "$APPDIR/usr/bin"
cp -a "$BUILD/dist/velocity/." "$APPDIR/usr/bin/"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/velocity" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"
cat > "$APPDIR/velocity.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=velocity
GenericName=Tutor
Comment=A tutor for any subject, with your own books and notes
Exec=velocity
Icon=velocity
Terminal=false
Categories=Education;
StartupWMClass=velocity
DESKTOP
cp src/velocity/web_static/icon.svg "$APPDIR/velocity.svg"
cp src/velocity/web_static/icon-256.png "$APPDIR/.DirIcon"

echo "==> appimagetool $APPIMAGETOOL_VERSION"
if [ ! -x "$APPIMAGETOOL" ]; then
  curl -fsSL -o "$APPIMAGETOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/$APPIMAGETOOL_VERSION/appimagetool-x86_64.AppImage"
  chmod +x "$APPIMAGETOOL"
fi
echo "$APPIMAGETOOL_SHA256  $APPIMAGETOOL" | sha256sum --check --quiet || {
  echo "appimagetool's checksum doesn't match: not using it." >&2
  rm -f "$APPIMAGETOOL"
  exit 1
}
OUT="$BUILD/velocity-$VERSION-x86_64.AppImage"
# Runs without FUSE (e.g. in CI containers).
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$APPIMAGETOOL" --no-appstream "$APPDIR" "$OUT"
echo "==> $OUT ($(du -h "$OUT" | cut -f1))"
