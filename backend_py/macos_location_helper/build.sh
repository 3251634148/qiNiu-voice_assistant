#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HERE/VoiceAssistantLocationHelper.app"
MACOS_DIR="$APP_DIR/Contents/MacOS"
RES_DIR="$APP_DIR/Contents/Resources"

mkdir -p "$MACOS_DIR" "$RES_DIR"
cp "$HERE/Info.plist" "$APP_DIR/Contents/Info.plist"

swiftc -O -o "$MACOS_DIR/VoiceAssistantLocationHelper" "$HERE/LocationHelper.swift"

# ad-hoc sign (helps TCC treat it as an app)
codesign --force --deep --sign - "$APP_DIR" >/dev/null 2>&1 || true

echo "built: $APP_DIR"
