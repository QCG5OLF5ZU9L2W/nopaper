# Johm Kalender

Erweiterung des Mailarbeitsplatzes `nopaper-mail` für Arztpraxen: erkennt Terminanliegen in der
Eingangspost, schlägt freie Plätze aus einem Nextcloud-Kalender vor, reserviert sie beim Versand des
Angebots und bucht nach der Bestätigung der Patientin. Alles läuft lokal mit einem Sprachmodell über
Ollama.

**Stand:** in Erprobung an einer Bezugsinstallation. Fast alles ist nur mit Offline-Tests geprüft.
Entscheidungen und offene Punkte stehen in [`AGENTS.md`](AGENTS.md), Änderungen in
[`CHANGELOG.md`](CHANGELOG.md).

**Kein Medizinprodukt.** Die MFA prüft jeden Entwurf und jedes Angebot, bevor es hinausgeht.

## Voraussetzungen

- Der Mailarbeitsplatz `nopaper-mail` in der Fassung, deren Prüfsummen in `quelle/original.sha256`
  stehen. Er ist **nicht** Teil dieses Repositorys.
- Ein Nextcloud-Kalender mit Rastereinträgen, deren Titel frei bedeutet (z. B. `Termin:`), und ein
  Nextcloud-Benutzer mit Schreibrecht darauf.
- Ollama mit dem Modell aus der Konfiguration.

## Bauen und installieren

```
bash johm/werkzeuge/installer-bauen.sh
sudo ./johm/johm-kalender-install.sh --check     # prüft Konfiguration und Kalender, ändert nichts am Dienst
sudo ./johm/johm-kalender-install.sh             # installiert und schaltet um
sudo ./johm/johm-kalender-install.sh --rollback  # schaltet die Erweiterung ab
```

Beim ersten Lauf legt das Skript `/etc/nopaper-mail/kalender.json` aus der Beispielkonfiguration an.
Sie muss vor dem zweiten Lauf ausgefüllt werden.

## Konfiguration

`/etc/nopaper-mail/kalender.json`, Vorlage: [`quelle/calendar.example.json`](quelle/calendar.example.json).

| Schlüssel | Bedeutung |
|---|---|
| `mode` | `preview` zeigt Vorschläge, reserviert und sendet nichts mit Terminen. `live` reserviert beim Senden und wertet Antworten aus. |
| `hosts` | Hostnamen, unter denen der Mailarbeitsplatz im Browser erreichbar ist. |
| `fax_address` | Adresse eines Faxgateways, an die nie geantwortet wird. Leer, wenn es keines gibt. |
| `calendar_home`, `username`, `password_file` | Zugang zum Nextcloud-Kalender. |
| `calendars` | Kalender mit Pfad und Kürzel. `blocks_only` für einen Kalender, der nur Sperrhinweise enthält. |
| `free_titles` | Titel der Rastereinträge, die als frei gelten. |
| `doctors` | Je Ärztin oder Arzt: `name`, `rasters` (deren Rastertitel), `calendars` (Kürzel, falls kein Raster zugeordnet ist). |
| `appointment_types` | Terminarten mit `label` und `free_titles`. Optional `urgent: true` für die Akut-Terminart und `hold_hours` für eine kürzere Bestätigungsfrist. |
| `hold_hours` | Bestätigungsfrist in Stunden. Plätze, die früher beginnen, werden nicht angeboten. |
| `default_days`, `max_days_mfa` | Suchzeitraum und seine Obergrenze. |
| `global_blocks` | Muster für Sperrhinweise in Titel oder Beschreibung. |
| `extra_holidays` | Zusätzliche freie Tage, `JJJJ-MM-TT` oder jährlich `MM-TT`. |
| `request_pattern` | Stichwörter als zweites Netz der Erkennung. |
| `reply_folders` | Ordner, in denen Antworten auf Angebote gesucht werden. |
| `model`, `model_api` | Optional: Modellname und Adresse von Ollama. |

Die Lesemerkmale (Schwangerschaft, Dringlichkeit usw.) stehen in `/etc/nopaper-mail/lesehilfe.json`
und können von der Praxis angepasst werden. Das Merkmal `pregnancy` muss bleiben.

## Bedienung in Kürze

- Mail öffnen: Übersetzung, Merkmale und passende Termine erscheinen.
- Plätze anklicken: Sie stehen im Entwurf. „Jetzt senden“ reserviert sie im Kalender.
- Im Chat: „suche Termine bei Dr. … nächste Woche vormittags“ sucht nach Vorgabe der MFA.
- Absage: im Nextcloud-Kalender „Absage“ in den Titel des Termins schreiben.

## Tests

```
python3 johm/tests/test_johm.py
```

Die Tests laufen ohne Netz und verwenden nur erfundene Daten. Eine Seite mit 37 Testmails für die
Erprobung am echten System baut `python3 johm/werkzeuge/testmails-bauen.py`.

## Lizenz

GNU AGPL, Version 3 oder später, wie das übrige Repository.
