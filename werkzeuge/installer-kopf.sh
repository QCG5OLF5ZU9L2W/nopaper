#!/usr/bin/env bash
# nopaper – selbstentpackender Installer fuer ein frisches Debian (12/13) oder Ubuntu LTS (24.04/26.04), amd64
#
#   PAPERLESS_URL=https://paperless.example.org EMPFAENGER="Klinik Muster GmbH" bash nopaper-installer.sh   (als root)
#
# Entpackt nach ./nopaper (aenderbar mit ZIEL=...) und startet dort install.sh.
# Nur entpacken, nicht installieren:  bash nopaper-installer.sh --nur-entpacken
set -euo pipefail

ZIEL="${ZIEL:-$PWD/nopaper}"
PAYLOAD_SHA256="@SHA256@"

NUR_ENTPACKEN=0
[[ "${1:-}" == "--nur-entpacken" ]] && NUR_ENTPACKEN=1

if [[ $NUR_ENTPACKEN -eq 0 && $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || { echo "Bitte als root ausfuehren (su -c 'bash $0')" >&2; exit 1; }
  exec sudo --preserve-env=PAPERLESS_URL,EMPFAENGER,LOCK_OLLAMA,SKIP_MODELS,ZIEL bash "$0" "$@"
fi

for c in base64 tar sha256sum awk; do command -v "$c" >/dev/null || { echo "$c fehlt" >&2; exit 1; }; done

ZEILE=$(awk '/^__NOPAPER_PAYLOAD__$/{print NR + 1; exit 0}' "$0")
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
tail -n +"$ZEILE" "$0" | base64 -d > "$TMP"
echo "$PAYLOAD_SHA256  $TMP" | sha256sum -c --quiet - || { echo "Paket beschaedigt (Pruefsumme falsch)" >&2; exit 1; }

ELTERN="$(dirname "$ZIEL")"
mkdir -p "$ELTERN"
if [[ -e "$ZIEL" ]]; then
  mv "$ZIEL" "$ZIEL.alt-$(date +%Y%m%d-%H%M%S)"
  echo "Vorhandenes $ZIEL umbenannt."
fi
TMPD=$(mktemp -d)
tar xzf "$TMP" -C "$TMPD"
mv "$TMPD/nopaper" "$ZIEL"
rmdir "$TMPD"

# Dateien dem Besitzer des Elternverzeichnisses geben
BESITZER="$(stat -c '%U:%G' "$ELTERN")"
[[ $EUID -eq 0 ]] && chown -R "$BESITZER" "$ZIEL"
echo "Entpackt nach $ZIEL"

[[ $NUR_ENTPACKEN -eq 1 ]] && exit 0
cd "$ZIEL"
exec bash ./install.sh
exit 0
