# Änderungen

Neueste Fassung zuerst. Ältere Fassungen (1.0 bis 1.4.0) sind in `AGENTS.md` unter den Entscheidungen E1–E37 beschrieben.

## 1.4.10

- **Mandanten:** Auf derselben Maschine können mehrere Mandanten getrennt laufen. Jeder Mandant hat einen eigenen Dienst (`nopaper@<name>`), einen eigenen Systembenutzer, eigene Ordner unter `/srv/nopaper-<name>`, eine eigene Konfiguration unter `/etc/nopaper-mandanten/<name>` und ein eigenes paperless. Gemeinsam sind nur das Programm und die Sprachmodelle.
  - Anlegen: `nopaper-mandant anlegen <name>`, Übersicht: `nopaper-mandant liste`
  - Bedienen: `nopaper --mandant <name> check|once|status|test …`
  - Starten: `nopaper-aktivieren <name>`, Protokoll: `journalctl -u nopaper@<name> -f`
  - Die bisherige Installation bleibt unverändert als Standard-Instanz bestehen (`nopaper`, `/etc/nopaper`, `/srv/nopaper`).
- **Die Samba-Option des Installers entfällt.** Scanner werden künftig über WebDAV mit HTTPS angebunden (noch nicht enthalten).

## 1.4.9

- **Verwendungszweck für Überweisungen** (Feldschlüssel `verwendungszweck`, z. B. Feld `Erkannt: Verwendungszweck`): Der Wert muss wörtlich im Text stehen, sonst wird er verworfen (`VERWENDUNGSZWECK_VERWORFEN`). Fehlt er, übernimmt nopaper ein ausdrücklich bezeichnetes **Kassenzeichen** aus dem Text (`VERWENDUNGSZWECK_AUS_TEXT`), auch gesperrt gedruckt und auch, wenn die Nummer in der Folgezeile steht. Stehen verschiedene Nummern hinter „Kassenzeichen“, bleibt das Feld leer.

## 1.4.8

- **Dritter Weg – nur den Volltext erneuern:** Dokumente mit dem Tag aus `tags.ausloeser_text` (z. B. `totext`) werden neu erkannt und geglättet; nopaper schreibt **nur den Volltext** zurück und entfernt den Tag. Felder, Titel, übrige Tags, Besitzer bleiben, es entsteht kein Sidecar. Ersetzt wird nur, wenn der neue Text nicht schlechter ist (höchstens 10 % weniger lesbare Zeichen, Anteil lesbarer Zeichen höchstens 3 Punkte niedriger); sonst bleibt der alte Text (`TEXT_BEHALTEN`). Das Protokoll nennt nur die Kennzahlen, keinen Inhalt. Ohne den Schlüssel ist der Weg abgeschaltet.

## 1.4.7

- **Dateiname schon beim Upload:** Rechnungen werden unter dem Namen `Rechnungsdatum Lieferant Betrag.pdf` an paperless übergeben, nicht mehr unter dem Scanner-Namen (`img1.pdf`). Titel und Originaldateiname in paperless stimmen damit von Anfang an. Zeichen, die in Dateinamen nicht erlaubt sind, werden durch `_` ersetzt. Dokumente, die keine Rechnung sind, behalten den Namen aus dem Eingang.

## 1.4.6

- **Titel aus den Rechnungsdaten:** Rechnungen bekommen in paperless den Titel `Rechnungsdatum Lieferant Betrag`, z. B. `2026-11-05 Muster Tankstelle 60,58 EUR`, statt des Scan-Dateinamens. Einstellbar über `titel_rechnung` in der Konfiguration (Platzhalter `{datum}`, `{lieferant}`, `{betrag}`, `{nummer}`, `{belegart}`; leer = Titel nicht ändern). Gilt auch bei der Nachbearbeitung. Sonstige Dokumente behalten ihren Titel.

## 1.4.5

