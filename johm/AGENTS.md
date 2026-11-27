# AGENTS.md – Johm Kalender

Diese Datei hält fest, **was** die Kalender-Erweiterung des Mailarbeitsplatzes tut und **welche
Entscheidungen gefallen sind**. Wer daran arbeitet (Mensch oder Coding-Agent), hält sich daran.
Abweichungen nur nach Rücksprache mit dem Projektverantwortlichen. Für das ganze Repository gilt
zusätzlich die `AGENTS.md` im Wurzelverzeichnis, vor allem deren Abschnitte 2 (Rahmenbedingungen)
und 9 (Sicherheit und Datenschutz).

Stand: 2026-11-27. Entstanden an zwei Arbeitstagen an einer Bezugsinstallation. **Geprüft ist fast
alles nur mit Offline-Tests und nachgestellten Daten**; was am echten System bestätigt wurde, steht
in Abschnitt 6.

---

## 1. Ziel

Der Mailarbeitsplatz („John“) zeigt einer MFA die Eingangspost eines Praxispostfachs, übersetzt sie
und entwirft Antworten mit einem lokalen Sprachmodell. Diese Erweiterung fügt hinzu:

- **Terminanliegen erkennen** – in jeder Sprache, auch ohne das Wort „Termin“.
- **Freie Plätze aus dem Nextcloud-Kalender vorschlagen**, passend zu Wunsch, Ärztin oder Arzt und Anlass.
- **Angebot versenden und Plätze reservieren**, Bestätigung der Patientin auswerten, buchen, freigeben.
- **Verlegungen und Absagen** bearbeiten.

Die MFA entscheidet und sendet. Das Modell bereitet vor.

## 2. Rahmenbedingungen

| Punkt | Vorgabe |
|---|---|
| Daten | Gesundheitsdaten nach Art. 9 DSGVO. Verarbeitung nur lokal: Mailserver, Nextcloud und Sprachmodell (Ollama) der Praxis. Kein Cloud-Dienst. |
| Basis | Die Erweiterung setzt den Mailarbeitsplatz `nopaper-mail` voraus und **ersetzt** dessen `desk.py`, `mail.py` und `outbound.py` über systemd-Drop-ins. Die Basis liegt **nicht** in diesem Repository (siehe 7). |
| Bibliotheken | Wie die Basis: `aiohttp`, `imapclient`, `icalendar` aus den Paketen der Distribution. Das weicht von „nur Standardbibliothek“ der Wurzel-`AGENTS.md` ab und ist vom Bestand übernommen. Neue Abhängigkeiten nur nach Rücksprache. |
| Kein Medizinprodukt | Die Erweiterung trifft keine medizinische Entscheidung. Merkmale wie „Dringend“ sind Lesehilfen für die MFA. |

## 3. Entscheidungen

