# Änderungen – Johm Kalender

Neueste Fassung zuerst. Die Nummern J1–J34 verweisen auf die Entscheidungen in `AGENTS.md`.

## 0.5.2 (2026-11-27)

- **Termine aus dem Chat entfernen** (J34): Kalender-Werkzeug 3.1 mit `termin_entfernen`, Bestätigungsdialog, neuer Aufruf `POST /intern/kalender/entfernen`. Nur offline geprüft.

## 0.5.1 (2026-11-27)

- **Eintragen aus dem Chat in jedes freie Raster** (J33): Bisher nur in die Raster aus `free_titles`; ein freies Schwangeren-Raster galt als belegt. Die Liste freier Zeiten nennt jetzt das Raster mit.

## 0.5.0 (2026-11-27)

Auf Anweisung des Verantwortlichen. Offline geprüft, nicht an einem echten Open WebUI.

- **Termine aus dem Chat eintragen** (J33): Kalender-Werkzeug 3.0 mit `termin_eintragen`, Bestätigungsdialog in Open WebUI, neuer Aufruf `POST /intern/kalender/eintragen` im Mailarbeitsplatz.

## 0.4.1 (2026-11-27)

- **Kalender-Werkzeug für Open WebUI 2.0** (J23): Filter nach Ärztin oder Arzt, Zeitangaben wie „übernächste Woche“ werden im Werkzeug umgerechnet, kompakte Ausgabe je Tag. Der Mailarbeitsplatz liefert dafür strukturierte Zeilen und das heutige Datum. Nicht an einem echten Open WebUI erprobt.

## 0.4.0 (2026-11-27)

Auf Anweisung des Verantwortlichen. Offline und mit nachgestellter Oberfläche geprüft.

- **Weiterleiten im Mailarbeitsplatz** an beliebige Adressen, mit der Originalmail samt Anhängen (J32). Neuer optionaler Konfigurationsschlüssel `forward_recipients` für Empfänger als Knöpfe.

## 0.3.0 (2026-11-27)

Auf Anweisung des Verantwortlichen. Nur offline geprüft.

- **Absage als Antwort auf die Buchungsbestätigung** gibt den Termin selbstständig frei, bestätigt der Patientin die Absage und meldet sie der MFA (J31). Verschieben, Rückfragen und Unklares bleiben bei der MFA.
- **Gebuchte Termine bleiben im Journal bis zum Tag nach dem Termin** statt sieben Tage (J22), mit der Kennung der Bestätigungsmail.

## 0.2.0 (2026-11-27)

Auf Anweisung des Verantwortlichen. Nur offline und mit nachgestellter Oberfläche geprüft.

- **Verlegung gibt den bisherigen Termin beim Senden des Angebots frei** statt erst bei der Bestätigung (J9 geändert). Die MFA wählt den Termin aus den Treffern oder „keinen freigeben“. Das Angebot nennt der Patientin die Freigabe. Endet das Angebot ohne Buchung, bekommt die MFA einen Hinweis.
- **Bisheriger Termin ohne Datum:** Suche über sechs Monate nach dem Namen der Patientin (J28). Sie greift auch, wenn am genannten Tag nichts passt.
- **Farben aus Nextcloud** an den Plätzen der Wochenansicht (J29); **dringende Anliegen** in der Farbe der Akut-Raster (J30).
- **Installationsskript:** Die Meldung des ersten, zu frühen Versuchs der Startprüfung entfällt.
- Namensteile zählen bei der Suche nach dem bisherigen Termin nur noch als ganze Wörter.

## 0.1.0 (2026-11-27)

Erste Fassung im Repository. Sie fasst die Arbeit vom 2026-11-26 und 2026-11-27 zusammen. Geprüft
mit Offline-Tests und nachgestellten Daten; was am echten System bestätigt ist, steht in `AGENTS.md`
Abschnitt 6.

### Erkennung