- **Dienst erreichte paperless nicht (`URLError`), obwohl `nopaper check` in Ordnung war:** Die Netzsperre des Dienstes lässt nur localhost und die IP von paperless zu, also auch keinen DNS-Server. `nopaper-aktivieren` trägt den Namen des paperless-Hosts deshalb fest in `/etc/hosts` ein (Zeilen mit `# nopaper-paperless`). Ändert sich die IP von paperless, `nopaper-aktivieren` erneut ausführen.

## 1.4.4

- **Belegnummer, bei der Bildmodell und Texterkennung sich in einer Ziffer widersprechen** (z. B. 919 gegen 319): Steht im Text vor der fast gleichen Nummer eine Belegbezeichnung, bleibt der Wert des Bildmodells erhalten (`RECHNUNGSNUMMER_ABWEICHEND`) und der Beleg geht über `RECHNUNGSNUMMER_NICHT_IM_TEXT` in die Prüfung. Bis 1.4.3 wurde die Nummer verworfen.

## 1.4.3

- **Prüfsumme des Originals:** paperless nennt je nach Version MD5 (32 Zeichen) oder SHA-256 (64 Zeichen). nopaper vergleicht mit dem passenden eigenen Wert. Bis 1.4.2 wurde immer MD5 erwartet (`INTEGRITAET_ORIGINAL` trotz unveränderter Datei).

## 1.4.2

- **paperless-ngx 3.x:** Das Aufgabenformat hat sich geändert (`status` klein geschrieben, `related_document_ids`, `result_data`). nopaper versteht beide Formate. Bis 1.4.1 wartete nopaper nach dem Upload stumm bis zu 30 Minuten.
- Die Warteschleife meldet alle 30 s den Stand und bricht nach 3 Minuten mit `PAPERLESS_AUFGABE_NICHT_GEFUNDEN` oder `PAPERLESS_AUFGABE_OHNE_DOKUMENT` ab, wenn paperless die Aufgabe nicht liefert oder „fertig“ ohne Dokument-ID meldet.
- `nopaper check` zeigt das Aufgabenformat und prüft auch das Extraktions- und das Glättungsmodell.

## 1.4.1

- **Rückgeld:** Ist „gegeben“ größer als der Endbetrag, gilt der Endbetrag als bezahlt, offen bleibt 0 (`RUECKGELD_ERKANNT`). Kassenbons und Bewirtungsbelege mit Zahlungsvermerk im Text gelten als bezahlt (`ZAHLUNG_AUS_TEXT`).
- **Belegnummer im Steuernummern-Format** wird nicht mehr verworfen, wenn eine Beleg-/Bon-/Rechnungsbezeichnung davorsteht.
- **Neue Feldschlüssel** in `felder`: `steuer_hoch` (Steuerbetrag zu Sätzen ab 10 %), `steuer_niedrig` (über 0, unter 10 %), `skonto` (Text `satz:frist:betrag`, Stufen mit `;` getrennt). Ohne Wert bleibt ein Feld leer.
- **Zahlungsziel:** Steht nur eine Frist in Tagen auf dem Beleg, wird das Datum aus dem Rechnungsdatum berechnet (`FAELLIGKEIT_BERECHNET`).
- **Skonto** steht im Sidecar in den Zahlungsbedingungen (`#SKONTO#TAGE=…#PROZENT=…#`).
- **`tags.pruefen` ist optional.** Ohne Prüf-Tag zeigt nur der Sidecar den Prüfstatus. Das Feld `pruefstatus` ist wie alle Felder optional.
- **nopaper legt in paperless nichts an.** Tags und Felder müssen vorhanden sein; `nopaper check` nennt fehlende (`TAG_FEHLT`, `FELD_FEHLT`). `nopaper paperless-skript` ist nur ein Angebot und nicht nötig.
- Soll-Vergleich im Testlauf um `due` (Zahlbetrag), `due_date` und `skonto` erweitert.