| # | Entscheidung |
|---|---|
| J1 | **Die MFA entscheidet und sendet.** Kein Angebot geht ohne ihren Klick hinaus. Selbstständig handelt John nur bei der Bestätigung (J12) und beim Fristablauf. |
| J2 | **Keine Praxisdaten im Code.** Hostnamen, Faxadresse, Nextcloud-Adresse, Postfächer, Kalender, Raster, Terminarten, Ärztinnen und Ärzte stehen in `/etc/nopaper-mail/kalender.json`. Der Dienst liest sie über den systemd-Credential `nc-config`. Die Beispielkonfiguration enthält nur Platzhalter. Das Installationsskript übernimmt `hosts` und `fax_address` einmalig aus einer älteren Installation. |
| J3 | **Erkennung nach Bedeutung.** Der Leseschritt (`reading.interpret`), der ohnehin für jede geöffnete Mail läuft, meldet das Terminanliegen (`none`, `new`, `change`, `cancel`, `uncertain`). Alles außer einem klaren `none` löst die Terminerkennung aus. Das Stichwortmuster `request_pattern` ist nur das zweite Netz. |
| J4 | **Schwangerschaft sperrt jeden Vorschlag**, auch bei einer Suche der MFA. Die Vergabe bleibt Handarbeit. Das Merkmal kommt aus der Lesehilfe und zusätzlich aus einer festen Regel für die deutsche Eigenangabe. |
| J5 | **Zeitwünsche als Felder, nie geraten.** Das Modell liefert Wochentage, Datum, Zeitraum (`datum_von`/`datum_bis`), Uhrzeitfenster, Ärztin oder Arzt und – bei Verlegung – den bisherigen Termin. Der Code prüft jedes Feld. Was sich nicht abbilden lässt (`zeit_sonstig`) oder ungültig ist, führt zur manuellen Vergabe. Ein errechnetes Datum, das dem genannten Wochentag widerspricht, wird verworfen. |
| J6 | **Reihenfolge der Vorschläge:** erst genau wie gewünscht; war der Wochentag nur eine Vorliebe, danach die übrigen Tage im Wunschzeitraum. Ist dort nichts frei, die nächsten freien Plätze ab dem Wunschtag, die dem Wunsch nächsten zuerst (Wochentag und Tageszeit, dann Tageszeit, dann Wochentag, dann alles). Ärztin oder Arzt und frühester Tag bleiben bindend. |
| J7 | **Wessen Platz es ist, entscheidet das Raster**, nicht der Kalender (`doctors[].rasters`). Der Kalender zählt nur, wo kein Raster zugeordnet ist. Anlass: Raster zweier Ärztinnen im selben Zeitfenster wurden derselben zugerechnet. |
| J8 | **Ohne genannten Anlass gibt es trotzdem Vorschläge** (Terminart `allgemein` aus allen `free_titles`). Ein ausdrücklich genannter anderer Anlass bleibt manuell – außer bei Verlegungen: Dort hat die Praxis den Anlass schon beim ersten Termin geprüft. |
| J9 | **Verlegung:** John sucht den bisherigen Termin über Datum, Uhrzeit und den Namen der Absenderin. Er wird erst freigegeben, **wenn die Patientin den neuen Termin bestätigt**, nur bei Übereinstimmung des Namens und nur, wenn der Eintrag seitdem unverändert ist. Sonst ein Hinweis an die MFA. |
| J10 | **Absage im Kalender:** Schreibt die MFA „Absage“, „abgesagt“, „Storno“ oder „storniert“ in den Titel, setzt John den Eintrag auf sein Raster zurück und entfernt Telefon und SMS-Vermerk. Gelesen werden nur seit dem letzten Abgleich geänderte Einträge (WebDAV `sync-collection`). Serien werden nie angefasst. Die Patientin wird nicht benachrichtigt. |
| J11 | **Reservierung ist im Kalender sichtbar:** Titel `RESERVIERT für <Name> · <Anlass> · bis <Frist> · <Raster>`. Name ist der von der MFA eingetragene, sonst der Absendername, sonst die Mailadresse. Nie ein Code. |
| J12 | **Bestätigung in zwei Wegen.** (a) **Mail-Link:** Das Angebot enthält je Termin einen Link, der eine vorbereitete Mail öffnet. Die Entscheidung steht im Betreff (`[OK <Code>]`, `[NO <Code> …]`) und wird ohne Modell ausgewertet, nur von der Adresse, an die das Angebot ging. (b) **Freie Antwort:** Das Modell ordnet zu; der Code lässt nur Codes dieses Angebots zu, höchstens eine Zusage je Anlass, Name und Telefon nur, wenn sie wörtlich in der Antwort stehen. **Unklares geht sofort an die MFA**, nicht erst bei Fristablauf. Jede Antwort wird genau einmal gelesen (`reply_done`). |
| J13 | **Der Antwortentwurf sagt nie einen Termin zu.** Er nennt selbst keine Daten oder Uhrzeiten und knüpft keine Bedingungen; die Terminliste fügt die Anwendung ein. Vor dem Formulieren läuft die Kalendersuche, ihr Ergebnis ist Vorgabe für das Modell. Das ist eine Anweisung an das Modell, keine technische Sperre – die MFA liest gegen. |
| J14 | **Anrede:** mit Namen nur, wenn er aus dem Absender oder dem Entwurf bekannt ist; sonst die neutrale Anrede. Kein Platzhalter. |
| J15 | **Angebotstext für Menschen:** Wochentag und Datum, kein Code, keine Befehlszeile, Grußformel. Bei einer Person entfällt die Rollenbezeichnung. In der Mail steht der Name der Ärztin oder des Arztes statt des Kalenderkürzels. |
| J16 | **Die Suche der MFA im Chat geht vor.** Bittet sie John um eine Terminsuche, sucht er auch bei manueller Einstufung und ersetzt den Zeitwunsch aus der Mail vollständig durch ihre Vorgaben (Ärztin, Zeitraum, Wochentag, Tageszeit, Terminart). „Spätere“ und „frühere“ beziehen sich auf die angezeigten Vorschläge. Ausnahme bleibt J4. Vorgaben werden vollständig geprüft, nichts aus dem Browser wird ungeprüft übernommen. |
| J17 | **Dringlichkeit ist ein Lesemerkmal** (`urgent` in der Lesehilfe, mit wörtlichem Beleg). Es wird der MFA angezeigt und wählt die als `urgent` markierte Terminart mit den frühesten Plätzen und eigener, kürzerer Bestätigungsfrist (`hold_hours` der Terminart). Kein Treffer ist keine medizinische Aussage. |
| J18 | **Feiertage:** Bundesweite Feiertage sind keine freien Plätze. Weitere Tage über `extra_holidays` (`JJJJ-MM-TT` oder jährlich `MM-TT`). |
| J19 | **Eine Kontextgröße für alle Modellaufrufe** (32768). Verschiedene Größen ließen Ollama das Modell bei jedem Wechsel neu laden. Die Terminerkennung einer Mail wird 90 Sekunden gemerkt. |
| J20 | **Wochenansicht:** Wochentage in festen Spalten, vormittags über nachmittags, eine Woche offen, die übrigen eingeklappt mit Anzahl. Oben die dem Wunsch nächsten Plätze, je einer pro Tag. Filter für Ärztin und Tageszeit ohne neue Suche. Bis zu 400 Plätze je Anliegen. |
| J21 | **Der Kalender bleibt die Wahrheit.** John schreibt nur mit `If-Match` auf den zuvor gelesenen Stand, prüft Reservierungen vor dem Buchen über Code und Fingerabdruck und fällt bei jeder Abweichung auf „manuell prüfen“. Das Journal ist Buchführung, kein zweiter Bestand. |
| J22 | **Journal:** je Angebot Code, Termin, Anlass, Empfängeradresse, Frist, Status; dazu ein Verlauf jedes Statuswechsels (nur Code und Status, 90 Tage) und Hinweise für die MFA. Angebote werden nach sieben Tagen gelöscht. Bereits angebotene Plätze werden derselben Adresse nicht erneut vorgeschlagen. |
| J23 | **Kalender-Werkzeug für den Chat:** nur lesend (`/intern/kalender`), nur vom Rechner selbst, nicht über den Reverse-Proxy, mit Schlüssel, nur für Konten mit freigeschaltetem Postfach. Es zeigt auf Wunsch auch belegte Termine mit Namen – das Modell läuft lokal. |
| J24 | **Feste Texte in fremden Sprachen** übersetzt das Modell nach Schema. Ist die Übersetzung nach zwei Versuchen unbrauchbar, gelten die englischen Texte, und das Terminfeld sagt es. |
| J25 | **Fehlermeldungen nennen die Ursache** (Modell oder Kalender). Protokolle enthalten nur die Art des Fehlers, nie Mailtext, Namen oder Termine. |
| J26 | **Bedienung:** Enter sendet die Frage im Chat, Umschalt+Enter neue Zeile, Strg+Enter schlägt eine Antwort vor. Die Statuszeile bleibt beim Scrollen sichtbar und zeigt aktive Reservierungen. |
| J27 | **Installation:** jede Fassung in ein eigenes Verzeichnis unter `/usr/local/lib/johm-kalender`, Umschalten über Drop-ins, Rückfall bei fehlgeschlagener Startprüfung. Behalten werden die aktuelle Fassung und die zwei davor. |

