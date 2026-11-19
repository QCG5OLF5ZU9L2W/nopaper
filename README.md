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

## Installation

Als root im entpackten Verzeichnis:

```bash
PAPERLESS_URL=https://paperless.example.org EMPFAENGER="Muster GmbH" bash install.sh
```

Das Skript installiert die Pakete, Ollama mit den Modellen aus der Konfiguration (mehrere GB) und nopaper als systemd-Dienst. Der Dienst wird noch nicht gestartet. Ollama wird nach dem Laden der Modelle auf localhost beschränkt.

Ein selbstentpackender Installer lässt sich mit `werkzeuge/installer-bauen.sh` erzeugen.

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

## Die drei Wege

| Weg | Auslöser | Was nopaper tut |
|---|---|---|
| Scanner | PDF in `eingang/rechnungen` oder `eingang/dokumente` | einordnen, auslesen, an paperless übergeben, Felder, Titel, Sidecar |
| Nachbearbeitung | Tag aus `tags.ausloeser_nachbearbeitung` am Dokument | als Rechnung auslesen, Felder, Titel, Tags, Sidecar; Tag entfernen |
| Volltext | Tag aus `tags.ausloeser_text` am Dokument | Text neu erkennen und glätten, nur den Volltext ersetzen; Tag entfernen |

Der Volltext wird nur ersetzt, wenn der neue Text nicht schlechter ist als der vorhandene.

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