- **Terminanliegen nach Bedeutung** (J3): Der Leseschritt meldet das Anliegen in jeder Sprache, auch ohne das Wort „Termin“ und als kurze Antwort in einem laufenden Vorgang. Bisher entschied ein Stichwortmuster, ob das Modell überhaupt gefragt wurde.
- **Zeitwünsche** (J5): Wochentag, Datum, Zeitraum, Uhrzeitfenster, „vormittags“/„nachmittags“, bevorzugter oder fester Wochentag, Ärztin oder Arzt.
- **Abgelehnte Angebote** gelten als fortbestehender Terminwunsch; die abgelehnten Plätze werden nicht erneut vorgeschlagen (J22).
- **Verlegungen** bearbeitet John selbst, mit Suche des bisherigen Termins (J9). Absagen bleiben bei der MFA.
- **Dringlichkeit** als neues Lesemerkmal mit wörtlichem Beleg; wählt die Akut-Terminart (J17). Das Installationsskript ergänzt das Merkmal in einer vorhandenen Lesehilfe.
- Die Terminerkennung arbeitet mit festem JSON-Schema und wird je Mail 90 Sekunden gemerkt (J19).

### Suche und Vorschläge

- **Reihenfolge nach Nähe zum Wunsch**, auch bei den Ausweichterminen (J6).
- **Ärztin oder Arzt über das Raster** statt über den Kalender (J7).
- **Ohne genannten Anlass** gibt es Vorschläge aus allen freien Rastern (J8).
- **Länge eines Platzes** kommt aus dem Kalendereintrag; der feste Wert `slot_minutes` gilt nicht mehr als Bedingung.
- **Feiertage** sind ausgenommen (J18).
- **Suche der MFA im Chat** mit Ärztin, Zeitraum, Wochentag, Tageszeit, Terminart; auch jenseits des Suchzeitraums, höchstens drei Monate je Abfrage (J16).
- **Wochenansicht** statt langer Liste, vollständig bis 400 Plätze (J20).

### Angebot und Bestätigung

- **Mail-Link** je Termin und „Keiner dieser Termine passt“; Auswertung ohne Modell (J12). Das Angebot geht dafür zusätzlich als HTML-Fassung hinaus.
- **Freie Antworten** liest das Modell, der Code prüft nach; Unklares geht sofort an die MFA (J12).
- **Bestätigte Verlegung** gibt den bisherigen Termin frei und sagt es der Patientin in der Buchungsbestätigung (J9).
- **Angebotstext** ohne Codes und Befehlszeilen, mit Wochentag und Grußformel (J15).
- **Antwortentwurf** sagt keine Termine mehr zu und richtet sich nach dem geprüften Kalenderstand (J13).
- **Anrede** ohne Platzhalter (J14).

### Kalender

- **Reservierung lesbar** mit Name, Anlass und Frist im Titel (J11).
- **Absage** durch ein Wort im Titel des Kalendereintrags (J10).
- **Verlauf** jedes Statuswechsels im Journal und Hinweise für die MFA (J22).

### Betrieb

- **Keine Praxisdaten mehr im Code** (J2); neue Schlüssel `hosts`, `fax_address`, `doctors`, `extra_holidays`, optional `model`, `model_api`, je Terminart `urgent` und `hold_hours`.
- **Eine Kontextgröße** für alle Modellaufrufe (J19). Anlass: Das Modell wurde bei jedem Wechsel neu geladen, Aufrufe liefen in die Zeitgrenze.
- **Übersetzung fester Texte** mit Schema, zweitem Versuch und englischem Ersatz (J24). Anlass: Eine arabische Mail brachte die ganze Kalendersuche zum Abbruch.
- **Fehlermeldungen** nennen Modell oder Kalender als Ursache (J25).
- **Kalender-Werkzeug für den Chat**, nur lesend (J23).
- **Bedienung:** Enter sendet, Statuszeile bleibt sichtbar und zeigt aktive Reservierungen (J26).
- **Alte Fassungen** werden bei der Installation bis auf drei entfernt (J27).

### Behobene Fehler aus der Entstehung

- Das Aufräumen des Verlaufs lief bei jedem Öffnen des Journals und konnte die Datenbank kurz sperren.
- Eine Suche der MFA behielt den Wochentag aus der Mail.
- Raster ohne Leerzeichen nach dem Doppelpunkt wurden bei Absage und Freigabe nicht erkannt.
- Plätze zweier Ärztinnen im selben Zeitfenster wurden derselben zugerechnet.
