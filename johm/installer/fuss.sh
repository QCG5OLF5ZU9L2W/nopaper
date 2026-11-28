(cd "$base"; sha256sum -c "$tmp/original.sha256") || { echo "Installierte Johm-Version weicht ab. Installation beendet."; exit 1; }
if ! /usr/bin/python3 -c 'import icalendar' 2>/dev/null; then apt-get install -y python3-icalendar; fi
if [[ ! -e "$conf/lesehilfe.json" ]]; then install -m 0600 "$tmp/lesehilfe.json" "$conf/lesehilfe.json"; fi
ln -s "$conf/lesehilfe.json" "$tmp/reading-rules"
(cd / && CREDENTIALS_DIRECTORY="$tmp" PYTHONPATH="$tmp:$base" /usr/bin/python3 -B -c 'import reading;reading.rules()')
if [[ ! -e "$cfg" ]]; then install -m 600 "$tmp/calendar.example.json" "$cfg"; fi
# Values that earlier versions carried in the code move into the configuration once.
/usr/bin/python3 - "$cfg" "$base" "$conf/lesehilfe.json" "$tmp/lesehilfe.json" <<'PY_MIGRATE'
import ast, json, re, sys
from pathlib import Path
path, base = Path(sys.argv[1]), Path(sys.argv[2])
# Reading signals that are new in this version join the practice's own list. Existing entries stay as they are.
own_path, shipped = Path(sys.argv[3]), json.loads(Path(sys.argv[4]).read_text())
own = json.loads(own_path.read_text())
missing = [signal for signal in shipped["signals"] if signal["id"] not in {row.get("id") for row in own["signals"]}]
if missing:
    own["signals"].extend(missing)
    fresh = own_path.with_name(own_path.name + ".new")
    fresh.write_text(json.dumps(own, ensure_ascii=False, indent=2) + "\n")
    fresh.chmod(0o600)
    fresh.replace(own_path)
    print("Lesehilfe ergänzt:", ", ".join(signal["label"] for signal in missing))
cfg = json.loads(path.read_text())
def literal(file, name):
    try:
        found = re.search(r"(?m)^" + name + r"\s*=\s*(.+)$", (base / file).read_text())
        return ast.literal_eval(found.group(1)) if found else None
    except (OSError, ValueError, SyntaxError):
        return None
added = []
if "hosts" not in cfg:
    hosts = literal("desk.py", "HOSTS")
    if isinstance(hosts, (set, list, tuple)) and hosts and all(isinstance(h, str) for h in hosts):
        cfg["hosts"] = sorted(hosts); added.append("hosts")
if "fax_address" not in cfg:
    fax = literal("mail.py", "FAX_ADDRESS")
    if isinstance(fax, str):
        cfg["fax_address"] = fax; added.append("fax_address")
if added:
    new = path.with_name(path.name + ".new")
    new.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    new.chmod(0o600)
    new.replace(path)
    print("Konfiguration ergänzt aus der bisherigen Installation:", ", ".join(added))
if not cfg.get("doctors"):
    print("Hinweis: In der Konfiguration fehlt der Abschnitt doctors. Ohne ihn kann John keine Ärztin und keinen Arzt zuordnen.")
PY_MIGRATE
pass=$(/usr/bin/python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["password_file"])' "$cfg")
[[ "$pass" =~ ^/etc/nopaper-mail/[[:alnum:]_.-]+\.pass$ && ! -L "$pass" ]] || { echo "Passwortdatei muss unter /etc/nopaper-mail liegen."; exit 1; }
if [[ ! -e "$pass" ]]; then
  read -rsp 'Nextcloud-Passwort für den konfigurierten Benutzer: ' secret; echo
  [[ -n "$secret" ]] || exit 1
  (set -C; printf '%s\n' "$secret" >"$pass"); unset secret
