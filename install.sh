#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# nopaper – Installation auf einer frischen Debian-VM (12 "bookworm" / 13 "trixie"), amd64, nur CPU.
#
#   bash install.sh          (als root)
#
# Optional vorab setzen:
#   PAPERLESS_URL=https://paperless.intern   EMPFAENGER="Klinik Muster GmbH"
#   LOCK_OLLAMA=1    Ollama nach dem Modell-Download auf localhost einsperren (Standard: 1)
#   SKIP_MODELS=1    Modelle nicht laden (z.B. wenn sie spaeter offline eingespielt werden)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PAPERLESS_URL="${PAPERLESS_URL:-}"
EMPFAENGER="${EMPFAENGER:-}"
LOCK_OLLAMA="${LOCK_OLLAMA:-1}"
SKIP_MODELS="${SKIP_MODELS:-0}"
BASE=/srv/nopaper

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'FEHLER: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "bitte als root ausfuehren (als root: bash install.sh)"
. /etc/os-release
[[ "$ID" == "debian" ]] || die "nur fuer Debian gedacht (gefunden: $ID)"
[[ "$(dpkg --print-architecture)" == "amd64" ]] || die "nur amd64 vorgesehen"
MEM_GB=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo) / 1024 / 1024 ))
(( MEM_GB >= 14 )) || echo "WARNUNG: nur ${MEM_GB} GB RAM – empfohlen sind 16–24 GB."
(( $(nproc) >= 6 )) || echo "WARNUNG: nur $(nproc) CPU-Kerne – empfohlen sind 8."

# --------------------------------------------------------------------- Pakete
log "Debian-Pakete installieren (OCR, Python, Werkzeuge)"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  python3 ocrmypdf tesseract-ocr tesseract-ocr-deu unpaper ghostscript poppler-utils \
  ca-certificates curl zstd
python3 -c 'import sys; assert sys.version_info >= (3, 11)' || die "Python >= 3.11 erforderlich"

# --------------------------------------------------------------------- Benutzer & Verzeichnisse
log "Systembenutzer und Verzeichnisse"
getent group nopaper-eingang  >/dev/null || groupadd --system nopaper-eingang
getent group nopaper-abholung >/dev/null || groupadd --system nopaper-abholung
id nopaper >/dev/null 2>&1 || useradd --system --home-dir "$BASE/zustand" --shell /usr/sbin/nologin nopaper
usermod -aG nopaper-eingang,nopaper-abholung nopaper

install -d -m 0755 -o root -g root "$BASE" "$BASE/eingang" "$BASE/ausgang"
install -d -m 2770 -o nopaper -g nopaper-eingang  "$BASE/eingang/rechnungen" "$BASE/eingang/dokumente"
install -d -m 2770 -o nopaper -g nopaper-abholung "$BASE/ausgang/rechnungsdaten"
install -d -m 0750 -o nopaper -g nopaper "$BASE/arbeit" "$BASE/fehler" "$BASE/zustand" "$BASE/zustand/cache" \
  "$BASE/test" "$BASE/test/eingang" "$BASE/test/ergebnis"

