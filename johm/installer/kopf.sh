#!/usr/bin/env bash
# Johm Kalender • auf dem bestehenden Johm-Host mit sudo ausführen
set -euo pipefail
umask 077
[[ $EUID == 0 ]] || { echo "Bitte mit sudo ausführen."; exit 1; }
mode=${1:---install}
case "$mode" in --install|--check|--rollback) ;; *) echo "Optionen: --check | --install | --rollback"; exit 2;; esac
service=nopaper-mail-desk.service
conf=/etc/nopaper-mail
base=/usr/local/lib/nopaper-mail
target=/usr/local/lib/johm-kalender
drop=/etc/systemd/system/$service.d/65-johm-kalender.conf
worker_drop=/etc/systemd/system/nopaper-mail@.service.d/65-johm-sprache.conf
cfg=$conf/kalender.json
exec 9>/run/lock/johm-kalender-install.lock
flock -n 9 || { echo "Kalenderinstallation läuft bereits."; exit 1; }
if [[ -e "$drop" ]] && ! grep -qxF '# managed by johm-kalender' "$drop"; then echo "Fremdes Drop-in vorhanden."; exit 1; fi
if [[ -e "$worker_drop" ]] && ! grep -qxF '# managed by johm-kalender' "$worker_drop"; then echo "Fremdes Worker-Drop-in vorhanden."; exit 1; fi
if [[ $mode == --rollback ]]; then
  if [[ -f "$worker_drop" ]]; then rm -- "$worker_drop"; systemctl daemon-reload; fi
  if [[ -f "$drop" ]]; then rm -- "$drop"; systemctl daemon-reload; systemctl restart "$service"; fi
  echo "Kalender-Erweiterung deaktiviert. Offene Reservierungen in Nextcloud bitte manuell betreuen."; exit 0
fi
[[ -d "$conf" && -f "$base/desk.py" ]] || { echo "Bestehender Johm-Mailarbeitsplatz fehlt."; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
