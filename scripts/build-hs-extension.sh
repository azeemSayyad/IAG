#!/usr/bin/env bash
# Package the IAG HealthSherpa Chrome extension for agents
# (unzip -> chrome://extensions -> Developer mode -> Load unpacked), or for the
# Chrome Web Store as-is. The zip is the PRODUCTION build: the localhost /
# 127.0.0.1 entries used for local testing are removed, so only the live
# portal (portal.iagplans.com) can hand the extension a job.
#   scripts/build-hs-extension.sh   -> dist/iag-healthsherpa-extension-<version>.zip
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=apps/hs-extension
VER=$(python3 -c "import json;print(json.load(open('$SRC/manifest.json'))['version'])")
OUT="$PWD/dist/iag-healthsherpa-extension-$VER.zip"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cp -R "$SRC"/manifest.json "$SRC"/common.js "$SRC"/background.js "$SRC"/portal.js "$SRC"/hs.js \
      "$SRC"/overlay.css "$SRC"/popup.html "$SRC"/popup.js "$SRC"/icons "$TMP"/
python3 - "$TMP" <<'PY'
import json, sys, re
d = sys.argv[1]
local = lambda u: "127.0.0.1" in u or "localhost" in u
m = json.load(open(f"{d}/manifest.json"))
m["host_permissions"] = [u for u in m["host_permissions"] if not local(u)]
for cs in m["content_scripts"]:
    cs["matches"] = [u for u in cs["matches"] if not local(u)]
json.dump(m, open(f"{d}/manifest.json", "w"), indent=2, ensure_ascii=False)
c = open(f"{d}/common.js").read()
c2 = re.sub(r'IAG\.PORTAL_HOSTS = \[[^\]]*\];', 'IAG.PORTAL_HOSTS = ["portal.iagplans.com"];', c)
assert c2 != c, "PORTAL_HOSTS line not found"
open(f"{d}/common.js", "w").write(c2)
PY
mkdir -p dist
rm -f "$OUT"
(cd "$TMP" && zip -qr "$OUT" . -x '*.DS_Store')
echo "dist/iag-healthsherpa-extension-$VER.zip"
