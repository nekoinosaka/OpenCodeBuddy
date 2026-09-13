#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src/opencode_buddy/native_ble_helper/CodeBuddyBLEHelper.swift"
PLIST="$ROOT/src/opencode_buddy/native_ble_helper/Info.plist"
APP="$ROOT/.build/native/CodeBuddyBLEHelper.app"
BIN="$APP/Contents/MacOS/CodeBuddyBLEHelper"
APP_PLIST="$APP/Contents/Info.plist"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$PLIST" "$APP_PLIST"
swiftc -target arm64-apple-macosx13.0 -parse-as-library -O \
  -framework AppKit -framework CoreBluetooth "$SRC" -o "$BIN"
xattr -cr "$APP"
codesign --force --sign - "$APP"
echo "$APP"