# --------------------------------------------------------------------- Programm
log "nopaper nach /opt/nopaper installieren"
rm -rf /opt/nopaper/lib
install -d -m 0755 /opt/nopaper /opt/nopaper/lib/nopaper
install -m 0644 "$HERE"/lib/nopaper/*.py /opt/nopaper/lib/nopaper/
install -m 0755 "$HERE/nopaper.py" /opt/nopaper/nopaper.py
install -m 0644 "$HERE/config.example.json" /opt/nopaper/config.example.json
install -m 0644 "$HERE/AGENTS.md" /opt/nopaper/AGENTS.md
install -m 0755 "$HERE/bin/nopaper" /usr/local/bin/nopaper
install -m 0755 "$HERE/bin/nopaper-aktivieren" /usr/local/sbin/nopaper-aktivieren
install -m 0755 "$HERE/bin/nopaper-mandant" /usr/local/sbin/nopaper-mandant
install -m 0755 "$HERE/bin/nopaper-modell-laden" /usr/local/sbin/nopaper-modell-laden
python3 -I -m compileall -q /opt/nopaper/lib >/dev/null

# --------------------------------------------------------------------- Konfiguration
install -d -m 0750 -o root -g nopaper /etc/nopaper
if [[ ! -f /etc/nopaper/config.json ]]; then
  log "Konfiguration anlegen: /etc/nopaper/config.json"
  python3 -I - "$HERE/config.example.json" /etc/nopaper/config.json "$PAPERLESS_URL" "$EMPFAENGER" <<'PY'
import json, sys
src, dst, url, empf = sys.argv[1:]
d = json.load(open(src, encoding="utf-8"))
if url:
    d["paperless"]["url"] = url.rstrip("/")
if empf:
    d["empfaenger"] = empf
open(dst, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
PY
else
  log "Konfiguration /etc/nopaper/config.json existiert – bleibt unveraendert"
fi
chown root:nopaper /etc/nopaper/config.json && chmod 0640 /etc/nopaper/config.json
[[ -f /etc/nopaper/paperless.token ]] || install -m 0640 -o root -g nopaper /dev/null /etc/nopaper/paperless.token
chown root:nopaper /etc/nopaper/paperless.token && chmod 0640 /etc/nopaper/paperless.token

# --------------------------------------------------------------------- Ollama
if ! command -v ollama >/dev/null; then
  log "Ollama installieren"
  curl -fsSL https://ollama.com/install.sh | sh
fi
install -d /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/10-nopaper.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=15m"
EOF
rm -f /etc/systemd/system/ollama.service.d/20-netlock.conf
systemctl daemon-reload
systemctl enable ollama >/dev/null
systemctl restart ollama
for _ in $(seq 30); do curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 2; done

if [[ "$SKIP_MODELS" != "1" ]]; then
  MODELS="$(python3 -I -c 'import json;m=json.load(open("/etc/nopaper/config.json"))["modelle"];print(m["text"], m.get("bild",""), m.get("glaettung","") if m.get("glaettung")!="aus" else "", m.get("extraktion",""))')"
  for m in $MODELS; do
    log "Modell $m laden (mehrere GB)"
    ollama pull "$m"
  done
fi

if [[ "$LOCK_OLLAMA" == "1" ]]; then
  log "Ollama auf localhost beschraenken (danach kein Internetzugriff mehr)"
  cat > /etc/systemd/system/ollama.service.d/20-netlock.conf <<'EOF'
[Service]
IPAddressDeny=any
IPAddressAllow=localhost
EOF
  systemctl daemon-reload
  systemctl restart ollama
fi

# --------------------------------------------------------------------- systemd
log "systemd-Dienst einrichten (noch nicht gestartet)"
install -m 0644 "$HERE/systemd/nopaper.service" /etc/systemd/system/nopaper.service
install -m 0644 "$HERE/systemd/nopaper@.service" /etc/systemd/system/nopaper@.service
systemctl daemon-reload

cat <<EOF

=====================================================================
nopaper ist installiert. (Alle Befehle als root ausfuehren.)

Testen OHNE paperless (nur Musterbelege!):
    nopaper test /pfad/zu/musterbelegen/          # oder einzelne PDFs
    nopaper test --als rechnung /pfad/zu/musterbelegen/rechnung.pdf
    nopaper test --extraktor nuextract3 --modell-glaettung aus   # Modellvergleich
    ls /srv/nopaper/test/ergebnis/            # Ergebnisse je Modell
    nopaper test --aufraeumen                 # Testdaten loeschen

Fuer den Betrieb mit paperless noch vier Schritte:

 1. Konfiguration anpassen:      nano /etc/nopaper/config.json
    (paperless.url, empfaenger, tags, felder, dokumentrechte)

 2. Setup-Skript fuer paperless erzeugen und dort ausfuehren:
      nopaper paperless-skript > nopaper-setup.py
    auf dem paperless-Host im Compose-Verzeichnis:
      docker compose exec -T webserver python3 manage.py shell < nopaper-setup.py
    Es legt Benutzer "nopaper", Tags und Felder an und gibt TOKEN=... aus.
    (Nicht noetig: Benutzer, Tags und Felder lassen sich auch von Hand anlegen;
     nopaper check nennt, was fehlt.)

 3. Token hinterlegen:           nano /etc/nopaper/paperless.token

 4. Aktivieren (Netzfreigabe, Pruefung, Start):
      nopaper-aktivieren

Eingang:   $BASE/eingang/rechnungen   $BASE/eingang/dokumente
Ausgang:   $BASE/ausgang/rechnungsdaten
Fehler:    $BASE/fehler
Protokoll: journalctl -u nopaper -f

Weitere Mandanten (eigener Dienst, eigene Ordner, eigenes paperless):
    nopaper-mandant anlegen <name>      danach: nopaper-aktivieren <name>

Empfehlung: Ausgehenden Verkehr der VM jetzt auf Hypervisor-/Netzebene
auf den paperless-Host (und ggf. Scanner/Buchhaltung) beschraenken.
=====================================================================
EOF
