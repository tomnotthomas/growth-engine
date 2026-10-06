#!/usr/bin/env bash
# Build Growth Engine.app (the Mac control center) with the Swift compiler from the Command Line Tools;
# no Xcode project needed. Output: mac/build/Growth Engine.app (ad-hoc signed).
#   mac/GrowthEngine/build.sh            build
#   mac/GrowthEngine/build.sh --install  build and copy to ~/Applications
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../build"
APP="$OUT/Growth Engine.app"
ARCH="$(uname -m)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$OUT/ModuleCache"
EXTRA=()
# Some Command Line Tools installs ship the Swift bridging module twice (module.modulemap and
# bridging.modulemap), which makes every SwiftUI build fail or hang. Hide the duplicate for this build
# only, with a file overlay, instead of changing the system install.
SWIFT_INC="$(dirname "$(dirname "$(xcrun --find swiftc)")")/include/swift"
if [ -f "$SWIFT_INC/module.modulemap" ] && [ -f "$SWIFT_INC/bridging.modulemap" ]; then
  : > "$OUT/empty.modulemap"
  printf '{"version":0,"case-sensitive":"false","roots":[{"type":"directory","name":"%s","contents":[{"type":"file","name":"bridging.modulemap","external-contents":"%s"}]}]}\n' \
    "$SWIFT_INC" "$OUT/empty.modulemap" > "$OUT/overlay.yaml"
  EXTRA=(-vfsoverlay "$OUT/overlay.yaml" -Xcc -ivfsoverlay -Xcc "$OUT/overlay.yaml")
fi
swiftc -O -parse-as-library -swift-version 5 \
  -target "$ARCH-apple-macos14.0" \
  -module-cache-path "$OUT/ModuleCache" ${EXTRA[@]+"${EXTRA[@]}"} \
  -o "$APP/Contents/MacOS/GrowthEngine" \
  "$HERE"/Sources/*.swift
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
codesign --force --sign - --options runtime "$APP" >/dev/null
echo "built $APP"

if [ "${1:-}" = "--install" ]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/Growth Engine.app"
  cp -R "$APP" "$HOME/Applications/"
  echo "installed ~/Applications/Growth Engine.app"
fi
