#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/Halo.app"
MACOS="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"

echo "Building frontends..."
(cd "$ROOT/frontend-dashboard" && npm install && npm run build)
(cd "$ROOT/frontend-overlay" && npm install && npm run build)

echo "Ensuring Python venv..."
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"
"$ROOT/.venv/bin/pip" install pywebview || true

echo "Writing Halo.app..."
rm -rf "$APP"
mkdir -p "$MACOS" "$RES"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Halo</string>
  <key>CFBundleDisplayName</key><string>Halo AI DJ</string>
  <key>CFBundleIdentifier</key><string>com.docweather.halodj</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Halo can listen when you enable mic in Settings, and never in DJ mode.</string>
</dict>
</plist>
PLIST

cat > "$MACOS/Halo" <<EOF
#!/bin/zsh
cd "$ROOT"
exec "$ROOT/.venv/bin/python" "$ROOT/desktop.py"
EOF
chmod +x "$MACOS/Halo"

echo "Done: $APP"
echo "Double-click Halo.app, or: open \"$APP\""
echo "OBS overlay: http://127.0.0.1:8000/overlay/"
echo "Room:        http://127.0.0.1:8000/booth/?tab=room"