## 4. Aufbau

| Pfad | Inhalt |
|---|---|
| `quelle/reading.py` | Sprache, Übersetzung, Merkmale mit wörtlichem Beleg, Terminanliegen |
| `quelle/wording.py` | feste Texte, Übersetzung, Anrede |
| `quelle/appointments.py` | Terminwünsche, Suche, Angebot, Reservierung, Auswertung der Antworten, Journal |
| `quelle/caldav_slots.py` | CalDAV: freie Plätze, Reservieren, Buchen, Freigeben, Absage, Feiertage |
| `quelle/desk.py`, `mail.py`, `outbound.py` | Mailarbeitsplatz, Entwurfsdienst, Versand – ersetzen die gleichnamigen Dateien der Basis |
| `quelle/app.js`, `index.html`, `style.css` | Oberfläche |
| `quelle/probe.py` | Prüfung der Konfiguration und der Kalenderrechte bei der Installation |
| `quelle/calendar.example.json`, `lesehilfe.json` | Beispielkonfiguration, Lesemerkmale |
| `quelle/original.sha256` | Prüfsummen der Basisfassung, auf der diese Erweiterung aufsetzt |
| `installer/` | Kopf und Fuß des Installationsskripts, Reihenfolge der Dateien |
| `werkzeuge/installer-bauen.sh` | baut `johm-kalender-install.sh` |
| `werkzeuge/johm_kalender_tool.py` | Werkzeug für Open WebUI (J23) |
| `werkzeuge/testmails-bauen.py` | Seite mit 37 erfundenen Testmails |
| `tests/test_johm.py` | Offline-Tests; `tests/fake/icalendar.py` ersetzt die Bibliothek nur im Test |

