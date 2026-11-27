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
| J1 | **Die MFA entscheidet und sendet.** Kein Angebot geht ohne ihren Klick hinaus. Selbstständig handelt John nur bei der Bestätigung (J12), bei der Absage auf seine Buchungsbestätigung (J31) und beim Fristablauf. |
| J2 | **Keine Praxisdaten im Code.** Hostnamen, Faxadresse, Nextcloud-Adresse, Postfächer, Kalender, Raster, Terminarten, Ärztinnen und Ärzte stehen in `/etc/nopaper-mail/kalender.json`. Der Dienst liest sie über den systemd-Credential `nc-config`. Die Beispielkonfiguration enthält nur Platzhalter. Das Installationsskript übernimmt `hosts` und `fax_address` einmalig aus einer älteren Installation. |
| J3 | **Erkennung nach Bedeutung.** Der Leseschritt (`reading.interpret`), der ohnehin für jede geöffnete Mail läuft, meldet das Terminanliegen (`none`, `new`, `change`, `cancel`, `uncertain`). Alles außer einem klaren `none` löst die Terminerkennung aus. Das Stichwortmuster `request_pattern` ist nur das zweite Netz. |
| J4 | **Schwangerschaft sperrt jeden Vorschlag**, auch bei einer Suche der MFA. Die Vergabe bleibt Handarbeit. Das Merkmal kommt aus der Lesehilfe und zusätzlich aus einer festen Regel für die deutsche Eigenangabe. |
| J5 | **Zeitwünsche als Felder, nie geraten.** Das Modell liefert Wochentage, Datum, Zeitraum (`datum_von`/`datum_bis`), Uhrzeitfenster, Ärztin oder Arzt und – bei Verlegung – den bisherigen Termin. Der Code prüft jedes Feld. Was sich nicht abbilden lässt (`zeit_sonstig`) oder ungültig ist, führt zur manuellen Vergabe. Ein errechnetes Datum, das dem genannten Wochentag widerspricht, wird verworfen. |
| J6 | **Reihenfolge der Vorschläge:** erst genau wie gewünscht; war der Wochentag nur eine Vorliebe, danach die übrigen Tage im Wunschzeitraum. Ist dort nichts frei, die nächsten freien Plätze ab dem Wunschtag, die dem Wunsch nächsten zuerst (Wochentag und Tageszeit, dann Tageszeit, dann Wochentag, dann alles). Ärztin oder Arzt und frühester Tag bleiben bindend. |
| J7 | **Wessen Platz es ist, entscheidet das Raster**, nicht der Kalender (`doctors[].rasters`). Der Kalender zählt nur, wo kein Raster zugeordnet ist. Anlass: Raster zweier Ärztinnen im selben Zeitfenster wurden derselben zugerechnet. |
| J8 | **Ohne genannten Anlass gibt es trotzdem Vorschläge** (Terminart `allgemein` aus allen `free_titles`). Ein ausdrücklich genannter anderer Anlass bleibt manuell – außer bei Verlegungen: Dort hat die Praxis den Anlass schon beim ersten Termin geprüft. |
| J9 | **Verlegung** (geändert 2026-11-27 auf Anweisung des Verantwortlichen: sonst blockiert eine Patientin zwei Plätze). John sucht den bisherigen Termin (J28) und zeigt die Treffer der MFA zur Auswahl; ein eindeutiger Treffer ist vorgewählt, „keinen freigeben“ ist immer möglich. **Der gewählte Termin wird freigegeben, sobald das Angebot mit den neuen Plätzen versendet ist** – nach dem Versand, nicht davor, und nur, wenn der Eintrag seit der Suche unverändert ist. Das Angebot sagt der Patientin, dass der bisherige Termin freigegeben ist. Die Wahl aus dem Browser wird gegen die neu berechnete Trefferliste geprüft. Scheitert die Freigabe, bekommt die MFA einen Hinweis. Endet das Angebot ohne Buchung (abgelehnt oder Frist abgelaufen), meldet John der MFA, dass die Patientin womöglich keinen Termin mehr hat. Angebote, die vor dieser Änderung versendet wurden, geben den alten Termin weiterhin erst bei der Bestätigung frei. |
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
| J22 | **Journal:** je Angebot Code, Termin, Anlass, Empfängeradresse, Frist, Status; dazu ein Verlauf jedes Statuswechsels (nur Code und Status, 90 Tage) und Hinweise für die MFA. Angebote werden nach sieben Tagen gelöscht; ein gebuchter Termin bleibt samt Kennung seiner Bestätigungsmail bis zum Tag nach dem Termin bekannt, damit eine Absage zugeordnet werden kann (J31). Bereits angebotene Plätze werden derselben Adresse nicht erneut vorgeschlagen. |
| J23 | **Kalender-Werkzeug für den Chat:** nur lesend (`/intern/kalender`), nur vom Rechner selbst, nicht über den Reverse-Proxy, mit Schlüssel, nur für Konten mit freigeschaltetem Postfach. Es zeigt auf Wunsch auch belegte Termine mit Namen – das Modell läuft lokal. |
| J24 | **Feste Texte in fremden Sprachen** übersetzt das Modell nach Schema. Ist die Übersetzung nach zwei Versuchen unbrauchbar, gelten die englischen Texte, und das Terminfeld sagt es. |
| J25 | **Fehlermeldungen nennen die Ursache** (Modell oder Kalender). Protokolle enthalten nur die Art des Fehlers, nie Mailtext, Namen oder Termine. |
| J26 | **Bedienung:** Enter sendet die Frage im Chat, Umschalt+Enter neue Zeile, Strg+Enter schlägt eine Antwort vor. Die Statuszeile bleibt beim Scrollen sichtbar und zeigt aktive Reservierungen. |
| J27 | **Installation:** jede Fassung in ein eigenes Verzeichnis unter `/usr/local/lib/johm-kalender`, Umschalten über Drop-ins, Rückfall bei fehlgeschlagener Startprüfung. Behalten werden die aktuelle Fassung und die zwei davor. |
| J28 | **Bisherigen Termin finden.** Mit Datum in der Mail: vergebene Plätze dieses Tages (und der Uhrzeit, falls genannt), abgeglichen mit dem Namen aus dem Absender und einem in der Mail genannten Namen. Ohne Datum, oder wenn am genannten Tag nichts passt: die Kalender von heute bis sechs Monate voraus, in drei Abfragen. **Vorgewählt** wird nur ein eindeutiger Treffer: mit Datum ein einziger Eintrag mit dem Namen, ohne Datum ein einziger Eintrag mit zwei Namensteilen (Vor- und Nachname). Ein einzelner Namensteil ist nur ein Hinweis und wird nie vorgewählt; mehr als acht solche Treffer werden nicht angezeigt. Namensteile zählen nur als ganze Wörter. Das Ergebnis wird je Mail zwei Minuten gemerkt. |
| J29 | **Farben aus Nextcloud.** Jeder vorgeschlagene Platz trägt in der Wochenansicht die Farbe seines Kalendereintrags (`COLOR`), sonst die Farbe seines Kalenders (zehn Minuten gemerkt). Die Farbe ist nur Anzeige: Sie gehört nicht zur Kennung eines Platzes, und ein Fehler beim Lesen der Kalenderfarbe hält die Suche nicht auf. Weitergegeben werden nur `#hex` oder ein einfacher Farbname; gesetzt wird sie im Browser als Stileigenschaft, nie als Markup. |
| J30 | **Akut in der Akut-Farbe.** Erkennt John ein dringendes Anliegen (J17), bekommt der Hinweis oben die Farbe, die die Akut-Raster im Kalender haben; die Akutplätze tragen sie ohnehin (J29). Die Farbe wird gelesen, nicht konfiguriert. Ist im Suchzeitraum kein Akut-Raster frei, bleibt der Hinweis in der Standardfarbe. |
| J31 | **Absage als Antwort auf die Buchungsbestätigung wird selbstständig bearbeitet** (Auftrag vom 2026-11-27; zweite Ausnahme von J1 neben J12). Bedingungen, alle im Code geprüft: Die Mail bezieht sich im Kopf auf Johns Buchungsbestätigung (`In-Reply-To`/`References`), kommt von der Adresse, an die das Angebot ging, ist keine automatische Antwort, und der Termin liegt in der Zukunft. Das Modell liest nur den eigenen Text der Antwort (ohne Zitat) und muss „absagen“ mit einem wörtlichen Beleg daraus liefern; verschieben, Rückfragen, bedingte oder unklare Absagen bleiben gewöhnliche Post für die MFA. Freigegeben wird nur der Termin genau dieser Bestätigung und nur, wenn der Kalendereintrag noch so aussieht, wie John ihn gebucht hat (Raster, Name, Zeit); Telefon- und SMS-Vermerk entfallen dabei. Die Patientin bekommt eine kurze Bestätigung, die MFA einen Hinweis. Enthält die Bestätigung mehrere Termine oder wurde der Eintrag geändert: nur Hinweis an die MFA. Jede Antwort wird einmal gelesen; ist das Modell nicht erreichbar, später erneut. Geprüft wird höchstens alle zwei Minuten, in der Post der letzten drei Tage. Gilt nur für Buchungen ab dieser Fassung. |

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
Termins beim Senden und die Suche danach (J9, J28), Farben (J29, J30), Absage per Mail (J31), Buchung über Mail-Link und freie Antwort (J12), Dringlichkeit (J17), Wochenansicht im
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

