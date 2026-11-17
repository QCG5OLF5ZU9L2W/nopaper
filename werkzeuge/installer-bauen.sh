#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Baut aus diesem Verzeichnis den selbstentpackenden Installer nopaper-installer.sh.
#   bash werkzeuge/installer-bauen.sh [zieldatei]
set -euo pipefail
HIER="$(cd "$(dirname "$0")/.." && pwd)"
ZIEL="${1:-$HIER/nopaper-installer.sh}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir "$TMP/nopaper"
( cd "$HIER" && tar cf - --exclude=.git --exclude=__pycache__ --exclude='nopaper-installer.sh' . ) | tar xf - -C "$TMP/nopaper"
tar czf "$TMP/paket.tar.gz" --owner=0 --group=0 -C "$TMP" nopaper
SHA="$(sha256sum "$TMP/paket.tar.gz" | cut -d' ' -f1)"
sed "s/@SHA256@/$SHA/" "$HIER/werkzeuge/installer-kopf.sh" > "$ZIEL"
echo "__NOPAPER_PAYLOAD__" >> "$ZIEL"
base64 "$TMP/paket.tar.gz" >> "$ZIEL"
echo "Installer: $ZIEL (Paket-SHA-256 $SHA)"