## 5. Arbeitsweise

- **Tests vor jeder Änderung und danach:** `python3 johm/tests/test_johm.py`. Sie laufen ohne Netz.
- **Nur erfundene Daten** in Code, Tests, Dokumentation, Issues. Kein Satz aus einer echten Mail, kein Bildschirmfoto aus dem Betrieb.
- **Jede Entscheidung bekommt eine Nummer** in Abschnitt 3 und einen Eintrag in `CHANGELOG.md`.
- **Was nicht am echten System geprüft ist, wird so benannt** – in der Entscheidung, im Changelog und gegenüber dem Verantwortlichen.
- **Anweisungen an das Modell sind keine Sicherung.** Was nicht passieren darf, prüft der Code (J5, J12, J16, J21).
- **Im Zweifel manuell.** Eine falsche Buchung ist schlimmer als eine Rückfrage an die MFA.
- Meldungen und Konfigurationsschlüssel deutsch, Bezeichner im Code dürfen englisch sein.

## 6. Am echten System bestätigt

Durch Rückmeldung des Verantwortlichen am 2026-11-26/27:

- Terminanliegen ohne das Wort „Termin“ wird erkannt, der Zeitwunsch gelesen.
- Angebot wird versendet, die Reservierung steht im Nextcloud-Kalender.
- Der Mail-Link öffnet im Mailprogramm die vorbereitete Mail.
- Absage angebotener Termine: die Plätze werden nicht erneut vorgeschlagen.
- Suche der MFA nach Ärztin und Zeitraum liefert Plätze.
- Verlegung: Wunsch und bisheriger Termin werden gelesen.

