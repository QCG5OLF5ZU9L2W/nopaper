# nopaper

**Deutsch** | [English](README.en.md)

Vorverarbeitung gescannter Dokumente vor einem **bestehenden [paperless-ngx](https://github.com/paperless-ngx/paperless-ngx)**. Läuft vollständig lokal auf einer Maschine mit Debian oder Ubuntu, ohne Cloud-Dienst; eine GPU ist nicht nötig.

- **Rechnungen und Belege** werden erkannt und ausgelesen. Das unveränderte Original geht an paperless, mit festen Tags, sprechendem Titel, geglättetem Volltext und den Rechnungsdaten in benutzerdefinierten Feldern. Dazu entsteht ein Sidecar (CII-XML) mit paperless-ID und SHA-256.
- **Sonstige Scans** gehen an paperless und bekommen einen geglätteten Volltext.
- **Vorhandene Dokumente** in paperless lassen sich über Tags nachträglich als Rechnung auslesen oder nur im Volltext erneuern.
- **Mehrere Mandanten** können getrennt auf derselben Maschine laufen, jeder mit eigenem paperless.

Die Sprachmodelle laufen lokal über [Ollama](https://ollama.com). Was ein Modell liest, wird gegen den erkannten Text geprüft: Ein Wert, der nicht wörtlich auf dem Beleg steht, wird verworfen oder zur Prüfung markiert.

## Stand und Grenzen

- Version siehe `lib/nopaper/__init__.py`, Änderungen in [`CHANGELOG.md`](CHANGELOG.md).
- Erprobt an **einer** Installation mit paperless-ngx 3.x, deutschen Belegen und CPU-Betrieb (etwa drei Minuten je Beleg). Das Aufgabenformat von paperless-ngx 2.x wird unterstützt, ist aber nur in den Offline-Tests geprüft.
- nopaper ersetzt keine Prüfung durch einen Menschen. Lesen Bildmodell und Texterkennung denselben Wert übereinstimmend falsch, fällt das keiner Regel auf.
- Der Sidecar ist eine Buchungshilfe im Format Factur-X/ZUGFeRD BASIC WL und **keine E-Rechnung** im Sinne von § 14 UStG. Er ist nicht gegen das offizielle Schema validiert.
- nopaper liest nur Kopfdaten, keine Rechnungspositionen.
- Keine Steuer- oder Rechtsberatung.

Entscheidungen und Regeln stehen in [`AGENTS.md`](AGENTS.md). Sie sind verbindlich für die Weiterentwicklung.

## Voraussetzungen

- Debian 12 oder 13 oder Ubuntu 24.04 LTS oder 26.04 LTS (amd64), 8 vCPU, 16–24 GB RAM, 60 GB Platte (verschlüsselt empfohlen)
- ein laufendes paperless-ngx, erreichbar per HTTPS
- Internet nur während der Installation (Pakete, Ollama, Modelle)

Außer Paketen der Distribution (`ocrmypdf`, `tesseract-ocr-deu`, `poppler-utils`; bei Ubuntu aus der Komponente `universe`) und Ollama gibt es keine Abhängigkeiten. Der Code nutzt nur die Python-Standardbibliothek.

## Installation Schritt für Schritt

Der ganze Weg von einem frisch installierten Server bis zum ersten Beleg. Die Befehle lassen sich so kopieren, wie sie dastehen. Nur in Schritt 4 setzt du deine eigenen Werte ein.

Du brauchst: einen Server mit Debian 12/13 oder Ubuntu 24.04/26.04 (siehe Voraussetzungen), Internetzugang für die Installation, die Adresse deines paperless-ngx und dort ein Administrator-Konto.

### 1. Als root anmelden

Alle folgenden Befehle laufen als root. Bei Ubuntu und bei Debian mit `sudo`:

```bash
sudo -i
```

Bei Debian ohne `sudo`:

```bash
su -
```

### 2. System aktualisieren und git installieren

```bash
apt-get update
apt-get -y upgrade
apt-get -y install git ca-certificates
```

### 3. nopaper herunterladen

```bash
cd /root
if [ -d nopaper/.git ]; then git -C nopaper pull --ff-only; else git clone https://github.com/QCG5OLF5ZU9L2W/nopaper.git; fi
cd /root/nopaper
```

Der Ordner `/root/nopaper` enthält danach die Quellen. Das Programm selbst landet im nächsten Schritt unter `/opt/nopaper`. Ist der Ordner schon da, holt der Befehl nur den neuen Stand; die Schritte 3 und 4 lassen sich deshalb jederzeit wiederholen.

### 4. Installieren

Ersetze die Adresse durch die deines paperless und den Namen durch den Rechnungsempfänger, der auf deinen Belegen steht:

```bash
cd /root/nopaper
PAPERLESS_URL=https://paperless.example.org EMPFAENGER="Muster GmbH" bash install.sh
```

Das Skript installiert die Pakete, Ollama mit den Sprachmodellen (mehrere GB, das dauert je nach Leitung eine Weile) und nopaper als systemd-Dienst. Der Dienst wird noch nicht gestartet. Ollama wird nach dem Laden der Modelle auf localhost beschränkt. Die beiden Angaben gelten für die Standard-Instanz; weitere Mandanten legst du später an (siehe [Mandanten](#mandanten)).

Auf einer vorhandenen Installation aktualisiert derselbe Aufruf das Programm: Konfiguration, Token und Belege bleiben, die beiden Angaben werden nicht mehr gebraucht, und die Dienste, die schon liefen, werden neu gestartet.

Am Ende steht `nopaper ist installiert.` Prüfen, ob die Modelle da sind:

```bash
ollama list
```

### 5. Probelauf ohne paperless (optional)

Nur mit Musterbelegen, nie mit echten. Lege ein oder mehrere PDFs in einen Ordner, zum Beispiel `/root/muster`, und starte:

```bash
nopaper test /root/muster/
ls /srv/nopaper/test/ergebnis/
nopaper test --aufraeumen
```

### 6. paperless vorbereiten

Das geschieht in der Weboberfläche von paperless, nicht auf dem Server. nopaper legt dort nichts an.

1. **Benutzer** `nopaper` anlegen, ohne Admin-Rechte. Rechte: Dokumente ansehen, hinzufügen und ändern; Tags, benutzerdefinierte Felder und Aufgaben ansehen. Kein Löschrecht.
2. **Tags** anlegen, jeweils mit Zuweisung „Keine“: `Rechnung`, `Eingangsrechnung`, `Scan`, `nopaper prüfen`, `nopaper Rechnung`, `totext`, `Bewirtung`, `Kassenbon`, `Versicherung`, `Behörde`.
3. **Benutzerdefinierte Felder** anlegen:

| Feldname | Datentyp |
|---|---|
| `Erkannt: Belegart`, `Erkannt: Rechnungsnummer`, `Erkannt: Rechnungssteller`, `Erkannt: Skonto`, `Erkannt: Verwendungszweck`, `IBAN` | Text |
| `Erkannt: Rechnungsdatum`, `Zahlungsziel` | Datum |
| `Erkannt: Betrag brutto`, `Erkannt: Betrag netto`, `Erkannt: Steuer_High`, `Erkannt: Steuer_Low`, `Erkannt: Zahlbetrag` | Währung |

4. **API-Token** für den Benutzer `nopaper` erzeugen (in der Admin-Oberfläche von paperless unter „Tokens“) und kopieren.

Das sind die Namen aus der mitgelieferten Konfiguration. Willst du andere Namen oder weniger Felder, änderst du sie in Schritt 7. Einzelheiten stehen unter [Einrichten in paperless](#einrichten-in-paperless).

### 7. Token und Konfiguration eintragen

Token hinterlegen. Der Befehl fragt nach dem Token; beim Einfügen wird nichts angezeigt, und das Token landet nicht im Befehlsverlauf:

```bash
read -rsp "Token: " T && printf '%s\n' "$T" > /etc/nopaper/paperless.token; unset T; echo
```

Die Konfiguration musst du nur öffnen, wenn deine Tags oder Felder anders heißen als in Schritt 6 oder wenn du Adresse und Empfänger in Schritt 4 nicht angegeben hast:

```bash
nano /etc/nopaper/config.json
```

Speichern mit `Strg+O`, `Enter`, beenden mit `Strg+X`.

### 8. Prüfen und starten

```bash
nopaper check
```

`nopaper check` nennt fehlende Tags, Felder und Modelle. Erst wenn nichts mehr fehlt:

```bash
nopaper-aktivieren
```

Der Befehl gibt im Dienst nur den paperless-Host frei, prüft noch einmal und startet den Dienst.

### 9. Der erste Beleg

Protokoll in einem zweiten Fenster mitlesen:

```bash
journalctl -u nopaper -f
```

Eine Rechnung als PDF in den Eingang legen (Pfad anpassen):

```bash
cp /root/muster/rechnung.pdf /srv/nopaper/eingang/rechnungen/
```

Nach einigen Minuten erscheint das Dokument in paperless mit Tags, Titel und Feldern. Andere Scans gehören nach `/srv/nopaper/eingang/dokumente/`. Soll ein anderer Benutzer des Servers Scans ablegen, nimm ihn in die Gruppe auf:

```bash
usermod -aG nopaper-eingang BENUTZERNAME
```

### 10. Später aktualisieren

Dafür braucht der Server kurz wieder Internetzugang. Konfiguration, Token, Belege und Modelle bleiben erhalten.

```bash
nopaper-aktualisieren
```

Der Befehl holt den neuen Stand in das Verzeichnis, aus dem installiert wurde (`/root/nopaper`), und startet dort `install.sh`. Das erkennt die vorhandene Installation und startet alle Dienste neu, die schon liefen, auch die der Mandanten. `nopaper-aktualisieren --pruefen` zeigt nur, ob es etwas Neues gibt.

Dasselbe erreichst du, indem du die Schritte 3 und 4 wiederholst. Gibt es den Befehl noch nicht (Installation vor Version 1.5.3), einmalig so:

```bash
cd /root/nopaper && git pull --ff-only && bash install.sh
```

Ein selbstentpackender Installer für Server ohne git lässt sich mit `werkzeuge/installer-bauen.sh` erzeugen.

## Einrichten in paperless

nopaper legt in paperless nichts an. Tags, Felder und der API-Benutzer müssen vorhanden sein.

1. **API-Benutzer** ohne Admin-Rechte anlegen. Nötige Rechte: Dokumente ansehen, hinzufügen und ändern; Tags, benutzerdefinierte Felder und Aufgaben ansehen. Kein Löschrecht.
2. **Tags** anlegen, jeweils mit Zuweisung „Keine“, damit paperless sie nicht selbst verteilt.
3. **Benutzerdefinierte Felder** anlegen. Schlüssel und Datentypen:

| Schlüssel in `felder` | Datentyp | Inhalt |
|---|---|---|
| `belegart` | Text | rechnung, kassenbon, bewirtung, versicherung, behoerde, gutschrift |
| `rechnungsnummer` | Text | Rechnungs- oder Belegnummer |
| `rechnungsdatum`, `leistungsdatum`, `faelligkeit` | Datum | |
| `rechnungssteller` | Text | Name des Verkäufers |
| `brutto`, `netto`, `steuer`, `zahlbetrag` | Währung | `zahlbetrag` = noch zu zahlen |
| `steuer_hoch`, `steuer_niedrig` | Währung | Steuerbetrag zu Sätzen ab 10 % bzw. darunter |
| `skonto` | Text | `satz:frist:betrag`, Stufen mit `;` getrennt |
| `iban`, `verwendungszweck` | Text | für die Überweisung |
| `pruefstatus` | Text | ok / prüfen |

   Jeder Schlüssel ist optional. Ein Feld ohne Wert bleibt leer.
4. `/etc/nopaper/config.json` anpassen (Adresse, Empfänger, Tags, Feldnamen, Dokumentrechte) und das Token in `/etc/nopaper/paperless.token` eintragen.
5. Prüfen und starten:

```bash
nopaper check              # nennt fehlende Tags, Felder und Modelle
nopaper-aktivieren         # Netzfreigabe nur zum paperless-Host, dann Start
```

`nopaper paperless-skript` gibt als Alternative ein Skript für die Django-Shell von paperless aus, das Benutzer, Tags und Felder anlegt. Es ist nicht nötig.

## Betriebsart „erst paperless“

Seit Version 1.6.0 kann nopaper hinter paperless arbeiten statt davor (Entscheidung E61 in `AGENTS.md`). Das ist die Zielarchitektur; der Scanner-Betrieb mit Eingangsordnern bleibt vorerst der Standard.

| | Scanner-Betrieb (Standard) | erst paperless |
|---|---|---|
| Der Scanner liefert an | Eingangsordner von nopaper | paperless (Consume-Ordner) |
| nopaper wird angestoßen durch | neue Datei im Eingang | Auslöser-Tag am Dokument |
| Sidecar | Datei im Ausgangsordner | Feld am Dokument in paperless |
| Daten auf dem nopaper-Rechner | Datenbank, Eingang, Ausgang, Fehlerordner | keine |
| Fällt nopaper aus | Scans bleiben im Eingang liegen | Dokumente sind trotzdem in paperless |

Einrichten:

1. In paperless ein benutzerdefiniertes Feld für den Sidecar anlegen, zum Beispiel `Erkannt: Sidecar`, mit einem Datentyp für langen Text. Ein gewöhnliches Textfeld ist zu kurz; `nopaper check` meldet das.
2. In paperless einen Arbeitsablauf anlegen, der neuen Dokumenten den Auslöser-Tag gibt: `nopaper Rechnung` für Rechnungen, `totext` für alles andere. Am einfachsten über zwei Scanprofile, die paperless unterscheiden kann.
3. Den Scanner auf den Consume-Ordner von paperless umstellen.
4. In `/etc/nopaper/config.json` zwei Einträge ergänzen und, falls vorhanden, den Abschnitt `kumero` entfernen:

```bash
python3 -I - <<'PY'
import json
p = "/etc/nopaper/config.json"
d = json.load(open(p, encoding="utf-8"))
d["betriebsart"] = "paperless"
d["felder"]["sidecar"] = "Erkannt: Sidecar"
d.pop("kumero", None)
open(p, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
PY
nopaper check && nopaper-aktivieren
```

`nopaper status` zeigt danach, wie viele Dokumente in paperless noch einen Auslöser-Tag tragen. Die Buchhaltung holt den Sidecar aus dem Feld; nopaper liefert in dieser Betriebsart nicht selbst an kumero ein.

Grenzen: Die Betriebsart ist bisher nur in den Offline-Tests gelaufen. Die Gegenprobe zwischen Scan-Ordner und KI entfällt, weil der Tag als Entscheidung gilt. Wer das Dokument in paperless bearbeiten darf, kann auch das Sidecar-Feld ändern.

## Die drei Wege

| Weg | Auslöser | Was nopaper tut |
|---|---|---|
| Scanner | PDF in `eingang/rechnungen` oder `eingang/dokumente` | einordnen, auslesen, an paperless übergeben, Felder, Titel, Sidecar |
| Nachbearbeitung | Tag aus `tags.ausloeser_nachbearbeitung` am Dokument | als Rechnung auslesen, Felder, Titel, Tags, Sidecar; Tag entfernen |
| Volltext | Tag aus `tags.ausloeser_text` am Dokument | Text neu erkennen und glätten, nur den Volltext ersetzen; Tag entfernen |

Der Volltext wird nur ersetzt, wenn der neue Text nicht schlechter ist als der vorhandene.

## Termin aus einer Ladung

Trägt ein Dokument den Tag aus `tags.ausloeser_text` und ist es eine gerichtliche Ladung, schreibt nopaper den Verhandlungstermin in benutzerdefinierte Felder. Dafür in paperless die Felder anlegen und in der Konfiguration unter `felder` eintragen; jedes ist optional:

| Schlüssel in `felder` | Datentyp | Inhalt |
|---|---|---|
| `termin` | Datum | Tag der Verhandlung |
| `terminzeit` | Text | Uhrzeit und Saal, z. B. „11:00 Uhr, Sitzungssaal V“ |
| `aktenzeichen` | Text | Aktenzeichen des Gerichts |

Erkannt wird mit festen Regeln, ohne Sprachmodell: Es zählt nur ein Datum, das bei „Termin“, „Verhandlung“, „geladen“ oder „anberaumt“ steht und dem eine Uhrzeit folgt. Ist der Termin nicht sicher lesbar oder stehen mehrere im Text (etwa bei einer Umladung), bleiben die Felder leer und das Dokument bekommt den Prüf-Tag.

Das Feld ist eine Arbeitshilfe und ersetzt keine Fristenkontrolle: Liest die Texterkennung eine Ziffer falsch, steht der falsche Tag im Feld. Maßgeblich ist die Ladung selbst.

## Verzeichnisse

| Pfad | Zweck |
|---|---|
| `/srv/nopaper/eingang/rechnungen`, `…/dokumente` | Ablage für Scans (Gruppe `nopaper-eingang`) |
| `/srv/nopaper/ausgang/rechnungsdaten` | Sidecars zur Abholung (Gruppe `nopaper-abholung`); Dateien mit `.tmp-` am Anfang ignorieren |
| `/srv/nopaper/fehler` | nicht verarbeitbare Scans mit `*.fehler.json` (nur Codes) |
| `/srv/nopaper/arbeit` | Scans in Bearbeitung, nach bestätigtem Upload gelöscht |
| `/srv/nopaper/zustand` | SQLite, nur IDs, Prüfsummen und Status |

Eine Eingangsdatei wird erst gelöscht, wenn paperless die Dokument-ID gemeldet hat und die Prüfsumme des Originals übereinstimmt.

## Mandanten

Je Mandant ein eigener Dienst, eigener Systembenutzer, eigene Ordner, eigene Konfiguration und ein eigenes paperless. Gemeinsam sind nur Programm und Sprachmodelle.

```bash
nopaper-mandant anlegen kanzlei
# Konfiguration und Token unter /etc/nopaper-mandanten/kanzlei/ eintragen
nopaper --mandant kanzlei check
nopaper-aktivieren kanzlei
journalctl -u nopaper@kanzlei -f
nopaper-mandant liste
```

Grenzen: Die Mandanten teilen sich die Rechenzeit, und der Administrator der Maschine sieht alle Mandanten.

## Einlieferung an die Buchhaltung kumero

Optional. Ist in der Konfiguration ein Abschnitt `kumero` eingetragen, sendet nopaper jeden Sidecar selbst an kumero. Der Ausgangsordner ist dann die Warteschlange: Eine Datei bleibt liegen, bis kumero sie angenommen hat. Von Hand darf dort dann nichts mehr abgeholt oder gelöscht werden.

```json
"kumero": {
  "url": "https://kumero.example.org",
  "mandant": "praxis",
  "token_datei": "/etc/nopaper/kumero.token"
}
```

- Verwendet kumero ein selbst signiertes Zertifikat oder eine interne CA, trägst du zusätzlich `"ca_datei": "/etc/nopaper/kumero-ca.pem"` ein und legst das Zertifikat dort ab. Für paperless gibt es dasselbe als `paperless.ca_datei`. Die Zertifikatsprüfung lässt sich nicht abschalten.
- Das Token steht nur in der Token-Datei (root:nopaper, 0640). Danach `nopaper-aktivieren` ausführen, damit der Dienst den kumero-Host erreichen darf.
- `nopaper check` zeigt, ob kumero bereit ist. `nopaper status` zeigt, wie viele Sidecars warten und wie viele abgelehnt wurden.
- Ist kumero nicht erreichbar, läuft die Belegverarbeitung weiter, und die Sidecars werden später gesendet (nach 1, 5, 15 Minuten, dann stündlich).
- Abgelehnte Sidecars liegen unter `ausgang/rechnungsdaten/abgelehnt/` mit einer Begleitdatei, die nur den Code nennt.
- Mit `tags.zu_buchen` setzt nopaper jedem Beleg zusätzlich ein Tag für „noch zu buchen“.

## Lange Rechnungen

Mit den Standardwerten bekommt das Modell bis zu 12.000 Zeichen Text (etwa vier bis sechs Seiten) oder drei Seitenbilder. Ist ein Dokument länger, liest es Anfang und Ende. Soll eine Rechnung mit zehn und mehr Seiten vollständig gelesen werden, hebst du in der Konfiguration unter `modelle` zwei Werte an:

| Schlüssel | Standard | Wirkung |
|---|---|---|
| `kontext` | 8192 | Textbudget: 12.000 Zeichen je 8.192 Token. Mit 65536 sind es 96.000 Zeichen. |
| `nuextract_seiten` | 3 | Seitenbilder für NuExtract im Bild-Modus. Es werden nie mehr verwendet, als in den Kontext passen (etwa 3.500 Token je Seite; mit 65536 bis zu 18 Seiten). |

Mehr Kontext braucht mehr Speicher und Rechenzeit; auf einer Maschine ohne Grafikkarte ist das nur begrenzt sinnvoll. Mit `ollama ps` siehst du, ob das Modell noch ganz in den Grafikspeicher passt (`100% GPU`).

## Betrieb

```bash
journalctl -u nopaper -f          # Protokoll: nur IDs, Status, Codes
nopaper status                    # Warteschlange
nopaper once                      # ein Durchlauf von Hand
nopaper nachbearbeiten 1234       # ein vorhandenes Dokument sofort als Rechnung verarbeiten
nopaper-modell-laden <modell>     # weiteres Modell laden; die Netzsperre wird danach wieder gesetzt
```

Ändert sich die IP-Adresse des paperless-Hosts, `nopaper-aktivieren` erneut ausführen.

## Testen ohne paperless

```bash
nopaper test /pfad/zu/musterbelegen/          # Ordner oder einzelne PDFs
nopaper test --als rechnung beleg.pdf
nopaper test --extraktor nuextract3 --nuextract-eingabe bild --modell-glaettung aus
nopaper test --aufraeumen                     # Testdaten löschen
```

Liegt neben einer PDF eine Datei `<name>.soll.json` mit den erwarteten Werten, gibt der Testlauf eine Trefferquote je Feld aus. Die Ergebnisse liegen unter `/srv/nopaper/test/ergebnis/<modell>/` und enthalten die Belegdaten im Klartext. Deshalb nur mit Musterbelegen testen und danach aufräumen. Soll-Werte immer vom Papier nehmen, nie aus dem erkannten Text.

Offline-Tests des Codes, ohne Netz, ohne paperless und ohne Ollama:

```bash
python3 -I tests/test_nopaper.py
```

## Datenschutz

- Verarbeitung ausschließlich lokal. Der Dienst darf nur localhost und den paperless-Host erreichen (systemd `IPAddressDeny`), auch keinen DNS-Server.
- Protokolle enthalten nur Metadaten, nie Text, Namen oder Beträge.
- Keine dauerhaften Kopien von Belegen auf der nopaper-VM.
- Der API-Benutzer in paperless hat keine Admin- und keine Löschrechte.
- Tests, Beispiele und Fehlerberichte dürfen keine echten Belege enthalten.

## Lizenz

GNU Affero General Public License, Version 3 oder später (`AGPL-3.0-or-later`). Siehe `LICENSE`.