fi
chatkey=$conf/chat-tool.key
if [[ ! -e "$chatkey" ]]; then (set -C; /usr/bin/python3 -c 'import secrets;print(secrets.token_urlsafe(32))' >"$chatkey"); fi
PYTHONPATH="$base" /usr/bin/python3 "$tmp/probe.py" "$cfg"
PYTHONPATH="$tmp:$base" /usr/bin/python3 -c 'import desk;desk.make_app()'
if [[ $mode == --check ]]; then echo "Leseprüfung erfolgreich. Kalenderkonfiguration: $cfg"; exit 0; fi
release="$target/$(sha256sum "$0" | cut -c1-24)"
install -d -o root -g root -m 0755 "$target" "$release" "$(dirname "$drop")"
for file in lesehilfe.json reading.py wording.py mail.py appointments.py caldav_slots.py desk.py outbound.py app.js index.html style.css; do install -m 0644 "$tmp/$file" "$release/$file"; done
# Check the installed files with the actual service identity before stopping the running desk.
desk_user=$(systemctl show "$service" --property=User --value)
desk_group=$(systemctl show "$service" --property=Group --value)
[[ -n "$desk_user" ]] || { echo "Dienstbenutzer fehlt. Aktiver Arbeitsplatz läuft weiter."; exit 1; }
check_as=(runuser -u "$desk_user")
if [[ -n "$desk_group" ]]; then check_as+=(-g "$desk_group"); fi
if ! (cd / && "${check_as[@]}" -- /usr/bin/python3 -I -B - "$release" "$base" <<'PY_CHECK'
import sys
from pathlib import Path
release, base = sys.argv[1:]
sys.path[:0] = [release, base]
for name in ("reading.py","wording.py","mail.py","desk.py", "appointments.py", "caldav_slots.py", "outbound.py", "index.html", "style.css", "app.js"):
    (Path(release) / name).read_bytes()
import desk
desk.make_app()
PY_CHECK
); then
  namei -l "$release/desk.py" >&2
  echo "Zugriffsprüfung als $desk_user fehlgeschlagen. Laufender Dienst wurde nicht umgeschaltet." >&2
  exit 1
fi
install -d -m 0755 "$(dirname "$worker_drop")"
if [[ -f "$worker_drop" ]]; then cp "$worker_drop" "$tmp/previous-worker"; fi
# run_boxes must see the same mail.py as the interactive desk.
install -m 0644 "$base/run_boxes.py" "$release/run_boxes.py"
cat >"$tmp/worker" <<EOF
# managed by johm-kalender
[Service]
LoadCredential=nc-config:$cfg
Environment=PYTHONPATH=$base
ExecStart=
ExecStart=/usr/bin/python3 $release/run_boxes.py %i
EOF
if [[ -f "$drop" ]]; then cp "$drop" "$tmp/previous"; fi
cat >"$tmp/drop" <<EOF
# managed by johm-kalender
[Service]
LoadCredential=reading-rules:$conf/lesehilfe.json
LoadCredential=nc-config:$cfg
LoadCredential=nc-pass:$pass
LoadCredential=chat-key:$chatkey
Environment=PYTHONPATH=$base
ExecStart=
ExecStart=/usr/bin/python3 $release/desk.py
EOF
install -m 0644 "$tmp/drop" "$drop.new"
install -m 0644 "$tmp/worker" "$worker_drop.new"
mv -f "$worker_drop.new" "$worker_drop"
mv -f "$drop.new" "$drop"
if ! (systemctl daemon-reload && systemctl restart "$service" && curl --noproxy '*' --retry 10 --retry-connrefused --retry-delay 1 -fs --max-time 3 http://127.0.0.1:3301/johm >/dev/null && systemctl is-active --quiet "$service"); then
  journalctl -u "$service" -n 16 --no-pager >&2 || true
  if [[ -f "$tmp/previous-worker" ]]; then install -m 0644 "$tmp/previous-worker" "$worker_drop"; else rm -f "$worker_drop"; fi
  if [[ -f "$tmp/previous" ]]; then install -m 0644 "$tmp/previous" "$drop"; else rm -f "$drop"; fi
  systemctl daemon-reload; systemctl restart "$service"
  echo "Startprüfung fehlgeschlagen. Vorherige Dienstkonfiguration wieder eingesetzt."; exit 1
fi
# Keep this release and the two before it. Every installation used to leave its directory behind.
find "$target" -mindepth 1 -maxdepth 1 -type d ! -name "$(basename "$release")" -printf '%T@ %p\n' | sort -rn | tail -n +3 | cut -d' ' -f2- | xargs -r rm -rf --
echo "Johm-Kalender aktiv. Modus und Postfächer stehen in $cfg."
echo "Schlüssel für das Kalender-Werkzeug im Chat: $chatkey"