Alles Übrige ist nur offline geprüft, insbesondere: Abgleich der Absagen (J10), Freigabe des alten
Termins (J9), Buchung über Mail-Link und freie Antwort (J12), Dringlichkeit (J17), Wochenansicht im
Betrieb (J20), das Chat-Werkzeug (J23), die Migration der Konfiguration (J2).

## 7. Offene Punkte

1. **Die Basis `nopaper-mail` fehlt im Repository.** Ohne sie ist die Erweiterung nicht lauffähig; `original.sha256` bindet an genau eine Basisfassung. Aufzunehmen sind die Dateien aus `/usr/local/lib/nopaper-mail` und die systemd-Dateien, nie der Inhalt von `/etc/nopaper-mail`.
2. **Lizenzvermerk** (`SPDX-License-Identifier`) fehlt in den Dateien unter `quelle/`.
3. **Testbestand mit echten Formulierungen:** Die 37 Testmails sind erfunden. Ob das eingesetzte Modell sie wie erwartet einordnet, ist nicht gemessen.
4. **Weitere Raster** des Kalenders (z. B. für Online-Buchung) sind keiner Terminart zugeordnet; ihre Bedeutung ist zu klären.
5. **Regionale Feiertage** stehen nur in der Konfiguration.
6. **Dringlichkeit in der Postfachliste:** Das Merkmal entsteht erst beim Öffnen einer Mail.
7. **Terminwünsche in PDF- und Faxanhängen** werden nicht gelesen.
8. **Bestätigungsseite im Web** als dritter Weg neben J12: nur entworfen (getrennter Dienst, Johm holt ab, Einmal-Schlüssel, Buchung erst per Knopfdruck).
9. **Suche über das Postfach für den Chat** (Wortsuche über IMAP, später semantisch): nur entworfen. Ein Index mit Mailinhalten bräche mit dem Grundsatz, nichts davon auf die Platte zu schreiben.
10. **Chat-Werkzeug bei Open WebUI im Container:** `127.0.0.1` ist dann nicht der Rechner; Adresse und Prüfung wären anzupassen.
11. **Mehrere Postfächer mit Kalender:** Abgleich der Absagen und Journal sind je Postfach getrennt; ein Angebot aus Postfach A kennt der Abgleich von Postfach B nicht.

## 8. Für den nächsten Build

Vom Verantwortlichen am 2026-11-27 aufgetragen, noch nicht gebaut.

1. **Verlegung: bisherigen Termin sofort freigeben** (ändert J9). Grund: Sonst sind bis zur Bestätigung zwei Plätze für dieselbe Patientin blockiert. Vorgesehen: Freigabe in dem Moment, in dem die MFA das Angebot mit den neuen Plätzen sendet (J1), nicht schon beim Eingang der Mail. Die Bedingungen aus J9 bleiben (Name im Titel, Eintrag unverändert). Das Angebot muss der Patientin sagen, dass der bisherige Termin damit aufgehoben ist. **Zu klären:** was geschieht, wenn sie keinen der neuen Plätze bestätigt – sie hat dann keinen Termin mehr; mindestens ein Hinweis an die MFA bei Fristablauf.
2. **Farben aus Nextcloud in der Wochenansicht.** Jeder vorgeschlagene Platz erscheint in der Farbe seines Kalendereintrags (`COLOR` des Termins, sonst Farbe des Kalenders).
3. **Akut in der Akut-Farbe.** Erkennt John ein dringendes Anliegen (J17), werden Hinweis und Akutplätze in der Farbe dargestellt, die die Akut-Raster in Nextcloud haben. Die Farbe wird gelesen, nicht in der Konfiguration festgelegt.
4. **Installationsskript:** Die Meldung `curl: (7) Failed to connect` des ersten, zu frühen Versuchs der Startprüfung unterdrücken.