Die Aufträge vom 2026-11-27 sind mit 0.2.0 und 0.3.0 gebaut (J9, J28–J31). Offen:

1. **SMS-Erinnerung über einen eigenen LTE-Router** (Auftrag vom 2026-11-27). Vorhandenes Gerät: Teltonika RUT955 mit SIM-Karte im Praxisnetz. Bisher ist „SMS-Erinnerung: ja“ nur ein Vermerk im Termin; John verschickt keine SMS.
   - **Weg:** John spricht den Router direkt im lokalen Netz an, kein SMS-Dienst im Internet, kein weiterer Auftragsverarbeiter. Verschlüsselt, mit hinterlegtem Zertifikat des Routers, Prüfung nie abgeschaltet. Zugangsdaten in einer eigenen Datei unter `/etc/nopaper-mail`, als systemd-Credential geladen, nie in der Konfiguration oder im Protokoll.
   - **Schnittstelle hängt von der Firmware ab:** bis 6.x der Post/Get-Aufruf `cgi-bin/sms_send` (Kennwort in der Adresse – nur wenn nichts anderes geht); ab 7.x Anmeldung über `/api/login` mit Token und Versand über `/api/messages/actions/send` mit Modem-Kennung. Der Post/Get-Weg entfällt bei Teltonika ab 7.14.
   - **Vorschlag, noch zu bestätigen:** Erinnerung am Vortag um 10 Uhr, für Montage am Freitag; für jeden Termin mit „SMS-Erinnerung: ja“ und einer Mobilnummer in der Beschreibung, gleich ob von John oder der MFA eingetragen; Text nur mit Datum, Uhrzeit und Praxisname (aus der Konfiguration), kein Anlass, kein Arztname; je Termin genau einmal, im Journal vermerkt; Hinweis an die MFA, wenn eine SMS nicht hinausging; Antworten per SMS vorerst nicht ausgewertet, der Text verweist für Absagen auf Mail oder Telefon.
   - **Vor dem Bauen zu klären:** Firmware-Version des Routers; seine Adresse und ob `llm` ihn erreicht (Netzfreigabe des Dienstes); ob die SIM-Karte SMS versenden darf; Zeitpunkt und Text der Erinnerung; ob SMS-Antworten ausgewertet werden sollen. Eine nachgereichte Mobilnummer (Punkt 2) ist Voraussetzung für viele Erinnerungen.
2. **Nachgereichte Telefonnummer** (vorgeschlagen, nicht beauftragt): Antwort auf die Buchungsbestätigung mit Telefonnummer und SMS-Wunsch in den gebuchten Termin eintragen, wie in J31 nur mit Bezug im Mailkopf, von der Adresse des Angebots und mit der Nummer wörtlich in der Mail.
