#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Baut aus johm/quelle und johm/installer das Installationsskript johm-kalender-install.sh.
#   bash johm/werkzeuge/installer-bauen.sh [zieldatei]
set -euo pipefail
HIER="$(cd "$(dirname "$0")/.." && pwd)"
ZIEL="${1:-$HIER/johm-kalender-install.sh}"
{
  cat "$HIER/installer/kopf.sh"
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    printf 'cat > "$tmp/%s" <<'"'"'JOHM_FILE'"'"'\n' "$name"
    if grep -qx 'JOHM_FILE' "$HIER/quelle/$name"; then echo "Die Zeile JOHM_FILE darf in $name nicht vorkommen." >&2; exit 1; fi
    cat "$HIER/quelle/$name"
    printf 'JOHM_FILE\n'
  done < "$HIER/installer/dateien.txt"
  cat "$HIER/installer/fuss.sh"
} > "$ZIEL"
bash -n "$ZIEL"
echo "Installer: $ZIEL (SHA-256 $(sha256sum "$ZIEL" | cut -d' ' -f1))"
